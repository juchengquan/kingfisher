"""What an agent definition actually does to a run."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from kingfisher.core.access.capabilities import ALL, Capabilities, CapabilityError
from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.agent.prompting import system_prompt
from kingfisher.core.layout import DEFAULT_LAYOUT, PINNED_AGENT
from kingfisher.core.request import Request
from kingfisher.core.service import Kingfisher
from kingfisher.core.session.steps import drive
from kingfisher.plugins import LocalSessions, backend_at
from tests.conftest import FakeToolCallingModel, agent_for, agent_named, harness_in

NARROW = """name: narrow
description: Reads and nothing else.
builtin_tools: [read_file, ls]
system_prompt: |
  You read files and say what is in them.
"""

CHEAP = """name: cheap-one
description: Runs somewhere cheaper.
model: cheap-model
system_prompt: |
  You do the cheap half of the work.
"""


def _session(cfg, session_id: str):
    """One session's directory, made. `_agent_for` takes the directory now: the pin
    lives inside the session rather than in a directory keyed by its name."""
    from kingfisher.plugins.sessions.folders import ensure_session_layout

    return ensure_session_layout(cfg.workspace / "sessions" / session_id, DEFAULT_LAYOUT)


def _agents(cfg, *bodies: str) -> None:
    directory = cfg.catalogue_roots["agents"]
    directory.mkdir(parents=True, exist_ok=True)
    for body in bodies:
        name = body.split("\n")[0].removeprefix("name: ").strip()
        (directory / f"{name}.yaml").write_text(body, encoding="utf-8")


def _spec(cfg, name: str):
    from kingfisher.core.definitions.agents.catalogue import LocalAgentRepository

    return LocalAgentRepository(cfg.catalogue_roots["agents"]).specs[name]


def _offered(built) -> set[str]:
    """The tools the model will actually be offered."""
    for middleware in built.middleware:
        allowed = getattr(middleware, "_allowed", None)
        if allowed is not None:
            return set(allowed)
    unnarrowed = "no allowlist was wired, so nothing was narrowed"
    raise AssertionError(unnarrowed)


# -- the agent decides ------------------------------------------------------


def _held(cfg):
    """Session `s`, as the turn reads its pinned agent."""
    return harness_in(cfg, _session(cfg, "s"))


def test_an_agent_holds_only_the_tools_its_file_names(cfg, session_dir):
    """The whole point of the file."""
    _agents(cfg, NARROW)

    built = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        agent=_spec(cfg, "narrow"),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
    )

    held = _offered(built)
    assert "read_file" in held
    assert not held & {"write_file", "edit_file", "delete", "execute"}


def test_a_request_narrows_the_agent_and_cannot_widen_it(cfg, session_dir):
    """A caller asking for a tool the agent never held gets the overlap, which is empty."""
    _agents(cfg, NARROW)

    built = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        agent=_spec(cfg, "narrow"),
        capabilities=Capabilities(builtin_tools=("read_file", "execute")),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
    )

    held = _offered(built)
    assert "read_file" in held
    assert "execute" not in held, "a request reached past what the agent declared"


def test_the_agents_prompt_comes_first_then_the_instructions_then_the_harness(cfg):
    """Three parts, most specific first: the client's own text opens the prompt, ahead of
    kingfisher's account of the session."""
    assembled = system_prompt(
        cfg, "You read files and say what is in them.", instructions="House rule: be terse."
    )

    harness = assembled.index("/inputs")
    house = assembled.index("House rule")
    mine = assembled.index("You read files")
    assert mine < house < harness


def test_the_agent_runs_the_model_its_file_names(cfg, session_dir):
    """`cheap` is bound by the deployment, not written in the file: an agent that
    travels between deployments cannot portably name a vendor's id.
    """
    _agents(cfg, CHEAP)

    built = build_agent(cfg, backend=backend_at(cfg, session_dir), agent=_spec(cfg, "cheap-one"))

    assert built.model.model == "cheap-model"


# -- naming one -------------------------------------------------------------


def test_a_request_that_names_no_agent_is_refused_once_the_definitions_have_any(cfg):
    """No default and no implicit one."""
    _agents(cfg, NARROW, CHEAP)
    service = Kingfisher(cfg, sessions=LocalSessions())

    with pytest.raises(CapabilityError, match="names no agent"):
        agent_named(service, None)


def test_the_refusal_lists_what_the_definitions_actually_offer(cfg):
    _agents(cfg, NARROW, CHEAP)
    service = Kingfisher(cfg, sessions=LocalSessions())

    with pytest.raises(CapabilityError, match="cheap-one, narrow"):
        agent_named(service, "analyst")


def test_an_empty_definitions_folder_is_told_how_to_get_one(cfg):
    """The other half of that message."""
    service = Kingfisher(cfg, sessions=LocalSessions())

    with pytest.raises(CapabilityError, match="kingfisher seed"):
        agent_named(service, "analyst")


def test_naming_one_is_required_even_where_there_is_nothing_to_name(cfg):
    """No default, and no exemption for an empty workspace either."""
    with pytest.raises(CapabilityError, match="names no agent"):
        agent_named(Kingfisher(cfg, sessions=LocalSessions()), None)


def test_the_request_carries_the_name_and_nothing_more(cfg):
    """Names, never definitions -- so an untrusted caller can activate what the
    deployment reviewed and invent nothing.
    """
    asked = Request("go", agent="narrow")

    assert asked.agent == "narrow"
    assert asked.capabilities == Capabilities()
    assert Capabilities().builtin_tools == ALL


def test_a_request_naming_no_agent_is_still_a_valid_request(cfg):
    """Refused where the catalogue is known, not in the record."""
    assert Request("go").agent is None


# -- a session keeps the agent it opened with -------------------------------


def test_a_later_turn_runs_the_agent_as_its_file_now_says(cfg):
    """Kept as a copy, an edit to an agent never reached a session already open, so an
    update reached only the conversations started after it."""
    _agents(cfg, NARROW)
    service = Kingfisher(cfg, sessions=LocalSessions())
    asked = Request("go", agent="narrow", session_id="s")

    agent_for(service, asked, _held(cfg))
    _agents(cfg, NARROW.replace("Reads and nothing else.", "Reads and writes now."))

    assert agent_for(service, asked, _held(cfg)).description == "Reads and writes now."


def test_a_session_that_kept_a_whole_copy_follows_its_agent_as_it_is_now(cfg):
    """A session opened before kept its agent's whole definition. Read for more than its
    name, it would go on admitting the callers, and running the prompt, that the copy
    wrote."""
    locked = "source_ids: [{}]\nbuiltin_tools"
    _agents(
        cfg,
        NARROW.replace("builtin_tools", locked.format("B")),
        CHEAP.replace("builtin_tools", locked.format("A")),
    )
    service = Kingfisher(cfg, sessions=LocalSessions())
    copy = NARROW.replace("Reads and nothing else.", "What the copy said.")
    copy = copy.replace("builtin_tools", locked.format("A"))
    drive(_held(cfg).store(PINNED_AGENT, copy.encode()))

    ran = agent_for(service, Request("go", session_id="s"), _held(cfg), source_ids=["B"])

    assert ran.description == "Reads and nothing else."
    assert service.session("s", source_ids=("B",)) is not None
    assert service.session("s", source_ids=("A",)) is None


def test_naming_a_different_agent_later_is_refused_rather_than_ignored(cfg):
    """Honouring it is wrong and ignoring it is worse: the caller asked a question and
    would be told nothing.
    """
    _agents(cfg, NARROW, CHEAP)
    service = Kingfisher(cfg, sessions=LocalSessions())
    agent_for(service, Request("go", agent="narrow", session_id="s"), _held(cfg))

    with pytest.raises(CapabilityError, match="running 'narrow'"):
        agent_for(service, Request("again", agent="cheap-one", session_id="s"), _held(cfg))


def test_naming_the_same_agent_again_is_fine(cfg):
    """A stateless caller sends the same payload every turn and should not have to
    remember what it opened the session with.
    """
    _agents(cfg, NARROW)
    service = Kingfisher(cfg, sessions=LocalSessions())
    asked = Request("go", agent="narrow", session_id="s")
    agent_for(service, asked, _held(cfg))

    assert agent_for(service, asked, _held(cfg)).name == "narrow"


def test_a_turn_that_names_nothing_still_gets_the_sessions_agent(cfg):
    """The session decides, not the turn."""
    _agents(cfg, NARROW)
    service = Kingfisher(cfg, sessions=LocalSessions())
    agent_for(service, Request("go", agent="narrow", session_id="s"), _held(cfg))

    assert agent_for(service, Request("again", session_id="s"), _held(cfg)).name == "narrow"


def test_a_request_refused_at_the_build_pins_no_agent(scripted):
    """The pin was written before the build's refusals had run, so a session whose first
    request was refused there was fixed to that agent with no turn run in it: the next
    request, for another agent, was told the session was running the first.
    """
    from kingfisher.core.definitions.subagents.spec import RunOn
    from tests.conftest import subagents_dir
    from tests.unit.scripted import Scripted

    subagents_dir(scripted).mkdir(parents=True, exist_ok=True)
    (subagents_dir(scripted) / "reviewer.yaml").write_text(
        "name: reviewer\ndescription: Checks figures.\nsystem_prompt: |\n  You check.\n",
        encoding="utf-8",
    )
    delegating = (
        "name: delegating\ndescription: Hands work on.\nbuiltin_tools: [task]\n"
        "subagents: [reviewer]\nsystem_prompt: |\n  You hand work on.\n"
    )
    _agents(scripted, NARROW, delegating)
    kf = Kingfisher(scripted, sessions=LocalSessions())
    kf.session("s", create=True)

    with pytest.raises(CapabilityError, match="did not activate"):
        kf.run(Request("go", agent="delegating", session_id="s", run_on={"ghost": RunOn("x")}))

    Scripted.script.append(AIMessage(content="done"))
    assert kf.run(Request("go", agent="narrow", session_id="s")).answer == "done"


def test_a_sessions_agent_is_written_once_and_not_overwritten(cfg, tmp_path):
    """The property that fixes a session to one agent.

    Its only caller checks first, so this holds it directly: a second writer added later
    would switch a session's agent under a history made with another, and every test
    above would still pass.
    """
    from kingfisher.core.session.store import pinned_in, pinned_name, remember_agent

    harness = harness_in(cfg, tmp_path)
    drive(remember_agent(harness, "first"))
    drive(remember_agent(harness, "second"))

    pinned, _zone = drive(pinned_in(harness))
    assert pinned is not None
    assert pinned_name(pinned) == "first"
