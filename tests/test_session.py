"""The Session aggregate: turn allocation and disposal."""

from __future__ import annotations

from concurrent import futures

import pytest

from kingfisher.domain.session import Session


def test_caller_supplied_turn_id_wins_and_is_idempotent(workspace, dirs):
    """A service passes its own request id, so a retry reuses the same turn
    rather than forking a second one."""
    session = Session.open(workspace, "sess", dirs)

    first = session.allocate_turn(dirs, "req-abc")
    again = session.allocate_turn(dirs, "req-abc")

    assert first.id == again.id == "req-abc"
    assert first.directory == again.directory
    assert first.directory.is_dir()


def test_concurrent_allocation_never_collides(workspace, dirs):
    """Scanning for the highest id and then creating it is a race. Allocation
    goes through mkdir, which fails if the name is taken, so two callers
    cannot both decide they are t001."""
    session = Session.open(workspace, "busy", dirs)

    with futures.ThreadPoolExecutor(max_workers=16) as pool:
        ids = list(pool.map(lambda _: session.allocate_turn(dirs).id, range(40)))

    assert len(set(ids)) == 40, f"{len(ids) - len(set(ids))} duplicates"


def test_allocated_ids_are_sequential_and_readable(workspace, dirs):
    session = Session.open(workspace, "ordered", dirs)
    assert [session.allocate_turn(dirs).id for _ in range(3)] == ["t001", "t002", "t003"]


def test_a_turn_knows_its_virtual_paths(workspace, dirs):
    """Virtual paths are machine-independent, so they can go in a task message
    without pinning the prompt to this host."""
    turn = Session.open(workspace, "s", dirs).allocate_turn(dirs, "t001")

    assert turn.virtual_dir == "/runs/t001"
    assert turn.virtual_input_dir == "/runs/t001/input"
    assert str(workspace) not in turn.virtual_dir
    assert turn.input_dir == turn.directory / "input"


def test_a_turn_is_addressed_without_its_session(workspace, dirs):
    """The session directory is the backend root, so naming the session in a
    virtual path would address outside the root — and would put the id into
    the prompt, changing the cached prefix on every session."""
    turn = Session.open(workspace, "s1", dirs).allocate_turn(dirs)

    assert turn.directory == workspace / "sessions" / "s1" / "runs" / "t001"
    assert "s1" not in turn.virtual_dir


def test_a_session_holds_the_whole_vocabulary_not_just_runs(workspace, dirs):
    """It is the backend root now: `data` and `derived` live beside `runs`."""
    session = Session.open(workspace, "s1", dirs)

    assert session.directory == workspace / "sessions" / "s1"
    assert session.runs_dir == session.directory / "runs"


def test_discard_removes_the_thread_then_the_directory(workspace, dirs):
    order: list[str] = []

    class Threads:
        def delete_thread(self, thread_id: str) -> None:
            order.append("thread")

    session = Session.open(workspace, "gone", dirs)
    session.allocate_turn(dirs)

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


def test_turn_allocation_stays_atomic_through_the_port(workspace):
    """The retry loop is the rule; `create_exclusive` is the primitive it needs.

    Turn ids used to be allocated by `mkdir` failing on a taken name. Moving
    the I/O out could have become "scan, then create" in a caller -- which is
    exactly the race the loop exists to avoid. This proves the guarantee
    survived the move: a port that keeps losing the name still yields a
    distinct turn, and never returns one it did not claim.
    """

    class Contended:
        """Loses the first two races, then behaves."""

        def __init__(self):
            self.refused = []
            self.claimed = []

        def ensure(self, path):
            path.mkdir(parents=True, exist_ok=True)

        def create_exclusive(self, path):
            if len(self.refused) < 2:
                self.refused.append(path.name)
                return False
            self.claimed.append(path.name)
            return True

        def children(self, path):
            return ()

        def listing(self, path):
            return ()

        def remove_tree(self, path):
            return None

    contended = Contended()
    turn = Session.open(workspace, "race", contended).allocate_turn(contended)

    assert contended.refused == ["t001", "t002"]  # both races lost
    assert turn.id == "t003"  # and it took the next free name
    assert contended.claimed == ["t003"]  # never claimed one it was refused


def test_discard_will_not_report_success_without_a_way_to_delete(workspace, dirs):
    """`dirs` is required, not optional. An earlier draft defaulted it to None
    and returned None -- a session that deleted nothing and said it had."""
    session = Session.open(workspace, "kept", dirs)

    with pytest.raises(TypeError):
        session.discard()  # ty: ignore[missing-argument]

    assert session.directory.is_dir()


def test_the_shell_form_of_a_run_directory_is_the_virtual_one_without_its_slash(workspace, dirs):
    """The shell starts in the session root, which is what virtual `/` names,
    so the two forms differ by exactly one character. Asserted against the real
    `Turn` because `test_service` drives a hand-written double, and a double
    that drifts is how the message and the filesystem come to disagree.
    """
    session = Session.open(workspace, "s1", dirs)
    turn = session.allocate_turn(dirs)

    assert turn.shell_dir == turn.virtual_dir.lstrip("/")
    assert not turn.shell_dir.startswith("/")
    assert (session.directory / turn.shell_dir) == turn.directory


def test_the_turn_message_names_both_forms(workspace, dirs):
    """Measured over ten runs of one task: told only the virtual path, the agent
    passed it to `execute` 4 times in 10. Each failed with `No such file or
    directory` and cost roughly three times the whole task to recover -- +5.2
    model calls, +19s, +56k input tokens. The 6 that used the shell form first
    never failed once.
    """
    from kingfisher.application.service import turn_message

    session = Session.open(workspace, "s1", dirs)
    turn = session.allocate_turn(dirs)

    message = turn_message("count the rows", turn, (), has_inputs=False)

    assert turn.virtual_dir in message
    # Not a plain `in`: the virtual path *contains* the shell form as a
    # substring, so that assertion passed even with the shell form removed.
    # Caught by mutation-testing this test rather than by reading it.
    without_virtual = message.replace(turn.virtual_dir, "")
    assert turn.shell_dir in without_virtual, (
        f"the shell form is only present as part of {turn.virtual_dir!r}"
    )
