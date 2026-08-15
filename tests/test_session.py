"""The Session aggregate: turn allocation and disposal."""

from __future__ import annotations

import concurrent.futures as futures

from kingfisher.domain.session import Session


def test_caller_supplied_turn_id_wins_and_is_idempotent(workspace):
    """A service passes its own request id, so a retry reuses the same turn
    rather than forking a second one."""
    session = Session.open(workspace, "sess")

    first = session.allocate_turn("req-abc")
    again = session.allocate_turn("req-abc")

    assert first.id == again.id == "req-abc"
    assert first.directory == again.directory
    assert first.directory.is_dir()


def test_concurrent_allocation_never_collides(workspace):
    """Scanning for the highest id and then creating it is a race. Allocation
    goes through mkdir, which fails if the name is taken, so two callers
    cannot both decide they are t001."""
    session = Session.open(workspace, "busy")

    with futures.ThreadPoolExecutor(max_workers=16) as pool:
        ids = list(pool.map(lambda _: session.allocate_turn().id, range(40)))

    assert len(set(ids)) == 40, f"{len(ids) - len(set(ids))} duplicates"


def test_allocated_ids_are_sequential_and_readable(workspace):
    session = Session.open(workspace, "ordered")
    assert [session.allocate_turn().id for _ in range(3)] == ["t001", "t002", "t003"]


def test_a_turn_knows_its_virtual_paths(workspace):
    """Virtual paths are machine-independent, so they can go in a task message
    without pinning the prompt to this host."""
    turn = Session.open(workspace, "s").allocate_turn("t001")

    assert turn.virtual_dir == "/runs/s/t001"
    assert turn.virtual_input_dir == "/runs/s/t001/input"
    assert str(workspace) not in turn.virtual_dir
    assert turn.input_dir == turn.directory / "input"


def test_discard_removes_the_thread_then_the_directory(workspace):
    order: list[str] = []

    class Threads:
        def delete_thread(self, thread_id: str) -> None:
            order.append("thread")

    session = Session.open(workspace, "gone")
    session.allocate_turn()

    assert session.discard(Threads()) is None
    assert order == ["thread"]
    assert not session.directory.exists()


def test_discard_keeps_the_session_whole_when_the_thread_survives(workspace):
    class Broken:
        def delete_thread(self, thread_id: str) -> None:
            msg = "nope"
            raise RuntimeError(msg)

    session = Session.open(workspace, "stuck")
    failure = session.discard(Broken())

    assert failure is not None
    assert "thread not deleted" in failure
    assert session.directory.is_dir(), "directory removed despite the thread surviving"
