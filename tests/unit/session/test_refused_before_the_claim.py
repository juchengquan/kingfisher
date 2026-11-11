"""A request refused for what it asks, not for what the session holds, is refused before the
session's claim is taken, so a dead turn's stale claim and its note survive the refusal."""

from __future__ import annotations

import os
import time

import pytest
from langchain_core.messages import AIMessage

from kingfisher import Kingfisher, Request
from kingfisher.core.access.capabilities import Capabilities, CapabilityError
from kingfisher.core.session.files import InputsError
from kingfisher.plugins import LocalSessions
from tests.conftest import an_agent, start
from tests.unit.scripted import Scripted
from tests.unit.stand_ins import claim_of


def _missing_input(tmp_path) -> tuple[Request, type[Exception]]:
    asked = Request("go", agent="only", session_id="s", inputs=(tmp_path / "absent.csv",))
    return asked, InputsError


def _unknown_subagent(tmp_path) -> tuple[Request, type[Exception]]:
    asked = Request(
        "go", agent="only", session_id="s", capabilities=Capabilities(subagents=("ghost",))
    )
    return asked, CapabilityError


@pytest.mark.parametrize("refused", [_missing_input, _unknown_subagent])
def test_a_refused_turn_leaves_the_dead_turns_note_for_the_next(scripted, tmp_path, refused):
    """A turn taking over a dead turn's stale claim and then refused for its request gave
    the claim back with the note in it, so the next turn was never told the one before it
    never finished."""
    an_agent(scripted, subagents='["*"]', builtin_tools='["*"]')
    start(scripted, "s")
    kf = Kingfisher(scripted, sessions=LocalSessions())
    assert LocalSessions().claim(scripted, "s", stale_after=3600)
    long_ago = time.time() - scripted.claim_stale_after - 60
    os.utime(claim_of(scripted, "s"), (long_ago, long_ago))
    asked, raised = refused(tmp_path)

    with pytest.raises(raised):
        kf.run(asked)

    Scripted.script.append(AIMessage(content="checked first"))
    assert kf.run(Request("go", agent="only", session_id="s")).earlier_turn_unfinished
