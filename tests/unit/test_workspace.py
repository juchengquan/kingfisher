from __future__ import annotations

import os

from kingfisher import WorkspacePaths
from kingfisher.core.definitions.catalogue import DEFINITION_KINDS
from kingfisher.core.layout import (
    DEFAULT_LAYOUT,
    HARNESS,
    LAYOUT_DIRS,
    SESSION_DIRS,
)
from kingfisher.core.workspace import EXAMPLE, EXAMPLES, ensure_definitions, ensure_layout
from kingfisher.plugins import LocalSessions
from kingfisher.plugins.sessions.folders import LocalSessionDirs, ensure_session_layout


def test_layout_is_created_and_idempotent(tmp_path):
    ws = ensure_layout(tmp_path / "ws")
    ensure_layout(ws)
    for name in LAYOUT_DIRS:
        assert (ws / name).is_dir()
    assert (ws / ".kingfisher" / "WORKSPACE").exists()


def test_the_workspace_layout_holds_nothing_a_person_writes(tmp_path):
    """A kind folder or a worked example in the workspace is a place to write a
    definition or a catalogue that nothing reads: both are read from the definitions
    folder.
    """
    ws = ensure_layout(tmp_path / "ws")

    assert {p.name for p in ws.iterdir()} == set(LAYOUT_DIRS)


def test_the_definitions_folder_is_laid_out_and_idempotent(tmp_path):
    folder = ensure_definitions(tmp_path / "definitions")
    ensure_definitions(folder)

    for name in DEFINITION_KINDS:
        assert (folder / name).is_dir(), name
    for name in EXAMPLES:
        assert (folder / name).is_file(), name


def test_a_kind_folder_already_in_the_workspace_is_kept(tmp_path):
    """A layout that tidied away a workspace's own `skills/` would delete an operator's
    definitions on the first start after they moved to the definitions folder.
    """
    ws = tmp_path / "ws"
    (ws / "skills" / "mine").mkdir(parents=True)

    ensure_layout(ws)

    assert (ws / "skills" / "mine").is_dir()


def test_a_start_makes_no_kind_folder(tmp_path):
    """A start that made the kinds it did not find would write into a folder several
    deployments read, and hide a mount that failed behind a catalogue that is merely
    empty.
    """
    from kingfisher import Kingfisher
    from kingfisher.core.config import Config
    from tests.conftest import FAKE_CATALOGUE

    definitions = tmp_path / "definitions"
    definitions.mkdir()
    cfg = Config(workspace=tmp_path / "ws", definitions=definitions, models=FAKE_CATALOGUE)

    kf = Kingfisher(cfg, sessions=LocalSessions())

    assert not list(definitions.iterdir()), "a start wrote into the definitions folder"
    assert not set(DEFINITION_KINDS) & {p.name for p in kf.workspace.iterdir()}, (
        "a folder in the workspace that nothing reads"
    )


def test_the_definitions_folder_carries_the_catalogue_example(tmp_path):
    """`models.yaml` is required and has no fallback, so its worked example is the one
    document a new deployment cannot start without reading -- and the error it hits
    without one names this file as the place to look.
    """
    folder = ensure_definitions(tmp_path / "definitions")

    assert (folder / EXAMPLE).is_file()
    assert "models" in (folder / EXAMPLE).read_text(encoding="utf-8")


def test_the_definitions_folder_carries_a_keys_example_holding_no_keys(tmp_path):
    """Placed beside the catalogue's, whose `key:` entries name its lines. It holds no
    live entry, so renaming it in place puts no key in a folder several deployments read
    until one is typed into it -- and its first lines say to copy it out first.
    """
    import yaml

    from kingfisher.core.workspace import KEYS_EXAMPLE

    folder = ensure_definitions(tmp_path / "definitions")
    text = (folder / KEYS_EXAMPLE).read_text(encoding="utf-8")

    assert yaml.safe_load(text) is None, "the example carries a live entry"
    assert "KINGFISHER_MODEL_KEYS_FILE" in text
    assert "out of this folder" in text


def test_the_catalogue_example_is_refreshed_but_not_rewritten(tmp_path):
    """Neither of the two obvious rules."""
    folder = ensure_definitions(tmp_path / "definitions")
    example = folder / EXAMPLE
    untouched = example.stat().st_mtime_ns

    ensure_definitions(folder)
    assert example.stat().st_mtime_ns == untouched, "rewrote an identical file"

    example.write_text("# stale\n", encoding="utf-8")
    ensure_definitions(folder)
    assert example.read_text(encoding="utf-8") != "# stale\n", "never refreshed"


def test_the_example_lands_beside_the_catalogue_it_describes(tmp_path):
    """Asked of where `models.yaml` is read from rather than of a folder written out
    here, so moving the catalogue without its example fails rather than passing on a
    copy that sits where nothing reads.
    """
    paths = WorkspacePaths(tmp_path / "definitions", workspace=tmp_path / "ws")

    ensure_definitions(paths.definitions)

    assert (paths.authored_files["models.yaml"].parent / EXAMPLE).is_file()


def test_no_source_ids_example_is_placed_anywhere(tmp_path):
    """`groups.yaml.example` shipped and was placed for two days, and went."""
    folder = ensure_definitions(tmp_path / "definitions")

    assert not list(folder.glob("source_ids.yaml*")), "a source ids file or example came back"
    assert (folder / EXAMPLE).is_file(), "the control: the catalogue's example is there"


def test_no_gitignore_is_written_for_a_repository_nothing_manages(workspace):
    """Kingfisher ran git once -- `pre_run_commit` snapshotted the tracked tier before
    each turn -- and that went with `adapters/workspace_git.py`.

    It was also wrong rather than merely idle: it named two of the five things a
    workspace holds, so `Library/` sat outside it and a `git add -A` offered to
    commit a 21MB pip cache. An operator who wants their workspace versioned is
    better served writing the rules they want than inheriting stale ones.
    """
    assert not (workspace / ".gitignore").exists()


def sweep(workspace, keep, sessions=None):
    """`reap`, over the sessions under this workspace."""
    import time

    from kingfisher import Kingfisher
    from kingfisher.core.config import Config
    from tests.conftest import FAKE_CATALOGUE
    from tests.unit.test_run import StubAgent

    definitions = ensure_definitions(workspace.parent / "definitions")
    cfg = Config(workspace=workspace, definitions=definitions, models=FAKE_CATALOGUE)
    kf = Kingfisher(cfg, graph=StubAgent("ok"), sessions=sessions or LocalSessions())
    return kf.reap(older_than_seconds=keep, now=time.time())


def test_sweep_keeps_the_newest_and_deletes_the_idle(workspace):
    """A swept session loses its directory, and one still in use keeps it."""
    import time

    now = time.time()
    for name, age in (("oldest", 10_000), ("middle", 20), ("newest", 1)):
        d = workspace / "sessions" / name
        d.mkdir(parents=True)
        os.utime(d, (now - age, now - age))

    result = sweep(workspace, keep=100)  # idle over 100s goes

    assert result.removed == ("oldest",)
    assert not (workspace / "sessions" / "oldest").exists()
    assert (workspace / "sessions" / "newest").exists()


def test_sweep_is_a_noop_when_under_the_limit(workspace):
    (workspace / "sessions" / "only").mkdir(parents=True)
    result = sweep(workspace, keep=10_000)
    assert result.removed == ()


def test_sweep_failures_are_reported_not_swallowed(workspace):
    """Sessions that can never be deleted should be visible, not tolerated silently on
    every single run.
    """

    class Refusing(LocalSessions):
        def delete(self, cfg, session_id):
            return "refused"

    for name in ("a", "b"):
        (workspace / "sessions" / name).mkdir(parents=True)

    result = sweep(workspace, keep=0, sessions=Refusing())

    assert result.removed == ()
    assert len(result.failures) == 2
    # Each failure names the session it belongs to, so the report is actionable.
    assert {f.split(":")[0] for f in result.failures} == {"a", "b"}
    assert all("refused" in f for f in result.failures)


def test_the_layout_names_no_genre_of_output():
    """`/reports` privileged one kind of result in the workspace structure itself."""
    assert "reports" not in LAYOUT_DIRS
    assert "reports" not in SESSION_DIRS
    assert "outputs" in SESSION_DIRS


def test_one_pass_makes_a_whole_session(tmp_path):
    """What `LocalSessions` used to finish off, and the reason this exists."""
    session = ensure_session_layout(tmp_path / "s", DEFAULT_LAYOUT)

    for name in (*SESSION_DIRS, HARNESS):
        assert (session / name).is_dir(), name


def test_turn_names_are_claimed_exclusively(workspace):
    """The one filesystem guarantee kingfisher's correctness rests on.

    `allocate_turn` is atomic *because* mkdir fails on an existing name; a shared
    filesystem that did not honour it would let two concurrent turns share a directory,
    silently restoring the defect the turn tier was built to fix.
    """
    from concurrent.futures import ThreadPoolExecutor

    dirs = LocalSessionDirs()
    runs = workspace / "scratch"
    runs.mkdir(parents=True, exist_ok=True)

    with ThreadPoolExecutor(max_workers=8) as pool:
        won = list(pool.map(lambda _: dirs.create_exclusive(runs / "t001"), range(8)))

    assert sum(won) == 1, "two callers both believed they had claimed the name"
