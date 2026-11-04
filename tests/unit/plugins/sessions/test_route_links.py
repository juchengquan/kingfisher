"""A link inside a route, answered as the operation's error rather than ending the turn."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from kingfisher import Kingfisher, Request
from kingfisher.plugins import LocalSessions
from tests.conftest import EVERY_TOOL, an_agent, start
from tests.unit.scripted import Scripted

NOTES = "draft a\n"


@pytest.fixture
def session(scripted):
    """A session with the links a model or its shell could leave: its notes kept in the
    scratchpad and linked from memory, a saved result that is a link out of its folder,
    one in `/inputs`, whose write bits a shell may lift, and a link in memory that stays in
    memory."""
    an_agent(scripted, **EVERY_TOOL)
    start(scripted, "s")
    made = scripted.workspace / "sessions" / "s"
    (made / "scratchpad" / "notes.md").write_text(NOTES)
    (made / "memory" / "notes.md").symlink_to("../scratchpad/notes.md")
    (made / "scratchpad" / "tool_results" / "kept.txt").symlink_to("../../outputs")
    (made / "inputs").chmod(0o755)
    (made / "inputs" / "notes.md").symlink_to("../scratchpad/notes.md")
    (made / "memory" / "real.md").write_text("kept in memory\n")
    (made / "memory" / "alias.md").symlink_to("real.md")
    return made


def _answer(cfg: Any, tool: str, args: dict[str, Any]) -> ToolMessage:
    Scripted.script.extend(
        [
            AIMessage(content="", tool_calls=[{"name": tool, "args": args, "id": "c1"}]),
            AIMessage("done"),
        ]
    )
    service = Kingfisher(cfg, sessions=LocalSessions())
    request = Request("go", agent="only", session_id="s")
    service.run(request)
    (said,) = {
        one.tool_call_id: one
        for heard in Scripted.heard
        for one in heard
        if isinstance(one, ToolMessage)
    }.values()
    return said


CALLS = [
    ("read_file", {"file_path": "/memory/notes.md"}),
    ("write_file", {"file_path": "/memory/notes.md", "content": "X"}),
    ("edit_file", {"file_path": "/memory/notes.md", "old_string": "a", "new_string": "b"}),
    ("delete", {"file_path": "/memory/notes.md"}),
    ("ls", {"path": "/memory/notes.md"}),
    ("read_file", {"file_path": "/scratchpad/tool_results/kept.txt"}),
    ("read_file", {"file_path": "/inputs/notes.md"}),
]


@pytest.mark.parametrize("call", CALLS, ids=[f"{c[0]}-{n}" for n, c in enumerate(CALLS)])
def test_a_link_out_of_a_route_is_the_operations_error(scripted, session, call):
    """deepagents refuses a link out of a route's root with a `ValueError`, which the file
    tools let escape: a link a model left from `/memory` to its own scratchpad notes ended
    the turn with the host's paths in the error."""
    tool, args = call

    answer = _answer(scripted, tool, args)

    assert answer.status == "error"
    assert "a link out of its folder" in answer.text
    assert str(scripted.workspace) not in answer.text
    assert (session / "scratchpad" / "notes.md").read_text() == NOTES
    assert (session / "memory" / "notes.md").is_symlink()


def test_a_link_that_stays_in_its_route_is_followed(scripted, session):
    """The control: refusing every link in a route would pass the test above, and a link
    inside one stays under the route's own rules, as upstream has it."""
    answer = _answer(scripted, "read_file", {"file_path": "/memory/alias.md"})

    assert answer.status == "success"
    assert "kept in memory" in answer.text
