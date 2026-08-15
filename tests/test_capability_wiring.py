"""Capabilities on a request reaching the agent that serves it."""

from __future__ import annotations

from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage

from kingfisher.adapters.agent import CapabilityError, build_agent
from kingfisher.adapters.capabilities import ScopedSkills, ToolAllowlist
from kingfisher.domain.capabilities import Capabilities
from tests.conftest import FakeToolCallingModel

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

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003, ARG002
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


def _capture(monkeypatch) -> dict:
    captured: dict = {}

    def spy(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("kingfisher.adapters.agent.create_deep_agent", spy)
    return captured


def test_no_capabilities_means_no_filtering(cfg, monkeypatch):
    """The default has to stay zero-cost, or every existing caller pays for a
    feature it never asked for."""
    captured = _capture(monkeypatch)
    build_agent(cfg, model=FakeToolCallingModel(responses=[AIMessage(content="ok")]))

    names = {type(m).__name__ for m in captured["middleware"]}
    assert "ToolAllowlist" not in names
    assert "ScopedSkills" not in names
    assert "subagents" not in captured


def test_restricting_tools_removes_the_shell_from_what_the_model_sees(cfg):
    """Run for real rather than at the construction seam: the point of the
    allowlist is that the model is never *offered* the tool, and only a live
    model call proves that."""
    model = RecordingModel(responses=[AIMessage(content="ok")])

    agent = build_agent(
        cfg,
        model=model,
        capabilities=Capabilities(tools=("read_file", "write_file")),
    )
    agent.invoke({"messages": [{"role": "user", "content": "go"}]})

    assert set(model.offered) == {"read_file", "write_file"}
    assert "execute" not in model.offered  # builtins are filtered too


def test_an_unrestricted_run_is_offered_the_shell(cfg):
    """The negative control for the test above — otherwise it would pass even
    if the shell were never wired in the first place."""
    model = RecordingModel(responses=[AIMessage(content="ok")])

    build_agent(cfg, model=model).invoke({"messages": [{"role": "user", "content": "go"}]})

    assert "execute" in model.offered


def test_activating_a_skill_scopes_the_index_and_denies_the_rest(cfg, monkeypatch):
    _write_skill(cfg.workspace, "tabular-qa")
    _write_skill(cfg.workspace, "other")

    with_skills = replace(cfg, skills_enabled=True)
    captured = _capture(monkeypatch)
    build_agent(
        with_skills,
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


def test_leaving_skills_unset_keeps_the_stock_middleware(cfg, monkeypatch):
    """Unrestricted is not "restricted to everything": no filter, no deny rules."""
    _write_skill(cfg.workspace, "tabular-qa")
    captured = _capture(monkeypatch)
    build_agent(
        replace(cfg, skills_enabled=True),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
    )

    assert captured["skills"] == ["/skills/"]
    assert not any(isinstance(m, ScopedSkills) for m in captured["middleware"])
    assert len(captured["permissions"]) == 1  # just the /data rule


def test_activating_a_subagent_passes_its_definition_through(cfg, monkeypatch):
    _write_subagent(cfg.workspace)
    captured = _capture(monkeypatch)
    build_agent(
        cfg,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=("reviewer",)),
    )

    (subagent,) = captured["subagents"]
    assert subagent["name"] == "reviewer"
    assert subagent["system_prompt"] == "You review analyses."
    # Unset in the definition, so absent here — deepagents then inherits.
    assert "tools" not in subagent
    assert "model" not in subagent


def test_requesting_no_subagents_is_distinct_from_not_asking(cfg, monkeypatch):
    _write_subagent(cfg.workspace)
    captured = _capture(monkeypatch)
    build_agent(
        cfg,
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
def test_naming_something_the_workspace_lacks_fails_loudly(cfg, caps, message):
    """Rather than running with quietly less than the caller asked for."""
    _write_skill(cfg.workspace, "tabular-qa")
    _write_subagent(cfg.workspace)

    with pytest.raises(CapabilityError, match=message):
        build_agent(
            replace(cfg, skills_enabled=True),
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


def test_an_injected_agent_cannot_honour_capabilities(cfg):
    """It was built elsewhere, so the restrictions were never applied to it.
    Refusing beats running with more access than the caller asked for."""
    from kingfisher.app.run import run
    from kingfisher.domain.request import Request

    prebuilt = build_agent(cfg, model=FakeToolCallingModel(responses=[AIMessage(content="ok")]))

    with pytest.raises(ValueError, match="pre-built agent"):
        run(
            Request(task="go", capabilities=Capabilities(tools=("read_file",))),
            cfg=cfg,
            agent=prebuilt,
        )

    # The unrestricted case still works, so the guard is not just "reject agent=".
    assert run(Request(task="go"), cfg=cfg, agent=prebuilt).answer == "ok"


def test_scoping_skills_builds_against_the_real_backend(cfg):
    """The spy-based tests above never reach deepagents' own validation, which
    is what rejected these deny rules live: FilesystemMiddleware refuses
    `permissions=` unless every rule path is scoped to a backend route."""
    _write_skill(cfg.workspace, "tabular-qa")
    _write_skill(cfg.workspace, "other")

    build_agent(
        replace(cfg, skills_enabled=True),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(skills=("other",)),
    )


def test_a_disallowed_tool_is_refused_even_when_the_model_calls_it_anyway(cfg):
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

    agent = build_agent(
        cfg,
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


def test_a_typo_in_a_tool_name_is_caught(cfg):
    """Without this, `read_fil` silently narrows the allowlist and the agent
    runs crippled -- the same quiet failure skills and subagents refuse."""
    with pytest.raises(CapabilityError, match="unknown tool"):
        build_agent(
            cfg,
            model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
            capabilities=Capabilities(tools=("read_file", "read_fil")),
        )


def test_the_registered_tool_names_are_discoverable(cfg):
    """Pins the introspection the check above depends on. If deepagents or
    LangGraph moves the tool node, this fails loudly here rather than silently
    turning tool validation into a no-op."""
    from kingfisher.adapters.agent import registered_tools

    graph = build_agent(cfg, model=FakeToolCallingModel(responses=[AIMessage(content="ok")]))
    names = registered_tools(graph)

    assert {"read_file", "write_file", "edit_file", "ls", "glob", "grep"} <= set(names)
    assert "execute" in names  # the shell
    assert "task" in names  # subagent delegation


def test_unrecognised_graph_shapes_disable_the_check_rather_than_crashing(cfg):
    from kingfisher.adapters.agent import registered_tools

    assert registered_tools(object()) == ()


RESTRICTED_SUBAGENT = """---
name: reader
description: Reads files and reports what they contain.
tools: [read_file, glob]
---
You read files.
"""


def test_a_subagent_with_restricted_tools_builds_for_real(cfg):
    """Regression: `SubAgent.tools` takes tool *objects* deepagents will
    register, not a selection by name. Passing names raised inside ToolNode.
    The spy-based test below never caught it, and the live run used a subagent
    with no `tools:` field."""
    _write_subagent(cfg.workspace, RESTRICTED_SUBAGENT, "reader.md")

    build_agent(
        cfg,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=("reader",)),
    )


def test_a_subagents_tool_restriction_becomes_an_allowlist(cfg, monkeypatch):
    _write_subagent(cfg.workspace, RESTRICTED_SUBAGENT, "reader.md")
    captured = _capture(monkeypatch)
    build_agent(
        cfg,
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


def test_a_subagents_model_is_built_through_our_provider_table(cfg, monkeypatch):
    """A bare name would go to deepagents' `init_chat_model`, which infers its
    own provider and reads credentials from the environment -- around the
    configured base_url and api_style entirely."""
    _write_subagent(cfg.workspace, MODEL_SUBAGENT, "cheap.md")
    captured = _capture(monkeypatch)
    build_agent(
        cfg,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=("cheap",)),
    )

    (subagent,) = captured["subagents"]
    assert not isinstance(subagent["model"], str)
    assert subagent["model"].model == "some-small-model"
    # Same gateway as the main agent, not whatever the environment suggests.
    assert str(subagent["model"].anthropic_api_url).startswith(cfg.base_url)


def test_role_models_override_a_subagents_declared_model(cfg, monkeypatch):
    """Cost routing is an operator decision, so it must not require editing
    workspace content."""
    _write_subagent(cfg.workspace, MODEL_SUBAGENT, "cheap.md")
    captured = _capture(monkeypatch)
    build_agent(
        replace(cfg, role_models={"cheap": "operator-choice"}),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=("cheap",)),
    )

    (subagent,) = captured["subagents"]
    assert subagent["model"].model == "operator-choice"
