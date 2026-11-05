"""What an agent definition actually does to a run."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from kingfisher.core.access.capabilities import ALL, Capabilities, CapabilityError
from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.agent.prompting import system_prompt
from kingfisher.core.layout import HARNESS
from kingfisher.core.request import Request
from kingfisher.core.service import Kingfisher
from kingfisher.plugins import LocalSessions, backend_at
from tests.conftest import FakeToolCallingModel, agent_for, agent_named
from tests.unit.scripted import Scripted

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


# -- every turn names its agent ----------------------------------------------

OTHER = """name: other
description: Another agent.
system_prompt: |
  You are another agent.
"""


def test_a_later_turn_runs_the_agent_as_its_file_now_says(cfg):
    """Kept as a copy, an edit to an agent never reached a session already open, so an
    update reached only the conversations started after it."""
    _agents(cfg, NARROW)
    service = Kingfisher(cfg, sessions=LocalSessions())
    asked = Request("go", agent="narrow", session_id="s")

    agent_for(service, asked)
    _agents(cfg, NARROW.replace("Reads and nothing else.", "Reads and writes now."))

    assert agent_for(service, asked).description == "Reads and writes now."


def test_a_later_turn_may_run_another_agent_on_the_whole_conversation(scripted):
    """A session was fixed to the agent its first turn named, so a caller wanting another
    in the same conversation was refused it; the second must hear the first's turn."""
    _agents(scripted, NARROW, OTHER)
    kf = Kingfisher(scripted, sessions=LocalSessions())
    Scripted.script.extend([AIMessage(content="narrow answered"), AIMessage(content="other")])

    kf.run(Request("first question", agent="narrow", session_id="s"))
    answered = kf.run(Request("second question", agent="other", session_id="s"))

    assert answered.answer == "other"
    heard = [message.content for message in Scripted.heard[-1]]
    assert "narrow answered" in heard, "the second agent was not handed the first's turn"


def test_a_later_turn_naming_no_agent_is_refused(scripted):
    """A turn naming none ran the agent the session opened with; with any agent able to run
    there, it would run whichever another caller's turn left, at that agent's cost."""
    _agents(scripted, NARROW)
    kf = Kingfisher(scripted, sessions=LocalSessions())
    Scripted.script.append(AIMessage(content="done"))
    kf.run(Request("go", agent="narrow", session_id="s"))

    with pytest.raises(CapabilityError, match="names no agent"):
        kf.run(Request("again", session_id="s"))


def test_the_pin_an_older_session_kept_decides_nothing(scripted):
    """Read back, the `agent.yaml` a session kept before every call named its agent would
    go on fixing that agent and shutting out every caller its lock did not name."""
    _agents(
        scripted,
        NARROW.replace("builtin_tools", "source_ids: [A]\nbuiltin_tools"),
        OTHER.replace("system_prompt", "source_ids: [B]\nsystem_prompt"),
    )
    kf = Kingfisher(scripted, sessions=LocalSessions())
    kf.session("s", create=True)
    (scripted.sessions_root / "s" / HARNESS / "agent.yaml").write_text("name: narrow\n")
    Scripted.script.append(AIMessage(content="done"))

    ran = kf.run(Request("go", agent="other", session_id="s"), source_ids=("B",))

    assert ran.answer == "done"


LISTS = """name: lists
description: Lists files.
builtin_tools: [ls]
system_prompt: |
  You list files.
"""


def _last_asked() -> str:
    """The message the last model call was handed as the user's."""
    content = next(m.content for m in reversed(Scripted.heard[-1]) if m.type == "human")
    assert isinstance(content, str), content
    return content


def test_a_turn_is_told_which_tools_called_earlier_it_does_not_have(scripted):
    """A second agent shown the first one's calls made them again, each refused at a model
    call's cost, and on MiniMax-M3 one said it had re-read a file it no longer could."""
    _agents(scripted, LISTS, OTHER)
    kf = Kingfisher(scripted, sessions=LocalSessions())
    listed = AIMessage(content="", tool_calls=[{"name": "ls", "args": {"path": "/"}, "id": "c1"}])
    Scripted.script.extend([listed, AIMessage(content="listed"), AIMessage(content="answered")])
    kf.run(Request("list", agent="lists", session_id="s"))

    kf.run(Request("again", agent="other", session_id="s"))
    told = _last_asked()
    # The control beside it: the agent that has the tool is told nothing.
    Scripted.script.append(AIMessage(content="listed again"))
    kf.run(Request("once more", agent="lists", session_id="s"))

    assert "Not available on this turn: ls." in told
    assert "Not available" not in _last_asked()
