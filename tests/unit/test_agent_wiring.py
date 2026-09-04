from __future__ import annotations

import warnings

import pytest
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage

from kingfisher.domain.agent import AgentSpec
from kingfisher.domain.capabilities import ALL, Capabilities, CapabilityError
from kingfisher.infrastructure.harness.agent import build_agent
from kingfisher.infrastructure.harness.backend import skills_sources
from kingfisher.infrastructure.harness.middleware import (
    REQUIRED_BY_DEEPAGENTS,
    _deepagents_middleware_names,
    declared_middleware,
)
from kingfisher.infrastructure.prompting import system_prompt
from tests.conftest import FakeToolCallingModel, capture_build
from tests.unit.test_confinement import needs_a_real_toolchain


def _all_text(messages) -> str:
    return "\n".join(str(getattr(m, "content", "")) for m in messages)


@needs_a_real_toolchain
def test_agent_runs_shell_and_writes_files(cfg, session_dir):
    """The wiring test that matters: a scripted tool sequence, no network.

    Exercises the three things most likely to be misconfigured at once -- the
    shell env allowlist (does `python3` resolve?), the backend root (does a
    virtual path land inside the workspace?), and tool dispatch itself.
    """
    responses = [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "execute", "args": {"command": 'python3 -c "print(6*7)"'}, "id": "c1"}
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "write_file",
                    "args": {"file_path": "/out.txt", "content": "42"},
                    "id": "c2",
                }
            ],
        ),
        AIMessage(content="done"),
    ]

    agent = build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=responses))
    out = agent.invoke(
        {"messages": [{"role": "user", "content": "go"}]},
        config={"recursion_limit": 12},
    )

    # The shell actually ran, with a PATH we constructed rather than inherited.
    assert "42" in _all_text(out["messages"])
    # The virtual path /out.txt resolved to a real file inside root_dir.
    assert (session_dir / "out.txt").read_text().strip() == "42"


def test_planning_and_permissions_are_wired(cfg, monkeypatch, session_dir):
    """deepagents 0.7.6 ships no planning tool, and /data must be write-denied.

    Asserted at the construction seam rather than by digging into compiled
    graph internals, which are not a public contract.
    """
    captured = capture_build(monkeypatch)
    build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]))

    middleware_names = {type(m).__name__ for m in captured["middleware"]}
    assert "TodoListMiddleware" in middleware_names

    # Two routes are read-only for every request, whatever it was granted:
    # `/data` is the caller's input, and `/skills` is instructions the agent
    # follows. Named rather than counted, so adding a third rule does not fail
    # this and a *removed* one still does.
    # `delete` maps to the `write` operation, so one rule covers write/edit/delete.
    read_only = {
        rule.paths[0] for rule in captured["permissions"]
        if rule.mode == "deny" and "write" in rule.operations
    }
    assert read_only == {"/data/**", "/skills/**"}

    assert captured["system_prompt"] == system_prompt(cfg)
    # M2 capabilities are off by default, so neither is passed through.
    assert "skills" not in captured
    assert "memory" not in captured


def test_enabling_a_capability_wires_the_middleware_not_just_the_prompt(
    cfg,
    monkeypatch,
    session_dir,
):
    """One switch drives both, so the prompt cannot describe a missing capability."""
    from dataclasses import replace

    captured = capture_build(monkeypatch)
    build_agent(
        replace(cfg, skills_enabled=True, memory_enabled=True),
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
    )

    assert captured["skills"] == skills_sources()
    assert captured["memory"] == ["/memory/AGENTS.md"]
    assert "/skills" in captured["system_prompt"]


def test_system_prompt_carries_no_host_paths_or_session_ids():
    """Q19/Q20: the prompt must stay byte-identical across workspaces and
    sessions, or the cached prefix is invalidated on every run."""
    text = system_prompt()
    assert "/Users/" not in text
    assert "/home/" not in text
    assert "session_id" not in text
    # It must still teach the virtual layout.
    assert "/data" in text
    assert "/derived" in text


def test_the_agent_exposes_the_expected_tool_surface(cfg, session_dir):
    """A regression guard on the whole surface, not just one middleware.

    deepagents 0.7.6 ships no planning tool, so `write_todos` here proves
    TodoListMiddleware is wired; `task` proves the general-purpose subagent is
    present even though this model has no harness profile registered.
    """
    agent = build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]))

    names = set()
    for node in agent.nodes.values():
        registry = getattr(getattr(node, "bound", None), "tools_by_name", None)
        if isinstance(registry, dict):
            names |= set(registry)

    assert names == {
        "ls", "read_file", "write_file", "edit_file", "delete", "glob", "grep",
        "execute", "task", "write_todos",
    }


# -- the agent's own middleware --------------------------------------------


class _Audit(AgentMiddleware):
    """Stands in for what a deployment registers: an audit hook, a rate limit.

    No hook, like `Audited` in `test_subagent_middleware.py` -- these assert on
    the list handed to `create_deep_agent`, not on compiled graph nodes, so a
    middleware that declares nothing is enough and stays out of ty's way.
    """

    name = "_Audit"


def _named(spec_middleware, **kwargs) -> AgentSpec:
    return AgentSpec(
        name="probed",
        description="an agent whose file names middleware",
        system_prompt="You work.",
        middleware=spec_middleware,
        **kwargs,
    )


def test_an_agents_own_middleware_is_wrapped_around_the_agent(cfg, monkeypatch, session_dir):
    """`middleware:` in an agent file did nothing to the agent.

    It parsed, and `declares` folded it into `Capabilities.middleware`, where
    the only reader was the `granted=` argument of the delegates' own
    `approved_middleware` call. So the name bounded what an agent's *delegates*
    could activate and never reached the agent -- silently, since nothing
    refuses and `_withheld_by_kind` reports tools, skills and subagents but not
    this.

    Measured before it was fixed, one build, same registry, same name: the
    delegate's graph had an `Audit.before_agent` node and the agent's did not.
    An audit hook that covers the cheap half of a run and not the expensive
    half is worse than none, because it looks like coverage.

    The design doc for this format says `middleware` "read[s] exactly as they
    do in a subagent file". This is that sentence made true.
    """
    captured = capture_build(monkeypatch)

    build_agent(
        cfg,
        agent=_named(("audit",)),
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        middleware_registry={"audit": _Audit},
    )

    assert "_Audit" in {type(m).__name__ for m in captured["middleware"]}


def test_the_agents_middleware_runs_after_the_narrowing_kingfisher_applied(
    cfg, monkeypatch, session_dir
):
    """Last, for the reason `as_subagent` already gives for a delegate's: a
    deployment's middleware should see the tool and skill narrowing rather than
    running ahead of it."""
    captured = capture_build(monkeypatch)

    build_agent(
        cfg,
        agent=_named(("audit",)),
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        middleware_registry={"audit": _Audit},
        capabilities=Capabilities(builtin_tools=("read_file",)),
    )

    names = [type(m).__name__ for m in captured["middleware"]]
    assert names[-1] == "_Audit", f"the deployment's middleware is not last: {names}"
    assert "ToolAllowlist" in names, "nothing narrowed, so this proves nothing"


def test_an_agent_naming_middleware_nothing_registered_is_refused(cfg, session_dir):
    """The same refusal a subagent gets, and for the same reason: a name
    nothing registered is a mistake in the definition, not a narrower caller."""
    with pytest.raises(CapabilityError, match="unregistered middleware"):
        build_agent(
            cfg,
            agent=_named(("audit",)),
            session_dir=session_dir,
            model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
            middleware_registry={},
        )


def test_a_request_may_not_quietly_drop_the_agents_middleware(cfg, session_dir):
    """Withholding it refuses rather than running with less than the definition
    asked for -- which could mean running without the rate limit or the audit
    hook it was written to have. `Capabilities.middleware` defaults to `ALL`, so
    only a request that narrowed it on purpose reaches this."""
    with pytest.raises(CapabilityError, match="may not use"):
        build_agent(
            cfg,
            agent=_named(("audit",)),
            session_dir=session_dir,
            model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
            middleware_registry={"audit": _Audit},
            capabilities=Capabilities(middleware=()),
        )


def test_an_agent_that_names_none_wires_none(cfg, monkeypatch, session_dir):
    """Omission grants nothing here, like `skills` and `subagents` -- so a
    deployment with a registry does not wrap every agent in it by default."""
    captured = capture_build(monkeypatch)

    build_agent(
        cfg,
        agent=_named(None),
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        middleware_registry={"audit": _Audit},
    )

    assert "_Audit" not in {type(m).__name__ for m in captured["middleware"]}


# -- middleware that replaces deepagents' own ------------------------------


class FilesystemMiddleware(AgentMiddleware):
    """A deployment's own middleware, named like deepagents' scaffolding.

    Spelled as the collision rather than as `name = "FilesystemMiddleware"`,
    because a class name is what collides: `AgentMiddleware.name` defaults to
    `self.__class__.__name__`, so this is the shape somebody actually writes
    when they write this by accident, and it needs no `name` line to be one.
    """


class _Summarization(AgentMiddleware):
    """deepagents' own, but not one it refuses to run without.

    The two halves of the notice are different sentences, and this is the one
    that gets the shorter of them.
    """

    name = "SummarizationMiddleware"


def _replacement_warnings(recorded) -> list[str]:
    """Just the ones this file is about, since a build warns about other things."""
    return [str(w.message) for w in recorded if "deepagents merges by name" in str(w.message)]


def test_the_required_names_match_deepagents():
    """`REQUIRED_BY_DEEPAGENTS` is a copy, so something has to compare it.

    Copied rather than imported because `_REQUIRED_MIDDLEWARE_NAMES` is private,
    and an upstream rename would otherwise change which replacement gets the
    louder sentence with nobody deciding.
    """
    from deepagents.graph import _REQUIRED_MIDDLEWARE_NAMES

    assert set(REQUIRED_BY_DEEPAGENTS) == set(_REQUIRED_MIDDLEWARE_NAMES)


def test_deepagents_own_middleware_is_discovered_across_the_package():
    """The breadth, pinned, because the narrow version looked right and was not.

    Walking `graph.py`'s namespace alone found eight names and missed the
    summarizer, which is reached there through a factory. A notice that covers
    most of the stack is the kind nobody notices has stopped covering the rest.
    """
    found = _deepagents_middleware_names()

    assert set(REQUIRED_BY_DEEPAGENTS) <= found
    assert "SummarizationMiddleware" in found, "the factory-built one, missed by graph.py alone"
    assert "AgentMiddleware" not in found, "the base class is not one of deepagents' own"


def test_replacing_deepagents_filesystem_warns_and_still_runs(cfg, monkeypatch, session_dir):
    """The whole point: said, not forbidden.

    A filesystem middleware of one's own is a reasonable thing to deploy, and
    refusing it here would be kingfisher inventing a policy deepagents does not
    have. What is unreasonable is doing it by accident -- the names collide
    because somebody picked an obvious class name, and neither side would
    otherwise mention that deepagents' own is now gone rather than wrapped.

    So the build completes and the middleware is handed over, which is the half
    a warning could quietly have cost.
    """
    captured = capture_build(monkeypatch)

    with pytest.warns(UserWarning, match="deepagents merges by name"):
        build_agent(
            cfg,
            agent=_named(("audit",)),
            session_dir=session_dir,
            model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
            middleware_registry={"audit": FilesystemMiddleware},
        )

    assert "FilesystemMiddleware" in {m.name for m in captured["middleware"]}


def test_the_warning_names_both_of_the_deployments_two_names():
    """A deployment has two names for one middleware and only one is the problem.

    The registry key is fine and stays; the class name is the collision. A
    message naming only the key would send somebody to rename the thing that was
    never wrong. `kind` reaches the wording too, which is why it has no default.
    """
    with pytest.warns(UserWarning) as recorded:
        declared_middleware(
            _named(("audit",)), {"audit": FilesystemMiddleware}, ALL, kind="subagent"
        )

    message = _replacement_warnings(recorded)[0]
    assert "'audit'" in message, "the key it was registered under"
    assert "'FilesystemMiddleware'" in message, "the class name that collides"
    assert message.startswith("subagent "), "the kind, so the reader opens the right file"
    assert "rename the class" in message


def test_replacing_one_deepagents_needs_says_so_more_loudly():
    """Two sentences, because the two cases are not the same size.

    Losing the summarizer costs summarization. Losing `FilesystemMiddleware`
    costs the `permissions` rules every built-in file tool is checked against,
    and a deployment that did that by accident has quietly turned off a
    guarantee rather than a feature.
    """
    with pytest.warns(UserWarning) as recorded:
        declared_middleware(_named(("audit",)), {"audit": FilesystemMiddleware}, ALL, kind="agent")
    required = _replacement_warnings(recorded)[0]

    with pytest.warns(UserWarning) as recorded:
        declared_middleware(
            _named(("summarize",)), {"summarize": _Summarization}, ALL, kind="agent"
        )
    ordinary = _replacement_warnings(recorded)[0]

    assert "refuses to run without" in required
    assert "permissions" in required
    assert "refuses to run without" not in ordinary
    assert "deepagents merges by name" in ordinary, "still said, just not as loudly"


def test_a_middleware_named_its_own_thing_warns_about_nothing(cfg, monkeypatch, session_dir):
    """The quiet path, which is every deployment that named its classes normally.

    Without this the notice could grow into one that fires on every registered
    middleware, and a warning that always fires is one nobody reads.
    """
    captured = capture_build(monkeypatch)

    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        build_agent(
            cfg,
            agent=_named(("audit",)),
            session_dir=session_dir,
            model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
            middleware_registry={"audit": _Audit},
        )

    assert _replacement_warnings(recorded) == []
    assert "_Audit" in {m.name for m in captured["middleware"]}
