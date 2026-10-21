"""A portable subagent runs the agent's middleware, as `SubagentMiddlewares` chooses.

Driven through `build_agent` with an agent read from its own file: a portable entry
may name no middleware, so what it runs is only decided where the two meet.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage

from kingfisher import Kingfisher, Request
from kingfisher.core.access.capabilities import Capabilities, CapabilityError
from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.definitions.agents.catalogue import LocalAgentRepository
from kingfisher.core.definitions.subagents.spec import RunOn, declared
from kingfisher.plugins import AgentsMiddlewares, LocalSessions, Plugins, backend_at
from tests.conftest import (
    EVERY_TOOL,
    FakeToolCallingModel,
    an_agent,
    middleware_of,
    start,
    subagent_of,
    subagents_dir,
)
from tests.unit.scripted import Scripted


class Audit(AgentMiddleware):
    """A deployment's own, with a setting the agent's file may write."""

    name = "Audit"
    defaults = {"level": "INFO"}
    yaml_settable = frozenset({"level"})

    def __init__(self, level: str) -> None:
        self.level = level
        super().__init__()


class Other(AgentMiddleware):
    """A second, so a summoner's line can differ from the agent's."""

    name = "Other"


class WantsAModel(AgentMiddleware):
    """Filled in with the model the graph it is built for runs."""

    name = "WantsAModel"
    wants = frozenset({"model"})

    def __init__(self, model: object) -> None:
        self.model = model
        super().__init__()


REGISTRY = {"audit": Audit, "other": Other, "wants_model": WantsAModel, "rogue": Other}

PORTABLE = """
SUBAGENTS = [
    {
        "name": "surveyor",
        "description": "Surveys files.",
        "system_prompt": "You survey.",
        "builtin_tools": ["read_file"],
    }
]
"""

COMPILED = '''
SUBAGENTS = [
    {
        "name": "built",
        "description": "Runs as it was built.",
        "build": lambda model, tools: _graph(model, tools),
    }
]


def _graph(model, tools):
    from langchain.agents import create_agent

    return create_agent(model, tools)
'''


class Answering:
    """A `SubagentMiddlewares` answering `names` for every subagent, and keeping what it
    was asked."""

    def __init__(self, names: tuple[str, ...] | None = None) -> None:
        self.answer = names
        self.asked: list[tuple[str, str, tuple[str, ...]]] = []

    def names(self, subagent: str, agent: str, middlewares: tuple[str, ...]) -> Any:
        self.asked.append((subagent, agent, middlewares))
        return middlewares if self.answer is None else self.answer


def _write(cfg: Any, name: str, text: str) -> None:
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / name).write_text(text, encoding="utf-8")


def _file_subagent(cfg: Any, name: str, body: str = "") -> None:
    _write(
        cfg,
        f"{name}.yaml",
        f"name: {name}\ndescription: Helps.\n{body}system_prompt: |\n  You help.\n",
    )


def _built(
    cfg: Any, session_dir: Any, plugin: Any = None, agent: str = "only", **given: Any
) -> Any:
    return build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        middleware_registry=REGISTRY,
        agent=LocalAgentRepository(cfg.catalogue_roots["agents"]).specs[agent],
        subagent_middlewares=plugin,
        **given,
    )


def _deployments(middleware: list[Any]) -> list[str]:
    """The registry's classes in a stack, by name: the standing ones are not this file's."""
    return [type(m).__name__ for m in middleware if type(m) in set(REGISTRY.values())]


def test_a_portable_subagent_runs_the_agents_middleware_and_a_plugin_may_say_none(
    cfg, session_dir
):
    """A portable subagent ran under the standing middleware alone -- no summariser,
    nothing the deployment offers -- though it may name none of its own."""
    _write(cfg, "surveyor.py", PORTABLE)
    an_agent(cfg, middlewares="[audit, other]", subagents="[surveyor]", **EVERY_TOOL)

    left_out = _built(cfg, session_dir)
    nothing = _built(cfg, session_dir, Answering(()))

    assert _deployments(middleware_of(left_out, "surveyor")) == ["Audit", "Other"]
    # The control: a plugin answering nothing is how a deployment turns it off.
    assert _deployments(middleware_of(nothing, "surveyor")) == []


def test_a_subagent_from_the_definitions_folder_is_not_portable(cfg, session_dir):
    """A file in the definitions folder has no `build` either, so a check falling back to
    `build` would hand one writing no `middlewares:` the agent's, unasked."""
    _file_subagent(cfg, "plain")
    _write(cfg, "surveyor.py", PORTABLE)
    an_agent(cfg, middlewares="[audit]", subagents="[plain, surveyor]", **EVERY_TOOL)
    asking = Answering()

    built = _built(cfg, session_dir, asking)

    assert _deployments(middleware_of(built, "plain")) == []
    assert [subagent for subagent, _, _ in asking.asked] == ["surveyor"]
    # The control beside it: the portable one, built in the same graph, does inherit.
    assert _deployments(middleware_of(built, "surveyor")) == ["Audit"]
    assert declared({"name": "s", "description": "d", "system_prompt": "Go."}, "x").portable


def test_a_compiled_subagent_is_never_asked_about(cfg, session_dir):
    """A compiled graph is built already: a plugin asked about one would be choosing
    middleware nothing wraps it in."""
    _write(cfg, "built.py", COMPILED)
    _write(cfg, "surveyor.py", PORTABLE)
    an_agent(cfg, middlewares="[audit]", subagents="[built, surveyor]", **EVERY_TOOL)
    asking = Answering()

    built = _built(cfg, session_dir, asking)

    assert [subagent for subagent, _, _ in asking.asked] == ["surveyor"]
    assert "middleware" not in subagent_of(built, "built")


def test_the_copy_carries_the_agents_settings_and_its_own_model(cfg, session_dir):
    """Settings the agent's file wrote reach the subagent's copy, and a `wants: model` is
    filled with the model the subagent runs, not the agent's."""
    _write(cfg, "surveyor.py", PORTABLE)
    an_agent(
        cfg,
        middlewares="\n  - name: audit\n    settings: {level: DEBUG}\n  - wants_model",
        subagents="[surveyor]",
        **EVERY_TOOL,
    )

    built = _built(
        cfg,
        session_dir,
        run_on={"surveyor": RunOn("cheap-model")},
        capabilities=Capabilities(models=("cheap-model",)),
    )

    audit, wants = [m for m in middleware_of(built, "surveyor") if type(m) in (Audit, WantsAModel)]
    assert isinstance(audit, Audit)
    assert isinstance(wants, WantsAModel)
    # Against `Audit.defaults`, which is what a dropped setting would build with.
    assert audit.level == "DEBUG"
    assert getattr(wants.model, "model", None) == "cheap-model"
    # The control: the agent's own copy holds the model the agent runs.
    (agents,) = [m for m in built.middleware if isinstance(m, WantsAModel)]
    assert isinstance(agents.model, FakeToolCallingModel)


def test_a_nested_portable_helper_gets_the_agents_names_not_its_summoners(cfg, session_dir):
    """A helper given its summoner's line would run what one subagent chose, where the
    agent's line is the ceiling everything beneath it is clamped by."""
    _file_subagent(
        cfg, "worker", "builtin_tools: [task]\nsubagents: [surveyor]\nmiddlewares: [other]\n"
    )
    _write(cfg, "surveyor.py", PORTABLE)
    an_agent(cfg, middlewares="[audit, other]", subagents="[worker, surveyor]", **EVERY_TOOL)
    asking = Answering()

    built = _built(cfg, session_dir, asking)

    worker = middleware_of(built, "worker")
    (task,) = [m for m in worker if type(m).__name__ == "SubAgentMiddleware"]
    (helper,) = [one for one in task._subagents if one["name"] == "surveyor"]
    assert _deployments(helper["middleware"]) == ["Audit", "Other"]
    # The control: the summoner itself runs its own line.
    assert _deployments(worker) == ["Other"]
    assert {agent for _, agent, _ in asking.asked} == {"only"}


def test_a_name_past_the_agents_line_is_refused_naming_the_plugin(cfg, session_dir):
    """Dropped, a name the agent was never granted would read as a plugin that worked;
    worded as the subagent's, it sends the reader to a definition that names nothing."""
    _write(cfg, "surveyor.py", PORTABLE)
    an_agent(cfg, middlewares="[audit]", subagents="[surveyor]", **EVERY_TOOL)

    with pytest.raises(CapabilityError, match="may not use: rogue") as raised:
        _built(cfg, session_dir, Answering(("audit", "rogue")))

    assert "SubagentMiddlewares" in str(raised.value)
    # The control: the same agent, answered within its line, builds.
    _built(cfg, session_dir, Answering(("audit",)))


def test_the_plugin_is_asked_once_per_subagent_compiled_not_per_path(cfg, session_dir):
    """Asked per path, a catalogue where many subagents consult one helper would ask about
    it once for each, where the subagent itself is compiled once."""
    for summoner in ("first", "second"):
        _file_subagent(cfg, summoner, "builtin_tools: [task]\nsubagents: [surveyor]\n")
    _write(cfg, "surveyor.py", PORTABLE)
    an_agent(cfg, middlewares="[audit]", subagents="[first, second, surveyor]", **EVERY_TOOL)
    asking = Answering()

    _built(cfg, session_dir, asking)

    # Twice: on its own, and as a helper -- compiled once for both summoners.
    assert asking.asked == [("surveyor", "only", ("audit",))] * 2


def test_the_default_answers_the_agents_own():
    """Left out, a portable subagent gets what the agent runs, in the agent's order."""
    assert AgentsMiddlewares().names("s", "a", ("b", "a")) == ("b", "a")


def test_the_deployments_plugin_reaches_every_turn(scripted):
    """A plugin `Kingfisher` held and never handed to the build would leave every portable
    subagent on the default, whatever the deployment wired. Through `Plugins`, as the
    command hands it."""
    _write(scripted, "surveyor.py", PORTABLE)
    an_agent(scripted, middlewares="[audit]", subagents="[surveyor]", **EVERY_TOOL)
    Scripted.script.append(AIMessage("done"))
    asking = Answering()
    wiring = Plugins(subagent_middlewares=asking).keywords()

    Kingfisher(scripted, sessions=LocalSessions(), middlewares=REGISTRY, **wiring).run(
        Request("go", agent="only", session_id=start(scripted, "s"))
    )

    assert asking.asked == [("surveyor", "only", ("audit",))]
