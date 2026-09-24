"""The Session aggregate: turn allocation and disposal."""

from __future__ import annotations

import pytest

from kingfisher.domain.session import Session


def test_a_callers_own_turn_id_wins(workspace, dirs):
    """A service passes its own request id so it can tie a run back to the request.

    What this no longer promises is de-duplication on a retry. That was `ensure` on
    the same per-turn directory, and the directory went with `/runs`: two runs under
    one id are now two runs, and a caller wanting one has to not ask twice.
    """
    session = Session.open(workspace, "sess", dirs)

    assert session.allocate_turn("req-abc").id == "req-abc"


def test_two_turns_are_told_apart(workspace, dirs):
    """The id used to come from counting directories, which made a listing the
    counter and started a restored session again at `t001`. Nothing reads the
    sequence -- it reaches a printed line, the run log and the result, and none of
    them compares two -- so what is left to hold is only that they differ.
    """
    session = Session.open(workspace, "sess", dirs)

    assert session.allocate_turn().id != session.allocate_turn().id


def test_a_session_is_the_backend_root(workspace, dirs):
    """Every name the agent addresses hangs off it, so it is what a virtual path is
    rooted at -- and naming the session inside one would address outside that root.
    """
    session = Session.open(workspace, "s1", dirs)

    assert session.directory == workspace / "sessions" / "s1"


def test_discard_removes_the_thread_then_the_directory(workspace, dirs):
    order: list[str] = []

    class Threads:
        def delete_thread(self, thread_id: str) -> None:
            order.append("thread")

    session = Session.open(workspace, "gone", dirs)
    session.allocate_turn()

    assert session.discard(dirs, Threads()) is None
    assert order == ["thread"]
    assert not session.directory.exists()


def test_discard_keeps_the_session_whole_when_the_thread_survives(workspace, dirs):
    class Broken:
        def delete_thread(self, thread_id: str) -> None:
            msg = "nope"
            raise RuntimeError(msg)

    session = Session.open(workspace, "stuck", dirs)
    failure = session.discard(dirs, Broken())

    assert failure is not None
    assert "thread not deleted" in failure
    assert session.directory.is_dir(), "directory removed despite the thread surviving"


def test_discard_will_not_report_success_without_a_way_to_delete(workspace, dirs):
    """`dirs` is required, not optional."""
    session = Session.open(workspace, "kept", dirs)

    with pytest.raises(TypeError):
        session.discard()  # ty: ignore[missing-argument]

    assert session.directory.is_dir()


def test_the_turn_message_names_both_forms_of_the_scratch_path(workspace, dirs):
    """Measured over ten runs of one task: told only the virtual path, the agent passed
    it to `execute` 4 times in 10, each failing and costing about three times the whole
    task to recover. It was a per-turn directory then and is the session's `/scratchpad`
    now, which changes nothing about the measurement -- what was measured is the
    agent's handling of the two spellings.
    """
    from kingfisher.application.turn import turn_message

    message = turn_message("count the rows", ())

    assert "/scratchpad" in message
    # Not a plain `in`: the virtual path *contains* the shell form as a substring, so
    # that assertion passed even with the shell form removed. Caught by mutation-testing
    # this test rather than by reading it.
    without_virtual = message.replace("/scratchpad", "")
    assert "scratchpad" in without_virtual, (
        "the shell form is only present as part of '/scratchpad'"
    )
