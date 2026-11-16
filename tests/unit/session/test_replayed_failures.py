"""A failed call is still failed when a later turn replays it from the turn log."""

from __future__ import annotations

from langchain_core.messages import AIMessage, ToolMessage

from kingfisher import Kingfisher
from kingfisher.core.request import Request
from kingfisher.plugins import LocalSessions
from tests.conftest import EVERY_TOOL, an_agent, start
from tests.unit.scripted import Scripted


def test_a_refused_write_is_still_refused_on_the_next_turn(scripted):
    """The turn log kept no status, so a later turn guessed a failure from its opening
    word: "This call did not run: the turn stopped before it began." read as a success, and
    clearing replaced the write's text with a line saying the file held it -- the text
    gone, and a file that was never written named as its home. Any failure carries the
    status; a refused write is the one a turn makes here."""
    an_agent(scripted, **EVERY_TOOL)
    kf = Kingfisher(scripted, sessions=LocalSessions())
    session = start(scripted, "s")
    write = {"name": "write_file", "args": {"file_path": "/inputs/r.md", "content": "x"}}
    Scripted.script.extend(
        [AIMessage("", tool_calls=[{**write, "id": "w1"}]), AIMessage("done"), AIMessage("ok")]
    )

    kf.run(Request("go", agent="only", session_id=session))
    refused = _answer(Scripted.heard[-1], "w1")
    kf.run(Request("again", agent="only", session_id=session))
    replayed = _answer(Scripted.heard[-1], "w1")

    assert refused.status == "error"
    assert (replayed.text, replayed.status) == (refused.text, "error")


def _answer(sent: list, call: str) -> ToolMessage:
    return next(one for one in sent if isinstance(one, ToolMessage) and one.tool_call_id == call)
