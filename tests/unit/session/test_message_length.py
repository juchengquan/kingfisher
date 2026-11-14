"""How long a turn's message may be: the length deepagents wrote one aside past, now a
refusal before the turn runs."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from kingfisher import Kingfisher, MessageTooLongError, Request
from kingfisher.core.agent.middlewares.standing import LONGEST_MESSAGE
from kingfisher.plugins import LocalSessions
from tests.conftest import an_agent, start
from tests.unit.scripted import Scripted


def _turn(kf: Kingfisher, task: str):
    Scripted.script.append(AIMessage(content="done"))
    return kf.run(Request(task, agent="only", session_id="s"))


def _kingfishers_lines(kf: Kingfisher) -> int:
    """How much kingfisher adds to a task in the session `s`, read off a turn it sent."""
    _turn(kf, "probe")
    (sent,) = [m for m in Scripted.heard[-1] if isinstance(m, HumanMessage)]
    Scripted.heard.clear()
    return len(sent.text) - len("probe")


@pytest.fixture
def kf(scripted) -> Kingfisher:
    an_agent(scripted)
    start(scripted, "s")
    return Kingfisher(scripted, sessions=LocalSessions())


def test_a_message_one_past_the_limit_is_refused_before_any_model_call(kf):
    """A task over deepagents' limit was written to `conversation_history/` and the model
    sent a cut-down copy. The task alone is under it here, so a check of the task without
    kingfisher's own lines would let it through."""
    task = "y" * (LONGEST_MESSAGE - _kingfishers_lines(kf) + 1)

    with pytest.raises(MessageTooLongError, match="send it as an input"):
        _turn(kf, task)

    assert Scripted.heard == []


def test_a_message_at_the_limit_is_sent_whole(kf):
    """The control beside the refusal: a limit read one character short, or the refusal
    counting more than the message, refuses a turn deepagents would have sent whole."""
    task = "y" * (LONGEST_MESSAGE - _kingfishers_lines(kf))

    assert _turn(kf, task).answer == "done"

    sent = [m for m in Scripted.heard[-1] if isinstance(m, HumanMessage)][-1]
    assert len(sent.text) == LONGEST_MESSAGE
    assert sent.text.startswith(task)
