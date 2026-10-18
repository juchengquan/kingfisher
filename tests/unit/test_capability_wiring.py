"""Capabilities on a request reaching the agent that serves it."""

from __future__ import annotations

import asyncio
import operator
from dataclasses import replace
from typing import Annotated, Any

import pytest
from deepagents.middleware.memory import MEMORY_SYSTEM_PROMPT
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.graph import END, START, StateGraph
from pydantic import Field
from typing_extensions import TypedDict

from kingfisher.core.access.capabilities import Capabilities
from kingfisher.core.agent.activation import available_skills
from kingfisher.core.agent.assembly import (
    CapabilityError,
    build_agent,
)
from kingfisher.core.agent.backend import skills_sources
from kingfisher.core.agent.middlewares.narrowing import (
    GrantedOnlyMiddleware,
    NarrowedSkillsMiddleware,
)
from kingfisher.core.agent.prompting import memory_guidelines
from kingfisher.core.layout import MEMORY
from kingfisher.plugins import LocalSessions
from kingfisher.plugins.sessions.backend import backend_at
from tests.conftest import (
    FakeToolCallingModel,
    declared_subagents,
    dispatched,
    subagent_of,
)

SUBAGENT = """name: reviewer
description: Checks an analysis for arithmetic errors.
system_prompt: |
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


class ToldModel(RecordingModel):
    """The same, and the system message of every call: what an agent is told about the
    tools it holds is composed on the way to the model, so no construction seam shows
    it."""

    heard: list[str] = []

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.heard.extend(m.text for m in messages if isinstance(m, SystemMessage))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def _write_skill(definitions, name):
    directory = definitions / "skills" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: A skill.\n---\nDo the thing.\n", encoding="utf-8"
    )


def _write_subagent(definitions, text=SUBAGENT, filename="reviewer.yaml"):
    directory = definitions / "subagents"
    directory.mkdir(exist_ok=True)
    (directory / filename).write_text(text, encoding="utf-8")


def test_no_capabilities_means_no_filtering(cfg, session_dir):
    """The default narrows nothing, which is what it has always meant."""
    built = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]))

    (granted,) = [m for m in built.middleware if isinstance(m, GrantedOnlyMiddleware)]
    assert granted._allowed is None
    assert "NarrowedSkillsMiddleware" not in {type(m).__name__ for m in built.middleware}
    assert not declared_subagents(built)


def test_restricting_tools_removes_the_shell_from_what_the_model_sees(cfg, session_dir):
    """Run for real rather than at the construction seam: the point of the allowlist is
    that the model is never *offered* the tool, and only a live model call proves
    that.
    """
    model = RecordingModel(responses=[AIMessage(content="ok")])

    agent = build_agent(cfg, backend=backend_at(cfg, session_dir),
        model=model,
        capabilities=Capabilities(builtin_tools=("read_file", "write_file")),
    ).graph
    agent.invoke({"messages": [{"role": "user", "content": "go"}]})

    assert set(model.offered) == {"read_file", "write_file"}
    assert "execute" not in model.offered  # builtins are filtered too


def test_a_turn_on_an_event_loop_is_offered_and_told_of_only_the_tools_it_was_granted(
    cfg, session_dir
):
    """`Kingfisher.arun` and `astream` drive `awrap_model_call` alone, so a filter that
    narrowed only on the sync hook would offer an async turn every built-in -- and what
    the model is told of is derived from the tools on the request, so it would be told
    about them too, and a model told of a tool calls it and is refused.

    Measured, by narrowing only the async hook: the turn below was offered all ten
    built-in tools and handed deepagents' line "The `execute` tool runs commands in the
    host shell", while the sync twin above stayed green and the whole suite with it.
    """
    model = ToldModel(responses=[AIMessage(content="ok")])

    built = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=model,
        capabilities=Capabilities(builtin_tools=("read_file", "write_file")),
    )
    asyncio.run(
        built.graph.ainvoke(
            {"messages": [{"role": "user", "content": "go"}]}, context=built.context
        )
    )

    assert set(model.offered) == {"read_file", "write_file"}
    # The name, not the `## The shell` heading: that section is composed from the
    # request's capabilities, so it is absent on this path whether the filter runs or
    # not -- asserting on it is a check that cannot fail. The line that arrives with an
    # unheld tool is deepagents' own, and it names `execute`.
    (system,) = model.heard
    assert "execute" not in system


def test_an_unrestricted_run_is_offered_the_shell(cfg, session_dir):
    """The negative control for the test above — otherwise it would pass even if the
    shell were never wired in the first place.
    """
    model = RecordingModel(responses=[AIMessage(content="ok")])

    build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=model).graph.invoke({"messages": [{"role": "user", "content": "go"}]})

    assert "execute" in model.offered


def test_activating_a_skill_scopes_the_index_and_denies_the_rest(cfg, session_dir):
    _write_skill(cfg.definitions, "tabular-qa")
    _write_skill(cfg.definitions, "other")

    with_skills = replace(cfg, skills_enabled=True)
    built = build_agent(
        with_skills,
        backend=backend_at(with_skills, session_dir),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(skills=("other",)),
    )

    scoped = [m for m in built.middleware if isinstance(m, NarrowedSkillsMiddleware)]
    assert len(scoped) == 1
    # Passing `skills=` would make deepagents build its own unfiltered
    # SkillsMiddleware alongside ours; ours has to be the only one.
    assert built.skills is None

    # `.harness` is denied reads unconditionally, by the layout rather than by
    # this request, so what a *granted skill list* adds is what is left over.
    denied = [
        r
        for r in built.permissions
        if r.mode == "deny" and "read" in r.operations and r.paths != ["/.harness/**"]
    ]
    assert [r.paths for r in denied] == [["/skills/tabular-qa/**"]]

    # What the model is actually shown. This went unasserted, and the whole
    # point of the middleware is what it renders -- a version of it that loaded
    # every skill and then filtered them all away passed everything above.
    loaded = scoped[0].before_agent({}, None, {})["skills_metadata"]
    shown = scoped[0]._format_skills_list(loaded)
    assert "other" in shown, shown
    assert "tabular-qa" not in shown, shown


def test_leaving_skills_unset_keeps_the_stock_middleware(cfg, session_dir):
    """Unrestricted is not "restricted to everything": no filter, no deny rules."""
    _write_skill(cfg.definitions, "tabular-qa")
    wired = replace(cfg, skills_enabled=True)
    built = build_agent(
        wired,
        backend=backend_at(wired, session_dir),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
    )

    assert built.skills == skills_sources()
    assert not any(isinstance(m, NarrowedSkillsMiddleware) for m in built.middleware)
    # The unconditional routes and nothing skill-specific: a request that
    # granted no skills adds no per-skill denials.
    assert {r.paths[0] for r in built.permissions} == {
        "/.harness/**",
        "/inputs/**",
        "/scratchpad/large_tool_results/**",
        "/scratchpad/tool_results/**",
        "/skills/**",
    }


def test_activating_a_subagent_passes_its_definition_through(cfg, session_dir):
    _write_subagent(cfg.definitions)
    built = build_agent(cfg, backend=backend_at(cfg, session_dir),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=("reviewer",)),
    )

    (subagent,) = declared_subagents(built)
    assert subagent["name"] == "reviewer"
    assert subagent["system_prompt"] == "You review analyses."
    # Set though empty: absent, deepagents would hand the subagent the agent's own tools.
    assert subagent["tools"] == []
    assert "model" not in subagent


def test_requesting_no_subagents_is_distinct_from_not_asking(cfg, session_dir):
    """`()` activates none where the default `ALL` activates every one the workspace
    offers -- so the reviewer written above is absent here.
    """
    _write_subagent(cfg.definitions)
    built = build_agent(cfg, backend=backend_at(cfg, session_dir),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=()),
    )

    assert declared_subagents(built) == []


def test_naming_something_the_workspace_lacks_fails_loudly(cfg, session_dir):
    """Rather than running with quietly less than the caller asked for."""
    _write_skill(cfg.definitions, "tabular-qa")
    _write_subagent(cfg.definitions)

    for caps, message in (
        (Capabilities(skills=("ghost",)), "unknown skill"),
        (Capabilities(subagents=("ghost",)), "unknown subagent"),
    ):
        with pytest.raises(CapabilityError, match=message):
            wired = replace(cfg, skills_enabled=True)
            build_agent(
                wired,
                backend=backend_at(wired, session_dir),
                model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
                capabilities=caps,
            )


def test_an_unnamed_tool_survives_the_allowlist():
    """The allowlist governs kingfisher's named surface; dropping something it cannot
    identify would be the worse failure.
    """

    unnamed = {"type": "web_search_20250305"}

    class Request:
        tools = [unnamed]

        def override(self, **kwargs):
            self.tools = kwargs["tools"]
            return self

    assert GrantedOnlyMiddleware(("read_file",))._filter(Request()).tools == [unnamed]


class _Call:
    """The shape `_refuse` reads: one tool call."""

    def __init__(self, name: str, **args: str) -> None:
        self.tool_call = {"name": name, "args": args, "id": "c1"}


def test_a_grant_of_nothing_in_particular_refuses_nothing():
    """`None` read as an empty grant, so an unrestricted request, which still carries
    the check on every graph, could call nothing."""
    granted = GrantedOnlyMiddleware()

    assert granted._refuse(_Call("execute")) is None
    assert granted._refuse(_Call("task", subagent_type="anyone")) is None


def test_a_task_outside_both_grants_is_refused_for_the_tool():
    """Told which subagents it may use when it may not call `task` at all, a model
    goes on trying them."""
    granted = GrantedOnlyMiddleware(("read_file",), subagents=("helper",))

    refusal = granted._refuse(_Call("task", subagent_type="helper"))

    assert refusal is not None
    assert "task is not available" in refusal.content


def test_an_injected_graph_cannot_honour_capabilities(cfg, session_dir):
    """It was built elsewhere, so the restrictions were never applied to it."""
    from kingfisher.core.request import Request
    from kingfisher.core.run import run

    # The graph itself: `graph=` is the injected-graph seam, and what a deployment
    # supplies there is a compiled graph rather than kingfisher's own record.
    prebuilt = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")])).graph

    with pytest.raises(ValueError, match="pre-built graph"):
        run(
            Request(task="go", capabilities=Capabilities(builtin_tools=("read_file",))),
            cfg=cfg,
            graph=prebuilt,
            sessions=LocalSessions(),
        )

    # The unrestricted case still works, so the guard is not just "reject graph=".
    assert run(
        Request(task="go"), cfg=cfg, graph=prebuilt, sessions=LocalSessions()
    ).answer == "ok"


def test_a_disallowed_tool_is_refused_even_when_the_model_calls_it_anyway(cfg, session_dir):
    """The filter is not the boundary."""
    responses = [
        AIMessage(
            content="",
            tool_calls=[{"name": "execute", "args": {"command": "echo pwned"}, "id": "c1"}],
        ),
        AIMessage(content="done"),
    ]

    agent = build_agent(cfg, backend=backend_at(cfg, session_dir),
        model=FakeToolCallingModel(responses=responses),
        capabilities=Capabilities(builtin_tools=("read_file", "write_file")),
    ).graph
    out = agent.invoke(
        {"messages": [{"role": "user", "content": "go"}]}, config={"recursion_limit": 12}
    )

    transcript = "\n".join(str(getattr(m, "content", "")) for m in out["messages"])
    assert "pwned" not in transcript  # the shell never ran
    assert "execute is not available for this request" in transcript
    # Refused, not raised: the agent gets to carry on and choose another route.
    assert out["messages"][-1].content == "done"


def test_a_typo_in_a_tool_name_is_caught(cfg, session_dir):
    """Without this, `read_fil` silently narrows the allowlist and the agent runs
    crippled -- the same quiet failure skills and subagents refuse.
    """
    with pytest.raises(CapabilityError, match="unknown tool"):
        build_agent(cfg, backend=backend_at(cfg, session_dir),
            model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
            capabilities=Capabilities(builtin_tools=("read_file",), tools=("read_fil",)),
        )


def test_the_registered_tool_names_are_discoverable(cfg, session_dir):
    """Pins the introspection the check above depends on."""

    graph = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")])).graph
    names = dispatched(graph)

    assert {"read_file", "write_file", "edit_file", "ls", "glob", "grep"} <= set(names)
    assert "execute" in names  # the shell
    assert "task" in names  # subagent delegation


def test_unrecognised_graph_shapes_disable_the_check_rather_than_crashing(cfg):
    """Still no crash, and now it says which of the two answers it is giving."""
    from kingfisher.core.agent.kinds.tools import registered_tools

    assert registered_tools(object()) is None


RESTRICTED_SUBAGENT = """name: reader
description: Reads files and reports what they contain.
builtin_tools: [read_file, glob]
system_prompt: |
  You read files.

"""


def test_a_subagent_with_restricted_tools_builds_for_real(cfg, session_dir):
    """Regression: `SubAgent.tools` takes tool *objects* deepagents will register, not a
    selection by name.
    """
    _write_subagent(cfg.definitions, RESTRICTED_SUBAGENT, "reader.yaml")

    build_agent(cfg, backend=backend_at(cfg, session_dir),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=("reader",)),
    )


def test_a_subagents_tool_restriction_becomes_an_allowlist(cfg, session_dir):
    _write_subagent(cfg.definitions, RESTRICTED_SUBAGENT, "reader.yaml")
    built = build_agent(cfg, backend=backend_at(cfg, session_dir),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=("reader",)),
    )

    (subagent,) = declared_subagents(built)
    # Its built-ins arrive by middleware; a name here would raise inside ToolNode.
    assert subagent["tools"] == []
    # By type rather than by position. A subagent carries guards it did not ask
    # for -- the host-path correction, and the workspace tools' own failures --
    # and this test is about the allowlist its definition produced.
    (allowlist,) = [m for m in subagent["middleware"] if isinstance(m, GrantedOnlyMiddleware)]
    assert allowlist._allowed == {"read_file", "glob"}


MODEL_SUBAGENT = """name: cheap
description: Does the bulk reading on a smaller model.
model: cheap-model
system_prompt: |
  You read things.

"""


def test_a_subagents_model_is_built_through_our_provider_table(cfg, monkeypatch, session_dir):
    """A bare name would go to deepagents' `init_chat_model`, which infers its own
    provider and reads credentials from the environment -- around the configured
    endpoint entirely.
    """
    _write_subagent(cfg.definitions, MODEL_SUBAGENT, "cheap.yaml")
    built = build_agent(cfg, backend=backend_at(cfg, session_dir),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=("cheap",)),
    )

    (subagent,) = declared_subagents(built)
    assert not isinstance(subagent["model"], str)
    assert subagent["model"].model == "cheap-model"
    # Same gateway as the main agent, not whatever the environment suggests.
    assert str(subagent["model"].anthropic_api_url).startswith(
        cfg.models.resolve()[1].base_url
    )


def test_the_environment_cannot_reroute_a_subagent(cfg, monkeypatch, session_dir):
    """The definition is the only author of where a subagent runs."""
    _write_subagent(cfg.definitions, MODEL_SUBAGENT, "cheap.yaml")
    monkeypatch.setenv("KINGFISHER_MODEL_SUBAGENT", "operator-choice")
    monkeypatch.setenv("KINGFISHER_PROVIDER_SUBAGENT", "openai")
    built = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=("cheap",)),
    )

    (subagent,) = declared_subagents(built)
    assert subagent["model"].model == "cheap-model"


def test_narrowing_can_only_subtract_from_what_the_deployment_wired(cfg, session_dir):
    """The rule that makes two axes safe rather than confusing: `Config` says what is
    wired and shapes the cached prompt; a request narrows within it.
    """
    built = build_agent(cfg, backend=backend_at(cfg, session_dir),  # memory_enabled is False
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(memory=True),
    )
    assert built.memory is None


def test_declining_memory_drops_the_mount_and_denies_the_file(cfg, session_dir):
    """The prompt still describes memory -- it is the cached prefix and must not vary
    per request -- so a deny rule is what actually stops the read.
    """
    wired = replace(cfg, memory_enabled=True)
    built = build_agent(
        wired,
        backend=backend_at(wired, session_dir),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(memory=False),
    )

    assert built.memory is None
    denied = [r for r in built.permissions if r.paths == ["/memory/**"]]
    assert len(denied) == 1
    assert denied[0].mode == "deny"


def test_memory_is_mounted_when_wired_and_not_declined(cfg, session_dir):
    """The negative control: without it the two tests above would pass even if memory
    were never wired at all.
    """
    wired = replace(cfg, memory_enabled=True)
    built = build_agent(
        wired,
        backend=backend_at(wired, session_dir),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
    )
    assert built.memory == ["/memory/AGENTS.md"]
    assert not any(r.paths == ["/memory/**"] for r in built.permissions)


class SentToAnthropic(ChatAnthropic):
    """Keeps the system message it is sent. A `ChatAnthropic` because both prompt-cache
    breakpoints are placed only for one: a fake model would be sent neither.
    """

    seen: list[Any] = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(messages[0])
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])


def _system_blocks_sent(cfg, session_dir) -> list[dict[str, Any]]:
    """What one turn with memory on sends the model as its system message."""
    wired = replace(cfg, memory_enabled=True)
    (session_dir / MEMORY / "AGENTS.md").write_text(
        "# Project memory\n\n- The caller is Ada.\n", encoding="utf-8"
    )
    model = SentToAnthropic(model="claude-sonnet-4-5", api_key="not-a-real-key")
    built = build_agent(wired, backend=backend_at(wired, session_dir), model=model)

    built.graph.invoke({"messages": [{"role": "user", "content": "hi"}]}, context=built.context)

    return model.seen[0].content


def test_the_memory_guidelines_promise_nothing_past_the_session(cfg, session_dir):
    """deepagents' own guidelines reaching the model -- by an upgrade, or a revert to
    `memory=` alone -- tell it memory lasts into "future conversations", and `/memory`
    is deleted with its session.
    """
    sent = "".join(block["text"] for block in _system_blocks_sent(cfg, session_dir))
    ours = set(memory_guidelines().splitlines())
    # Read off the library rather than listed: whatever deepagents says that
    # kingfisher's copy does not is what must not arrive, in whatever words.
    theirs_only = [
        line.strip()
        for line in MEMORY_SYSTEM_PROMPT.splitlines()
        if line.strip() and line not in ours
    ]

    assert "The caller is Ada." in sent, "no memory block was sent, so the rest is vacuous"
    assert theirs_only, "deepagents says nothing kingfisher's copy leaves out"
    assert [line for line in theirs_only if line in sent] == []


def test_the_memory_block_keeps_a_cache_breakpoint_of_its_own(cfg, session_dir):
    """Kingfisher's `MemoryMiddleware` passed without `memory=` lands ahead of the static
    prompt's breakpoint, so every edit to the memory file costs the whole prompt its cache.
    """
    blocks = _system_blocks_sent(cfg, session_dir)

    assert "The caller is Ada." in blocks[-1]["text"]
    assert "cache_control" in blocks[-1]
    assert any("cache_control" in block for block in blocks[:-1])


def test_skills_are_read_from_the_definitions_folder_and_never_the_workspace(cfg, session_dir):
    """Catches the skills catalogue being read from anywhere but the definitions folder,
    a `skills/` left in the workspace included."""
    _write_skill(cfg.definitions, "shared")
    _write_skill(cfg.workspace, "planted")
    wired = replace(cfg, skills_enabled=True)

    backend = backend_at(wired, session_dir)

    assert str(backend.routes["/skills/"].cwd) == str((cfg.definitions / "skills").resolve())
    assert backend.read("/skills/shared/SKILL.md").error is None
    assert available_skills(wired) == ("shared",)


def test_a_definition_chooses_when_no_operator_says_otherwise(cfg, session_dir):
    """The override wins, but only when there is one."""
    (cfg.definitions / "subagents").mkdir(parents=True, exist_ok=True)
    (cfg.definitions / "subagents" / "reviewer.yaml").write_text(
        "name: reviewer\ndescription: d\nmodel: cheap-model\nsystem_prompt: |\n  You review.\n",
        encoding="utf-8",
    )
    built = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=("reviewer",)),
    )

    assert subagent_of(built, "reviewer")["model"].model == "cheap-model"


# -- none, versus could not tell -------------------------------------------


class _CustomState(TypedDict):
    """At module scope on purpose."""

    messages: Annotated[list, operator.add]


def _hand_written_graph():
    """A graph kingfisher did not build: no model node, no tool node."""
    # ty reads langgraph's `StateT` bound as unsatisfied by a
    # `typing_extensions.TypedDict` under `from __future__ import annotations`.
    # The graph compiles and runs; the limitation is in the checker's model of
    # the bound, and narrowing the suppression to these two lines keeps it from
    # covering anything else in the file.
    builder = StateGraph(_CustomState)  # ty: ignore[invalid-argument-type]

    def work(_state: _CustomState) -> dict[str, Any]:
        return {"messages": []}

    builder.add_node("work", work)  # ty: ignore[invalid-argument-type]
    builder.add_edge(START, "work")
    builder.add_edge("work", END)
    return builder.compile()


def test_an_agent_with_no_tools_says_none_rather_than_unknown(fake_model):
    """Measured, because the obvious reading is wrong: `create_agent(tools=[])` compiles
    to `['__start__', 'model']` with **no tool node at all**, which is exactly the
    shape of a graph that dispatches nothing for a different reason.
    """
    from langchain.agents import create_agent

    from kingfisher.core.agent.kinds.tools import registered_tools

    assert registered_tools(create_agent(fake_model, tools=[])) == ()


def test_a_graph_we_did_not_build_says_it_could_not_tell(fake_model):
    from kingfisher.core.agent.kinds.tools import registered_tools

    assert registered_tools(_hand_written_graph()) is None


def test_a_real_build_is_readable(cfg, session_dir):
    """The pin."""
    from kingfisher.core.agent.assembly import build_agent
    from kingfisher.core.agent.kinds.tools import registered_tools

    built = build_agent(cfg, backend=backend_at(cfg, session_dir), model=None)
    names = registered_tools(built.graph)

    assert names is not None, "a graph we built must be readable"
    assert names, "and must dispatch something"


def test_a_listing_says_unknown_rather_than_none_when_it_cannot_read(monkeypatch, cfg):
    """The reason any of this changed."""
    from kingfisher.core import inventory as inventory_module
    from kingfisher.core.agent import assembly as agent_module

    # Patched where it is *called* rather than where it is defined: the probe is
    # `assembly.builtin_tool_names` now, and it took the name into its own namespace.
    monkeypatch.setattr(agent_module, "registered_tools", lambda _graph: None)

    found = inventory_module.inventory(cfg)

    assert found.builtin_tools == ()
    assert found.tools_error is not None
    assert "unknown" in found.tools_error


BARE_TOOL = '''
def shout(text: str) -> str:
    """Return the text in capitals. Use when asked to shout."""
    return text.upper()


TOOLS = [shout]
'''


def test_a_plain_function_is_withheld_when_the_grant_withholds_it(cfg, session_dir):
    """The grant is the security boundary, and it matches on a name.

    `GrantedOnlyMiddleware` reads `.name` and deliberately *keeps* anything it cannot identify,
    and a plain function has `__name__` and no `.name` -- so whether a withheld one is
    really withheld rests on deepagents wrapping it before the allowlist ever sees it.
    """
    from tests.conftest import tools_dir

    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "shout.py").write_text(BARE_TOOL, encoding="utf-8")

    model = RecordingModel(responses=[AIMessage(content="ok")])
    graph = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=model,
        capabilities=Capabilities(tools=None),  # every built-in, none of ours
    ).graph
    graph.invoke(
        {"messages": [{"role": "user", "content": "go"}]}, config={"recursion_limit": 4}
    )

    assert "read_file" in model.offered, "the built-ins should be untouched"
    assert "shout" not in model.offered


# -- the deployment's own ceiling, at the turn that has to respect it ---------

A_TOOL = '''
from langchain_core.tools import tool


@tool
def {name}(x: str) -> str:
    """Do {name}."""
    return x


TOOLS = [{name}]
'''


def _two_tools(cfg) -> None:
    """One the deployment will grant and one it will not."""
    from tests.conftest import tools_dir

    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    for name in ("granted", "withheld"):
        (tools_dir(cfg) / f"{name}.py").write_text(A_TOOL.format(name=name), encoding="utf-8")


def _handed_to_the_build(monkeypatch) -> list[Capabilities]:
    """What each build a turn makes is told it may use.

    Read at the seam the turn crosses, rather than by calling the builder with
    capabilities the test worked out for itself -- which is the one thing this cannot
    do and still be checking anything. The intersection is what is under test; a test
    that computes it and compares agrees with itself.
    """
    from kingfisher.core import service as service_module

    handed: list[Capabilities] = []
    real = service_module.build_agent

    def recording(*args, **kwargs):
        handed.append(kwargs["capabilities"])
        return real(*args, **kwargs)

    monkeypatch.setattr(service_module, "build_agent", recording)
    return handed


def test_a_turn_is_built_inside_the_deployments_ceiling(cfg, monkeypatch):
    """The escalation nothing watched: a caller asking for a tool the deployment never
    granted, and a build that was handed the request's own capabilities instead of the
    grants narrowed by them.

    Measured by making setup pass `request.capabilities` straight through: the
    withheld tool reached the graph and the whole suite stayed green, because every
    other fixture here leaves the deployment unrestricted, where the two are equal.
    """
    from kingfisher.core.request import Request
    from kingfisher.core.service import Kingfisher
    from kingfisher.plugins import LocalSessions
    from tests.conftest import an_agent

    an_agent(cfg, "only")
    _two_tools(cfg)
    kf = Kingfisher(
        replace(cfg, grants=Capabilities(tools=("granted",))), sessions=LocalSessions()
    )
    handed = _handed_to_the_build(monkeypatch)

    events = kf.stream(
        Request("go", agent="only", capabilities=Capabilities(tools=("granted", "withheld")))
    )
    try:
        next(events)
    finally:
        events.close()

    assert handed, "no build was made, so this asserts nothing"
    offered = handed[0].tools or ()
    assert "withheld" not in offered, "a caller reached past what the deployment granted"
    # And the control, or the assertion above passes on a build offered nothing at all.
    assert "granted" in offered
