"""The Session aggregate: naming its turns."""

from __future__ import annotations

from uuid import uuid4

import pytest

from kingfisher.domain.session import Session, UnknownSessionError, session_dir

#: One of each form of id that is not one path segment.
NOT_ONE_SEGMENT = ("", ".", "..", "../../escaped", "/etc", "a/b", "a\x00b")


def test_a_callers_own_turn_id_wins():
    """A service passes its own request id so it can tie a run back to the request.

    What this no longer promises is de-duplication on a retry. That was `ensure` on
    the same per-turn directory, and the directory went with `/runs`: two runs under
    one id are now two runs, and a caller wanting one has to not ask twice.
    """
    session = Session(id="sess")

    assert session.allocate_turn("req-abc").id == "req-abc"


def test_two_turns_are_told_apart():
    """The id used to come from counting directories, which made a listing the
    counter and started a restored session again at `t001`. Nothing reads the
    sequence -- it reaches a printed line, the run log and the result, and none of
    them compares two -- so what is left to hold is only that they differ.
    """
    session = Session(id="sess")

    assert session.allocate_turn().id != session.allocate_turn().id


def test_a_session_directory_is_the_default_backends_root(workspace):
    """Every name the agent addresses hangs off it, so it is what a virtual path is
    rooted at on kingfisher's own backend -- and naming the session inside one would
    address outside that root.
    """
    assert session_dir(workspace, "s1") == workspace / "sessions" / "s1"


@pytest.mark.parametrize("session_id", NOT_ONE_SEGMENT)
def test_an_id_that_is_not_one_path_segment_names_no_session(workspace, session_id):
    """Joined as given, `""` named every session -- `DefaultBackends.delete(cfg, "")`
    removed them all and reported success -- and `..` or an absolute id named somewhere
    outside them.
    """
    with pytest.raises(UnknownSessionError, match="omit session_id to start one"):
        session_dir(workspace, session_id)


def test_an_id_kingfisher_issues_is_one_path_segment(workspace):
    """The control beside the refusals: a rule refusing every id would pass all of them."""
    issued = uuid4().hex

    assert session_dir(workspace, issued) == workspace / "sessions" / issued


def test_the_turn_message_names_both_forms_of_the_scratch_path(workspace):
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
