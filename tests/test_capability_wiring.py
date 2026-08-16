"""Capabilities on a request reaching the agent that serves it."""

from __future__ import annotations

from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage

from kingfisher.domain.capabilities import Capabilities
from kingfisher.infrastructure.agent import (
    SKILLS_SOURCES,
    CapabilityError,
    _available_skills,
    build_agent,
)
from kingfisher.infrastructure.backend import build_backend
from kingfisher.infrastructure.scoping import ScopedSkills, ToolAllowlist
from kingfisher.infrastructure.subagent_store import load_all
from tests.conftest import FakeToolCallingModel, capture_build

SUBAGENT = """---
name: reviewer
description: Checks an analysis for arithmetic errors.
---
You review analyses.
"""


class RecordingModel(FakeToolCallingModel):
    """Remembers the tool names it was offered on the last call."""

    #: A pydantic field, not a plain attribute — the base model is a
    #: BaseModel, so an annotation without a default is a *required* field.
    offered: list[str] = []

    def bind_tools(self, tools, **kwargs):
        self.offered = [getattr(t, "name", None) or t.get("name") for t in tools]
        return self


def _write_skill(workspace, name):
    directory = workspace / "skills" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: A skill.\n---\nDo the thing.\n", encoding="utf-8"
    )


def _write_subagent(workspace, text=SUBAGENT, filename="reviewer.md"):
    directory = workspace / "subagents"
    directory.mkdir(exist_ok=True)
    (directory / filename).write_text(text, encoding="utf-8")


def test_no_capabilities_means_no_filtering(cfg, monkeypatch, session_dir):
    """The default has to stay zero-cost, or every existing caller pays for a
    feature it never asked for."""
    captured = capture_build(monkeypatch)
    build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]))

    names = {type(m).__name__ for m in captured["middleware"]}
    assert "ToolAllowlist" not in names
    assert "ScopedSkills" not in names
    assert "subagents" not in captured


def test_restricting_tools_removes_the_shell_from_what_the_model_sees(cfg, session_dir):
    """Run for real rather than at the construction seam: the point of the
    allowlist is that the model is never *offered* the tool, and only a live
    model call proves that."""
    model = RecordingModel(responses=[AIMessage(content="ok")])

    agent = build_agent(cfg, session_dir=session_dir,
        model=model,
        capabilities=Capabilities(tools=("read_file", "write_file")),
    )
    agent.invoke({"messages": [{"role": "user", "content": "go"}]})

    assert set(model.offered) == {"read_file", "write_file"}
    assert "execute" not in model.offered  # builtins are filtered too


def test_an_unrestricted_run_is_offered_the_shell(cfg, session_dir):
    """The negative control for the test above — otherwise it would pass even
    if the shell were never wired in the first place."""
    model = RecordingModel(responses=[AIMessage(content="ok")])

    build_agent(
        cfg,
        session_dir=session_dir,
        model=model).invoke({"messages": [{"role": "user", "content": "go"}]})

    assert "execute" in model.offered


def test_activating_a_skill_scopes_the_index_and_denies_the_rest(cfg, monkeypatch, session_dir):
    _write_skill(cfg.workspace, "tabular-qa")
    _write_skill(cfg.workspace, "other")

    with_skills = replace(cfg, skills_enabled=True)
    captured = capture_build(monkeypatch)
    build_agent(
        with_skills,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(skills=("other",)),
    )

    scoped = [m for m in captured["middleware"] if isinstance(m, ScopedSkills)]
    assert len(scoped) == 1
    # Passing `skills=` would make deepagents build its own unfiltered
    # SkillsMiddleware alongside ours; ours has to be the only one.
    assert "skills" not in captured

    denied = [
        r for r in captured["permissions"] if r.mode == "deny" and "read" in r.operations
    ]
    assert [r.paths for r in denied] == [["/skills/tabular-qa/**"]]


def test_leaving_skills_unset_keeps_the_stock_middleware(cfg, monkeypatch, session_dir):
    """Unrestricted is not "restricted to everything": no filter, no deny rules."""
    _write_skill(cfg.workspace, "tabular-qa")
    captured = capture_build(monkeypatch)
    build_agent(
        replace(cfg, skills_enabled=True),
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
    )

    assert captured["skills"] == SKILLS_SOURCES
    assert not any(isinstance(m, ScopedSkills) for m in captured["middleware"])
    assert len(captured["permissions"]) == 1  # just the /data rule


def test_activating_a_subagent_passes_its_definition_through(cfg, monkeypatch, session_dir):
    _write_subagent(cfg.workspace)
    captured = capture_build(monkeypatch)
    build_agent(cfg, session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=("reviewer",)),
    )

    (subagent,) = captured["subagents"]
    assert subagent["name"] == "reviewer"
    assert subagent["system_prompt"] == "You review analyses."
    # Unset in the definition, so absent here — deepagents then inherits.
    assert "tools" not in subagent
    assert "model" not in subagent


def test_requesting_no_subagents_is_distinct_from_not_asking(cfg, monkeypatch, session_dir):
    _write_subagent(cfg.workspace)
    captured = capture_build(monkeypatch)
    build_agent(cfg, session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=()),
    )

    assert captured["subagents"] == []


@pytest.mark.parametrize(
    ("caps", "message"),
    [
        (Capabilities(skills=("ghost",)), "unknown skill"),
        (Capabilities(subagents=("ghost",)), "unknown subagent"),
    ],
)
def test_naming_something_the_workspace_lacks_fails_loudly(cfg, caps, message, session_dir):
    """Rather than running with quietly less than the caller asked for."""
    _write_skill(cfg.workspace, "tabular-qa")
    _write_subagent(cfg.workspace)

    with pytest.raises(CapabilityError, match=message):
        build_agent(
            replace(cfg, skills_enabled=True),
            session_dir=session_dir,
            model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
            capabilities=caps,
        )


def test_an_unnamed_tool_survives_the_allowlist():
    """The allowlist governs kingfisher's named surface; dropping something it
    cannot identify would be the worse failure."""

    unnamed = {"type": "web_search_20250305"}

    class Request:
        tools = [unnamed]

        def override(self, **kwargs):
            self.tools = kwargs["tools"]
            return self

    assert ToolAllowlist(("read_file",))._filter(Request()).tools == [unnamed]


def test_an_injected_agent_cannot_honour_capabilities(cfg, session_dir):
    """It was built elsewhere, so the restrictions were never applied to it.
    Refusing beats running with more access than the caller asked for."""
    from kingfisher.application.run import run
    from kingfisher.domain.request import Request

    prebuilt = build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]))

    with pytest.raises(ValueError, match="pre-built agent"):
        run(
            Request(task="go", capabilities=Capabilities(tools=("read_file",))),
            cfg=cfg,
            agent=prebuilt,
        )

    # The unrestricted case still works, so the guard is not just "reject agent=".
    assert run(Request(task="go"), cfg=cfg, agent=prebuilt).answer == "ok"



def test_a_disallowed_tool_is_refused_even_when_the_model_calls_it_anyway(cfg, session_dir):
    """The filter is not the boundary. A live run showed MiniMax-M3 calling
    `execute` from memory after it was filtered out of the offered tools, and
    ToolNode running it, because the tool is still registered there."""
    responses = [
        AIMessage(
            content="",
            tool_calls=[{"name": "execute", "args": {"command": "echo pwned"}, "id": "c1"}],
        ),
        AIMessage(content="done"),
    ]

    agent = build_agent(cfg, session_dir=session_dir,
        model=FakeToolCallingModel(responses=responses),
        capabilities=Capabilities(tools=("read_file", "write_file")),
    )
    out = agent.invoke(
        {"messages": [{"role": "user", "content": "go"}]}, config={"recursion_limit": 12}
    )

    transcript = "\n".join(str(getattr(m, "content", "")) for m in out["messages"])
    assert "pwned" not in transcript  # the shell never ran
    assert "execute is not available for this request" in transcript
    # Refused, not raised: the agent gets to carry on and choose another route.
    assert out["messages"][-1].content == "done"


def test_a_typo_in_a_tool_name_is_caught(cfg, session_dir):
    """Without this, `read_fil` silently narrows the allowlist and the agent
    runs crippled -- the same quiet failure skills and subagents refuse."""
    with pytest.raises(CapabilityError, match="unknown tool"):
        build_agent(cfg, session_dir=session_dir,
            model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
            capabilities=Capabilities(tools=("read_file", "read_fil")),
        )


def test_the_registered_tool_names_are_discoverable(cfg, session_dir):
    """Pins the introspection the check above depends on. If deepagents or
    LangGraph moves the tool node, this fails loudly here rather than silently
    turning tool validation into a no-op."""
    from kingfisher.infrastructure.agent import registered_tools

    graph = build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]))
    names = registered_tools(graph)

    assert {"read_file", "write_file", "edit_file", "ls", "glob", "grep"} <= set(names)
    assert "execute" in names  # the shell
    assert "task" in names  # subagent delegation


def test_unrecognised_graph_shapes_disable_the_check_rather_than_crashing(cfg):
    from kingfisher.infrastructure.agent import registered_tools

    assert registered_tools(object()) == ()


RESTRICTED_SUBAGENT = """---
name: reader
description: Reads files and reports what they contain.
tools: [read_file, glob]
---
You read files.
"""


def test_a_subagent_with_restricted_tools_builds_for_real(cfg, session_dir):
    """Regression: `SubAgent.tools` takes tool *objects* deepagents will
    register, not a selection by name. Passing names raised inside ToolNode.
    The spy-based test below never caught it, and the live run used a subagent
    with no `tools:` field."""
    _write_subagent(cfg.workspace, RESTRICTED_SUBAGENT, "reader.md")

    build_agent(cfg, session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=("reader",)),
    )


def test_a_subagents_tool_restriction_becomes_an_allowlist(cfg, monkeypatch, session_dir):
    _write_subagent(cfg.workspace, RESTRICTED_SUBAGENT, "reader.md")
    captured = capture_build(monkeypatch)
    build_agent(cfg, session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=("reader",)),
    )

    (subagent,) = captured["subagents"]
    assert "tools" not in subagent  # names here would raise inside ToolNode
    (allowlist,) = subagent["middleware"]
    assert isinstance(allowlist, ToolAllowlist)
    assert allowlist._allowed == {"read_file", "glob"}


MODEL_SUBAGENT = """---
name: cheap
description: Does the bulk reading on a smaller model.
model: some-small-model
---
You read things.
"""


def test_a_subagents_model_is_built_through_our_provider_table(cfg, monkeypatch, session_dir):
    """A bare name would go to deepagents' `init_chat_model`, which infers its
    own provider and reads credentials from the environment -- around the
    configured base_url and api_style entirely."""
    _write_subagent(cfg.workspace, MODEL_SUBAGENT, "cheap.md")
    captured = capture_build(monkeypatch)
    build_agent(cfg, session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=("cheap",)),
    )

    (subagent,) = captured["subagents"]
    assert not isinstance(subagent["model"], str)
    assert subagent["model"].model == "some-small-model"
    # Same gateway as the main agent, not whatever the environment suggests.
    assert str(subagent["model"].anthropic_api_url).startswith(cfg.base_url)


def test_role_models_override_a_subagents_declared_model(cfg, monkeypatch, session_dir):
    """Cost routing is an operator decision, so it must not require editing
    workspace content.

    Keyed by *role*. This test used to pass `{"cheap": ...}` -- the subagent's
    name -- and passed, while the feature fired for nothing: `from_env` only
    ever populates `main`, `subagent` and `summarizer`, so a name-keyed entry
    could not exist outside a test that built one by hand. The test validated a
    path production cannot reach, which is why the defect survived it.
    """
    _write_subagent(cfg.workspace, MODEL_SUBAGENT, "cheap.md")
    captured = capture_build(monkeypatch)
    build_agent(
        replace(cfg, role_models={"subagent": "operator-choice"}),
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=("cheap",)),
    )

    (subagent,) = captured["subagents"]
    assert subagent["model"].model == "operator-choice"


def test_narrowing_can_only_subtract_from_what_the_deployment_wired(cfg, monkeypatch, session_dir):
    """The rule that makes two axes safe rather than confusing: `Config` says
    what is wired and shapes the cached prompt; a request narrows within it.
    Asking for memory a deployment never wired does not conjure it."""
    captured = capture_build(monkeypatch)
    build_agent(cfg, session_dir=session_dir,  # memory_enabled is False
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(memory=True),
    )
    assert "memory" not in captured


def test_declining_memory_drops_the_mount_and_denies_the_file(cfg, monkeypatch, session_dir):
    """The prompt still describes memory -- it is the cached prefix and must
    not vary per request -- so a deny rule is what actually stops the read."""
    captured = capture_build(monkeypatch)
    build_agent(
        replace(cfg, memory_enabled=True),
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(memory=False),
    )

    assert "memory" not in captured
    denied = [r for r in captured["permissions"] if r.paths == ["/memory/**"]]
    assert len(denied) == 1
    assert denied[0].mode == "deny"


def test_memory_is_mounted_when_wired_and_not_declined(cfg, monkeypatch, session_dir):
    """The negative control: without it the two tests above would pass even if
    memory were never wired at all."""
    captured = capture_build(monkeypatch)
    build_agent(
        replace(cfg, memory_enabled=True),
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
    )
    assert captured["memory"] == ["/memory/AGENTS.md"]
    assert not any(r.paths == ["/memory/**"] for r in captured["permissions"])


def test_the_catalogue_can_live_outside_the_workspace(cfg, session_dir, tmp_path):
    """One reviewed set of definitions, deployed once, serving every workspace.

    That is what making these roots configurable buys: a copy per workspace is
    a copy nobody can audit centrally.
    """
    catalogue = tmp_path / "catalogue" / "skills"
    (catalogue / "shared").mkdir(parents=True)
    (catalogue / "shared" / "SKILL.md").write_text(
        "---\nname: shared\ndescription: A procedure every deployment gets.\n---\nBody.\n",
        encoding="utf-8",
    )
    relocated = replace(cfg, skills_root=catalogue, skills_enabled=True)

    backend = build_backend(relocated, session_dir)

    assert str(backend.routes["/skills/"].cwd) == str(catalogue.resolve())
    assert backend.read("/skills/shared/SKILL.md").error is None
    # Offered to the agent without any copy existing in the workspace.
    assert _available_skills(relocated, None) == ("shared",)
    assert not (relocated.workspace / "skills" / "shared").exists()


def test_subagents_relocate_independently_of_skills(cfg, tmp_path):
    """A deployment may share one catalogue of procedures while keeping its own
    delegates, or the reverse -- so they are two roots, not one."""
    catalogue = tmp_path / "catalogue" / "subagents"
    catalogue.mkdir(parents=True)
    (catalogue / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Checks arithmetic.\n---\nYou review.\n",
        encoding="utf-8",
    )
    relocated = replace(cfg, subagents_root=catalogue)

    assert set(load_all(relocated.subagents_dir)) == {"reviewer"}
    assert load_all(relocated.workspace / "subagents") == {}


def test_a_definition_chooses_when_no_operator_says_otherwise(cfg, session_dir, monkeypatch):
    """The override wins, but only when there is one."""
    (cfg.workspace / "subagents").mkdir(parents=True, exist_ok=True)
    (cfg.workspace / "subagents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: d\nmodel: FROM-DEFINITION\n---\nYou review.\n",
        encoding="utf-8",
    )
    captured = capture_build(monkeypatch)

    build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=("reviewer",)),
    )

    (spec,) = [s for s in captured["subagents"] if s["name"] == "reviewer"]
    assert spec["model"].model == "FROM-DEFINITION"
