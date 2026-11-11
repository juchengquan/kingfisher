"""A session's folders on disk: the file tools follow no link out of them, a session
whose folder is a link is not opened, and the `AGENTS.md` scaffold writes through none."""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

import pytest

from kingfisher.core.layout import DEFAULT_LAYOUT, HARNESS, TURNS_FILE
from kingfisher.plugins import LocalSessions, backend_at
from tests.unit.plugins.sessions.conftest import CONVERSATION, keep_conversation

DEFAULT = "default"


def _planted(cfg, at: str, points_to: str):
    """The default session's backend, with a link at `at` pointing to `points_to`, both
    paths inside the session -- what a shell may make on every fence."""
    backend = LocalSessions().open(cfg, DEFAULT)
    session = cfg.workspace / "sessions" / DEFAULT
    (session / at).symlink_to(session / points_to)
    return backend, session


#: Each file tool that opens a path, by name.
FILE_TOOLS = {
    "write": lambda b, p: b.write(p, "forged\n"),
    "edit": lambda b, p: b.edit(p, "You help", "forged"),
    "read": lambda b, p: b.read(p),
    "download": lambda b, p: b.download_files([p])[0],
    "upload": lambda b, p: b.upload_files([(p, b"forged\n")])[0],
}


@pytest.mark.parametrize("tool", FILE_TOOLS)
@pytest.mark.parametrize("folder", ["scratchpad", "outputs"])
def test_the_file_tools_follow_no_link_into_the_harness(cfg, folder, tool):
    """The file tools run outside every fence, and the `/.harness` rule judged the path
    typed: `edit("/scratchpad/link")` rewrote the file in `.harness` the link pointed at.
    """
    backend, _ = _planted(cfg, f"{folder}/link", f"{HARNESS}/{TURNS_FILE}")
    kept = keep_conversation(cfg.workspace / "sessions" / DEFAULT)

    answer = FILE_TOOLS[tool](backend, f"/{folder}/link")

    assert answer.error, f"{tool} went through the link"
    assert getattr(answer, "content", None) is None
    assert kept.read_text(encoding="utf-8") == CONVERSATION


def test_the_file_tools_follow_no_link_into_the_saved_results(cfg):
    """A link to a saved result let `edit` forge what a later turn reads back as a tool's
    answer, under a write rule that judged only `/scratchpad/...`."""
    backend, session = _planted(cfg, "scratchpad/link", "scratchpad/tool_results/r1.txt")
    result = session / "scratchpad" / "tool_results" / "r1.txt"
    result.write_text("TRUE RESULT", encoding="utf-8")

    assert backend.edit("/scratchpad/link", "TRUE RESULT", "FORGED").error
    assert result.read_text(encoding="utf-8") == "TRUE RESULT"


def test_a_walk_names_no_link_and_reads_through_none(cfg):
    """`ls` named a link by its target's path, and grep's own walk read through one. The
    ordinary file beside it is the control: a walk that found nothing would pass."""
    backend, session = _planted(cfg, "scratchpad/link", f"{HARNESS}/{TURNS_FILE}")
    keep_conversation(cfg.workspace / "sessions" / DEFAULT)
    (session / "scratchpad" / "own.txt").write_text(CONVERSATION, encoding="utf-8")
    shell = backend.default

    listed = {e["path"] for e in backend.ls("/scratchpad").entries or [] if not e["is_dir"]}
    globbed = {m["path"] for m in backend.glob("*", "/scratchpad").matches or []}
    # The walk ripgrep stands in for where it is missing, which follows a file link.
    found, _, _ = shell._python_search("You help", shell._resolve_path("/scratchpad"), None)

    assert listed == globbed == set(found) == {"/scratchpad/own.txt"}


@pytest.mark.parametrize("dangling", [False, True], ids=["to a folder", "to nothing"])
@pytest.mark.parametrize("folder", DEFAULT_LAYOUT.unlinked_dirs)
def test_a_session_whose_folder_is_a_link_is_refused_at_open(cfg, tmp_path, folder, dangling):
    """A `memory` replaced by a link rooted the `/memory/` route wherever it pointed, so
    the file tools read and wrote the host and the scaffold wrote `AGENTS.md` there; and a
    link left where `scratchpad/blobs`, which nothing makes, would be let `read_file` read
    the host the next turn. Dangling too: the route is rooted at the target, whatever later
    makes it."""
    LocalSessions().open(cfg, DEFAULT)
    session = cfg.workspace / "sessions" / DEFAULT
    outside = tmp_path / "outside"
    if not dangling:
        outside.mkdir()
    moved = session / folder
    if moved.exists():
        # `inputs` is read-only, and moving a folder to another parent rewrites its `..`.
        moved.chmod(0o755)
        moved.rename(tmp_path / "aside")
    moved.symlink_to(outside)

    with pytest.raises(ValueError, match="link"):
        LocalSessions().open(cfg, DEFAULT)
    with pytest.raises(ValueError, match="link"):
        backend_at(cfg, session)
    assert not outside.exists() if dangling else list(outside.iterdir()) == []


@pytest.mark.parametrize(
    "target", ["outside, missing", "outside, empty", "into .harness"],
)
def test_the_scaffold_writes_through_no_link_left_at_agents_md(cfg, tmp_path, target):
    """The scaffold runs in kingfisher's process, outside every fence, and followed a link
    the shell left at `memory/AGENTS.md`: it made a file outside the workspace, filled an
    empty one, and planted `paused.state` in `.harness`. The session still opens."""
    LocalSessions().open(cfg, DEFAULT)
    session = cfg.workspace / "sessions" / DEFAULT
    pointed = {
        "outside, missing": tmp_path / "planted.txt",
        "outside, empty": tmp_path / "empty.txt",
        "into .harness": session / HARNESS / "paused.state",
    }[target]
    if target == "outside, empty":
        pointed.write_text("", encoding="utf-8")
    agents_md = session / "memory" / "AGENTS.md"
    agents_md.unlink()
    agents_md.symlink_to(pointed)

    LocalSessions().open(cfg, DEFAULT)

    assert agents_md.is_symlink(), "the link is left where it is"
    if target == "outside, empty":
        assert pointed.read_text(encoding="utf-8") == ""
    else:
        assert not pointed.exists()


@pytest.mark.parametrize("by", ["the shell", "the file tools"])
def test_a_folder_at_agents_md_is_left_and_the_session_still_opens(cfg, by):
    """`read_text` on a folder there raised at every open, so a session whose agent filed its
    memory under `/memory/AGENTS.md/` -- the file tools alone can -- never ran again. Left,
    not removed: what is in it is the agent's."""
    backend = LocalSessions().open(cfg, DEFAULT)
    session = cfg.workspace / "sessions" / DEFAULT
    agents_md = session / "memory" / "AGENTS.md"
    if by == "the shell":
        agents_md.unlink()
        agents_md.mkdir()
        (agents_md / "notes.md").write_text("filed by topic", encoding="utf-8")
    else:
        assert backend.delete("/memory/AGENTS.md").error is None
        assert backend.write("/memory/AGENTS.md/notes.md", "filed by topic").error is None

    LocalSessions().open(cfg, DEFAULT)
    LocalSessions().open(cfg, DEFAULT)

    assert (agents_md / "notes.md").read_text(encoding="utf-8") == "filed by topic"


@pytest.mark.parametrize("seen", ["missing", "a regular file"])
def test_an_agents_md_swapped_for_a_link_after_the_check_is_not_written_through(
    cfg, tmp_path, monkeypatch, seen
):
    """The check and the write are two calls, and the shell runs beside the open: the flags
    refuse a link themselves, so one made in between is not followed. Driven by having the
    check see what was there a moment before."""
    LocalSessions().open(cfg, DEFAULT)
    session = cfg.workspace / "sessions" / DEFAULT
    agents_md = session / "memory" / "AGENTS.md"
    pointed = tmp_path / "pointed.txt"
    if seen == "a regular file":
        pointed.write_text("", encoding="utf-8")
    regular = tmp_path / "regular.md"
    regular.write_text("", encoding="utf-8")
    agents_md.unlink()
    agents_md.symlink_to(pointed)
    lstat = Path.lstat

    def before_the_swap(path: Path) -> os.stat_result:
        if path != agents_md:
            return lstat(path)
        if seen == "missing":
            raise FileNotFoundError(path)
        return lstat(regular)

    monkeypatch.setattr(Path, "lstat", before_the_swap)
    LocalSessions().open(cfg, DEFAULT)

    if seen == "a regular file":
        assert pointed.read_text(encoding="utf-8") == ""
    else:
        assert not pointed.exists()


def test_an_agents_md_made_after_the_check_is_not_written_over(cfg, monkeypatch):
    """The agent saving memory between the check and the create: without `O_EXCL` the
    scaffold opened its file and wrote over the first of what it held."""
    LocalSessions().open(cfg, DEFAULT)
    agents_md = cfg.workspace / "sessions" / DEFAULT / "memory" / "AGENTS.md"
    agents_md.write_text("- the build runs on Fridays\n", encoding="utf-8")
    lstat = Path.lstat

    def before_it_was_made(path: Path) -> os.stat_result:
        if path == agents_md:
            raise FileNotFoundError(path)
        return lstat(path)

    monkeypatch.setattr(Path, "lstat", before_it_was_made)
    LocalSessions().open(cfg, DEFAULT)

    assert agents_md.read_text(encoding="utf-8") == "- the build runs on Fridays\n"


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root reads it")
def test_an_agents_md_the_open_cannot_read_is_left_alone(cfg):
    """`chmod 000 memory/AGENTS.md` is the shell's to run, and a scaffold that took a file
    it could not read for an empty one raised at every open after."""
    LocalSessions().open(cfg, DEFAULT)
    agents_md = cfg.workspace / "sessions" / DEFAULT / "memory" / "AGENTS.md"
    agents_md.chmod(0)
    try:
        LocalSessions().open(cfg, DEFAULT)
    finally:
        agents_md.chmod(0o644)

    assert agents_md.read_text(encoding="utf-8").startswith("# Project memory")


def test_an_empty_agents_md_is_scaffolded_and_one_with_text_is_kept(cfg):
    """The control for the link, folder and swap tests: refusing every file but a missing
    one would pass them, and leave an emptied memory with no text for `edit_file` to
    anchor against."""
    LocalSessions().open(cfg, DEFAULT)
    agents_md = cfg.workspace / "sessions" / DEFAULT / "memory" / "AGENTS.md"
    agents_md.write_text("  \n", encoding="utf-8")
    LocalSessions().open(cfg, DEFAULT)
    assert agents_md.read_text(encoding="utf-8").startswith("# Project memory")

    agents_md.write_text("- the build runs on Fridays\n", encoding="utf-8")
    LocalSessions().open(cfg, DEFAULT)
    assert agents_md.read_text(encoding="utf-8") == "- the build runs on Fridays\n"


def test_every_folder_a_route_is_rooted_at_in_the_session_may_not_be_a_link(cfg):
    """A route mounted inside the session but missing from `unlinked_dirs` is rooted through
    whatever link the shell left there, as `scratchpad/blobs` was after #1036 mounted it.
    Read off the routes the backend actually built, so a new one needs no one to remember."""
    backend = LocalSessions().open(cfg, DEFAULT)
    session = (cfg.workspace / "sessions" / DEFAULT).resolve()
    rooted = {
        Path(inner.cwd).relative_to(session).as_posix()
        for inner in backend.routes.values()
        if Path(inner.cwd).is_relative_to(session)
    }

    # The control: an unmade folder is among them, or the rule could not tell.
    assert {"scratchpad/blobs", "memory"} <= rooted
    assert rooted <= set(DEFAULT_LAYOUT.unlinked_dirs)



def _gone_once_seen(monkeypatch, removed: Path) -> None:
    """Have `removed` deleted right after its first look, as a sweep or the session's own
    shell can delete it between two looks."""
    look = Path.stat

    def then_gone(path: Path, **kwargs) -> os.stat_result:
        found = look(path, **kwargs)
        if path == removed:
            shutil.rmtree(removed) if stat.S_ISDIR(found.st_mode) else removed.unlink()
        return found

    monkeypatch.setattr(Path, "stat", then_gone)


def test_a_session_deleted_while_the_sessions_are_listed_is_left_out(cfg, monkeypatch):
    """A session reaped between the listing and its look raised `FileNotFoundError` out of
    the listing, which every turn asks first, so a turn in another session failed."""
    sessions = LocalSessions()
    sessions.open(cfg, "kept")
    sessions.open(cfg, "reaped")
    _gone_once_seen(monkeypatch, cfg.workspace / "sessions" / "reaped")

    assert "kept" in {name for name, _ in sessions.sessions(cfg)}


def test_a_file_removed_while_a_session_is_measured_is_not_counted(cfg, monkeypatch):
    """A file the session's shell removed mid-walk raised out of `size`."""
    sessions = LocalSessions()
    sessions.open(cfg, DEFAULT)
    scratch = cfg.workspace / "sessions" / DEFAULT / "scratchpad" / "build.tmp"
    scratch.write_bytes(b"x" * 10)
    _gone_once_seen(monkeypatch, scratch)

    assert sessions.size(cfg, DEFAULT) >= 0
