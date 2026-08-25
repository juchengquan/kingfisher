from __future__ import annotations

import os
import shutil

from kingfisher.domain import retention
from kingfisher.domain.layout import (
    AGENT_HOME,
    LAYOUT_DIRS,
    SESSION_DIRS,
    SESSION_PLUMBING,
)
from kingfisher.infrastructure.workspace_fs import (
    EXAMPLE,
    LocalSessionDirs,
    ensure_layout,
    ensure_session_layout,
)
from tests.conftest import StubCheckpointer


def test_layout_is_created_and_idempotent(tmp_path):
    ws = ensure_layout(tmp_path / "ws")
    ensure_layout(ws)
    for name in LAYOUT_DIRS:
        assert (ws / name).is_dir()
    assert (ws / ".kingfisher" / "WORKSPACE").exists()


def test_the_layout_carries_the_catalogue_example(tmp_path):
    """`models.yaml` is required and has no fallback, so its worked example is
    the one document a new deployment cannot start without reading -- and the
    error it hits without one names this file as the place to look.

    Laid out rather than seeded. It was seeded until seeding gained the ability
    to refuse, at which point a deployment naming no definitions would have been
    told to write `models.yaml` and given nothing to write it from.
    """
    ws = ensure_layout(tmp_path / "ws")

    assert (ws / EXAMPLE).is_file()
    assert "models" in (ws / EXAMPLE).read_text(encoding="utf-8")


def test_the_catalogue_example_is_refreshed_but_not_rewritten(tmp_path):
    """Neither of the two obvious rules.

    Writing every time would touch the disk on every run for nothing --
    `ensure_layout` is called on each invocation, not only on a first one.
    Writing only when absent would mean an upgrade never refreshed the example,
    so a deployment would keep reading last year's annotations for a file that
    had grown fields; re-seeding used to be what refreshed it.

    Both halves are asserted because they fail separately: the first catches a
    write that should not have happened, the second an upgrade that never
    arrives.
    """
    ws = ensure_layout(tmp_path / "ws")
    example = ws / EXAMPLE
    untouched = example.stat().st_mtime_ns

    ensure_layout(ws)
    assert example.stat().st_mtime_ns == untouched, "rewrote an identical file"

    example.write_text("# stale\n", encoding="utf-8")
    ensure_layout(ws)
    assert example.read_text(encoding="utf-8") != "# stale\n", "never refreshed"


def test_no_gitignore_is_written_for_a_repository_nothing_manages(workspace):
    """Kingfisher ran git once -- `pre_run_commit` snapshotted the tracked tier
    before each turn -- and that went with `adapters/workspace_git.py`. The
    ignore file outlived it, describing a review workflow the code no longer
    had, for a repo nothing created or read.

    It was also wrong rather than merely idle: it named two of the five things a
    workspace holds, so `Library/` sat outside it and a `git add -A` offered to
    commit a 21MB pip cache. An operator who wants their workspace versioned is
    better served writing the rules they want than inheriting stale ones.
    """
    assert not (workspace / ".gitignore").exists()


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


def test_one_pass_makes_a_whole_session(tmp_path):
    """What `build_backend` used to finish off, and the reason this exists.

    A session's names lived in two places: four in `SESSION_DIRS`, and `.home`
    and `skills/uploaded` in a line of `build_backend` -- which also re-made
    `data` and `memory`, so nothing created a whole session and the two halves
    could disagree. A backend is now built against a session that exists.
    """
    session = ensure_session_layout(tmp_path / "s")

    for name in (*SESSION_DIRS, *SESSION_PLUMBING):
        assert (session / name).is_dir(), name


def test_the_plumbing_is_listed_apart_from_what_the_agent_addresses():
    """`SESSION_DIRS` means "the names a prompt can refer to", which is why
    `.home` was left out of it rather than forgotten. Keeping the two lists
    separate is what lets that stay a single meaning -- a reader asking what the
    agent can name should not have to filter the answer."""
    assert not set(SESSION_DIRS) & set(SESSION_PLUMBING)
    assert AGENT_HOME in SESSION_PLUMBING
    assert AGENT_HOME.startswith("."), "the agent's home is plumbing, not a name it types"




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
