from __future__ import annotations

import warnings

import pytest
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage

from kingfisher import Kingfisher
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
from tests.conftest import FakeToolCallingModel, capture_build, repository_root
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


def test_the_guide_names_the_same_required_middleware():
    """The third copy, and the one with nothing else watching it.

    `docs/guides/middleware.md` writes both names out, because a page that said
    "two of deepagents' own are required and the warning will tell you which"
    makes the reader wait for the failure it exists to prevent.

    Here rather than beside the other documentation rules, because the person
    who breaks this is not the person editing docs. It is whoever updates the
    tuple after the test above goes red, and this is the next test down from
    the one that sent them.
    """
    page = repository_root() / "docs" / "guides" / "middleware.md"
    text = page.read_text(encoding="utf-8")

    missing = [name for name in REQUIRED_BY_DEEPAGENTS if name not in text]

    assert not missing, (
        f"{missing} are in REQUIRED_BY_DEEPAGENTS and not in {page.name}, which "
        "tells a deployment which two classes it must not shadow by accident"
    )


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


def test_a_registered_instance_is_refused_rather_than_called(cfg, session_dir):
    """`_instantiate` called every non-class entry, so an instance was *called*.

    A deployment writing the natural thing -- `middleware={"audit": _Audit()}`,
    building the object before handing it over -- got
    `TypeError: '_Audit' object is not callable` out of the middle of assembly,
    naming neither the entry nor the definition that asked for it. Every other
    refusal on this path is a `CapabilityError` that names both.

    Refused rather than supported, and the reason is in the message: a
    registered object is built again for every graph, so one shared instance
    accumulates across all of them. `CallCap` counts tool calls in `self._made`;
    as an instance it would spend its budget once and refuse every call
    afterwards for the life of the process.
    """
    with pytest.raises(CapabilityError, match="audit") as raised:
        build_agent(
            cfg,
            agent=_named(("audit",)),
            session_dir=session_dir,
            model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
            # `ty: ignore` is half the test. A type checker rejects this
            # statically -- `MiddlewareFactory` is `Callable[..., Any]` and an
            # instance is not one -- so a deployment running `ty` never gets
            # here. The runtime guard is for the registry assembled from
            # configuration, and for everyone who does not.
            middleware_registry={"audit": _Audit()},  # ty: ignore[invalid-argument-type]
        )

    message = str(raised.value)
    assert "probed" in message, "the definition that named it, as every other refusal here does"
    assert "a built _Audit rather than the class" in message, (
        "the mistake named, not just 'not callable' -- nobody registers a dict by accident"
    )
    assert "built again for every graph" in message, (
        "and why, or this reads as a rule kingfisher invented"
    )


def test_an_entry_no_definition_names_is_still_refused_when_it_is_registered(cfg):
    """The half `_instantiate` cannot reach, and the reason the sweep exists.

    `_instantiate` runs only for names a definition actually asked for. Register
    `{"audit": _Audit()}` today, write the definition that names it next month,
    and the mistake waits that long -- in somebody else's deployment, on the
    first request unlucky enough to reach it.

    The same argument `model_catalogue` makes for refusing an unbuildable `api`
    as the file loads rather than when a turn starts: it is a fact about what
    was written, not about the request that met it.
    """
    with pytest.raises(CapabilityError, match="audit"):
        Kingfisher(cfg, middleware={"audit": _Audit()})  # ty: ignore[invalid-argument-type]


def test_the_sweep_refuses_anything_uncallable_not_only_a_middleware(cfg):
    """The gate is `callable`, which is what `MiddlewareFactory` promises.

    Narrowing it to `isinstance(entry, AgentMiddleware)` would name the common
    mistake and let every other uncallable entry through to the `TypeError` this
    replaces. A registry holding a settings dict -- the shape somebody reaches
    for when they confuse registering with configuring -- is refused too, and
    said differently, because "a built X rather than the class" would be wrong
    about it.
    """
    with pytest.raises(CapabilityError, match="which cannot be called") as raised:
        Kingfisher(cfg, middleware={"audit": {"limit": 20}})  # ty: ignore[invalid-argument-type]

    assert "a dict" in str(raised.value)


def test_a_registered_class_is_built_again_for_every_graph(cfg, monkeypatch, session_dir):
    """The lifecycle the refusal above gives as its reason, held to it.

    The message says a middleware is built again for every graph, and that is
    the whole justification for refusing an instance. Nothing else in the tree
    checks it: graphs are built per request for reasons that have nothing to do
    with middleware -- capabilities narrow them, the backend is session-anchored
    -- and `service.py` already describes the cache that would end that as the
    thing to reach for above roughly 150 concurrent turns.

    If that day comes, a registered `CallCap` starts surviving across turns and
    the refusal's stated reason quietly inverts. This is what makes that a
    failure rather than a cap that stops capping.
    """
    built = []
    for _ in range(2):
        captured = capture_build(monkeypatch)
        build_agent(
            cfg,
            agent=_named(("audit",)),
            session_dir=session_dir,
            model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
            middleware_registry={"audit": _Audit},
        )
        built.append(next(m for m in captured["middleware"] if type(m) is _Audit))

    first, second = built
    assert first is not second, (
        "two graphs shared one middleware object, so a counter in it would carry "
        "across them -- which is the thing the instance refusal promises cannot happen"
    )
