from __future__ import annotations

import os
import shutil

from kingfisher.adapters.workspace_fs import LocalSessionDirs, ensure_layout
from kingfisher.domain import retention
from kingfisher.domain.layout import (
    LAYOUT_DIRS,
    SESSION_DIRS,
    WORKSPACE_GITIGNORE,
)
from tests.conftest import StubCheckpointer


def test_layout_is_created_and_idempotent(tmp_path):
    ws = ensure_layout(tmp_path / "ws")
    ensure_layout(ws)
    for name in LAYOUT_DIRS:
        assert (ws / name).is_dir()
    assert (ws / ".gitignore").exists()
    assert (ws / ".kingfisher" / "WORKSPACE").exists()


def test_gitignore_encodes_the_durability_tiers(workspace):
    text = (workspace / ".gitignore").read_text()
    # Harness state is local by design.
    assert ".kingfisher/" in text
    # Everything a session owns -- inputs, derived output, memory, run scratch
    # -- is ignored wholesale. Nothing under sessions/ is re-included: what a
    # run produces and wants kept leaves through the result, not through git.
    assert "sessions/" in text
    assert "!sessions" not in text


def sweep(workspace, keep, checkpointer):
    """What `reap()` does: list, choose by age, apply.

    `keep` is read as "keep sessions younger than this many seconds", which is
    what replaced keeping the newest N -- a count compares every caller's
    sessions against each other, and age asks only about the session itself.
    """
    import time

    dirs = LocalSessionDirs()
    runs = workspace / "runs"
    plan = retention.expired(dirs.listing(runs), older_than_seconds=keep, now=time.time())
    return retention.apply(plan, runs, dirs, checkpointer)


def test_sweep_keeps_the_newest_and_deletes_thread_with_directory(workspace):
    """A swept session loses its directory and its thread together, so the
    checkpointer can never point at files that no longer exist."""
    import time

    now = time.time()
    for name, age in (("oldest", 10_000), ("middle", 20), ("newest", 1)):
        d = workspace / "runs" / name
        d.mkdir(parents=True)
        os.utime(d, (now - age, now - age))

    ckpt = StubCheckpointer()
    result = sweep(workspace, keep=100, checkpointer=ckpt)  # idle over 100s goes

    assert result.removed == ("oldest",)
    assert not (workspace / "runs" / "oldest").exists()
    assert (workspace / "runs" / "newest").exists()
    assert ckpt.deleted == ["oldest"]


def test_sweep_is_a_noop_when_under_the_limit(workspace):
    (workspace / "runs" / "only").mkdir(parents=True)
    result = sweep(workspace, keep=10_000, checkpointer=StubCheckpointer())
    assert result.removed == ()



class BrokenCheckpointer:
    """A checkpointer whose thread deletion fails."""

    def delete_thread(self, thread_id: str) -> None:
        msg = f"cannot delete {thread_id}"
        raise RuntimeError(msg)


def test_sweep_deletes_the_thread_before_the_directory(workspace):
    """No transaction spans a filesystem and sqlite, so the order is chosen to
    make the surviving failure benign."""
    order: list[str] = []

    class Recording:
        def delete_thread(self, thread_id: str) -> None:
            order.append("thread")

    d = workspace / "runs" / "old"
    d.mkdir(parents=True)
    (d / "scratch.txt").write_text("x")

    original = shutil.rmtree

    def watched(*args, **kwargs):
        order.append("directory")
        return original(*args, **kwargs)

    shutil.rmtree = watched
    try:
        sweep(workspace, keep=0, checkpointer=Recording())
    finally:
        shutil.rmtree = original

    assert order == ["thread", "directory"]


def test_a_failed_thread_delete_leaves_the_session_whole(workspace):
    """Nothing is half-deleted: if the thread will not go, the directory stays,
    so the next sweep retries an intact session rather than finding a thread
    that points at files which are gone."""
    d = workspace / "runs" / "old"
    d.mkdir(parents=True)

    result = sweep(workspace, keep=0, checkpointer=BrokenCheckpointer())

    assert result.removed == ()
    assert d.is_dir(), "the directory was removed despite the thread surviving"
    assert len(result.failures) == 1
    assert "thread not deleted" in result.failures[0]


def test_sweep_failures_are_reported_not_swallowed(workspace):
    """A checkpointer that can never delete should be visible, not tolerated
    silently on every single run."""
    for name in ("a", "b"):
        (workspace / "runs" / name).mkdir(parents=True)

    result = sweep(workspace, keep=0, checkpointer=BrokenCheckpointer())

    assert len(result.failures) == 2
    # Each failure names the session it belongs to, so the report is actionable.
    assert {f.split(":")[0] for f in result.failures} == {"a", "b"}
    assert all("RuntimeError" in f for f in result.failures)


def test_the_layout_names_no_genre_of_output():
    """`/reports` privileged one kind of result in the workspace structure
    itself. Durability is the only thing the layout should encode: `/derived`
    survives, `runs/` does not, and what you call the file is your business."""
    assert "reports" not in LAYOUT_DIRS
    assert "reports" not in SESSION_DIRS
    assert "derived" in SESSION_DIRS

    # And nothing is tracked for being a "report" either.
    assert "report" not in WORKSPACE_GITIGNORE




def test_turn_names_are_claimed_exclusively(workspace):
    """The one filesystem guarantee kingfisher's correctness rests on.

    `allocate_turn` is atomic *because* mkdir fails on an existing name -- that
    is why `SessionDirs` has `create_exclusive` at all. A shared filesystem
    that does not honour it would let two concurrent turns share a directory,
    which is the defect the turn tier was built to fix, silently restored.

    Threads rather than processes because the primitive is the kernel's: what
    is being checked is that two callers racing for one name produce one
    winner.
    """
    from concurrent.futures import ThreadPoolExecutor

    dirs = LocalSessionDirs()
    runs = workspace / "runs"
    runs.mkdir(parents=True, exist_ok=True)

    with ThreadPoolExecutor(max_workers=8) as pool:
        won = list(pool.map(lambda _: dirs.create_exclusive(runs / "t001"), range(8)))

    assert sum(won) == 1, "two callers both believed they had claimed the name"
