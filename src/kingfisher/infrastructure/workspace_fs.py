"""The filesystem, doing what `domain.layout` describes.

Everything here touches the world: mkdir, chmod, rmtree. It was in `domain/`,
where a test asserted it imported nothing from langchain and passed happily
while shelling out to git and dropping write bits off files. "No foreign
imports" is not the same boundary as "no side effects", and only the second
one makes a domain layer worth having.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from kingfisher.domain.layout import (
    AGENTS_SCAFFOLD,
    ARTIFACT_DIRS,
    LAYOUT_DIRS,
    MARKER,
    SESSION_DIRS,
    SESSION_PLUMBING,
)
from kingfisher.domain.references import within
from kingfisher.domain.session import sessions_root

#: Where the catalogue example sits, as an import path rather than a filesystem
#: one -- an installed package is not in this repository's directory tree.
PACKAGE = "kingfisher"

#: The worked example of the one file a deployment *must* write. It lived at the
#: repository root once, which meant it existed only in a checkout: `packages =
#: ["src/kingfisher"]`, so anything one level up is not in the wheel. That is the
#: mistake `test_the_package_ships_the_catalogue_example` guards against, made
#: for the file a new deployment needs first.
#:
#: Read from here by `model_catalogue`, which names it in the error a deployment
#: without a `models.yaml` hits. It moved out of `seeding` with the code that
#: writes it: the example is workspace furniture, and seeding is about to become
#: able to refuse.
EXAMPLE = "models.yaml.example"


#: What a catalogue is made of, named once so a caller can quote it in an error
#: rather than writing the three out again.
class LocalSessionDirs:
    """`SessionDirs` over the real filesystem.

    `create_exclusive` deliberately does *not* pass `exist_ok`: the domain's
    turn allocation is correct only because a taken name fails here.
    """

    def ensure(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)

    def create_exclusive(self, path: Path) -> bool:
        try:
            path.mkdir()
        except FileExistsError:
            return False
        return True

    def mark_used(self, path: Path) -> None:
        # `exist_ok` because this records use of something that already exists;
        # a session that has gone is not an error here, it is the sweep winning
        # a race, and the turn will fail on its own next read.
        with suppress(OSError):
            path.touch(exist_ok=True)

    def children(self, path: Path) -> tuple[str, ...]:
        if not path.is_dir():
            return ()
        return tuple(p.name for p in path.iterdir() if p.is_dir())

    def listing(self, path: Path) -> tuple[tuple[str, float], ...]:
        if not path.is_dir():
            return ()
        return tuple((p.name, p.stat().st_mtime) for p in path.iterdir() if p.is_dir())

    def remove_tree(self, path: Path) -> str | None:
        try:
            # not ignore_errors: partials must surface
            shutil.rmtree(path, onexc=_unlock_and_retry)
        except OSError as exc:
            return f"directory not removed ({exc.strerror})"
        return None


def is_new_workspace(workspace: Path) -> bool:
    """True when this path has never been used as a workspace.

    Surfaced by callers so a silently relocated workspace — an unstable `~`,
    a changed env var — reads as "created new" rather than as a first run.
    """
    return not (Path(workspace) / MARKER).exists()


def ensure_layout(workspace: Path) -> Path:
    """Create the workspace layout. Idempotent.

    What the workspace still owns is what sessions share — the skill and
    subagent definitions — plus the directory sessions live in. Everything the
    agent addresses belongs to a session and is made by `ensure_session_layout`.

    No `.gitignore` is written. Kingfisher ran git once -- a `pre_run_commit`
    that snapshotted the tracked tier before each turn -- and that went with
    `adapters/workspace_git.py`. What was left behind was an ignore file for a
    repository nothing created, nothing wrote to and nothing read, describing a
    review workflow the code no longer had.

    Worse than merely unused: it listed two of the five things a workspace
    holds, so it read as complete while being wrong. `Library/` was outside it,
    and a `git add -A` in a real workspace offered to commit a 21MB pip cache.
    An operator who wants their workspace under version control is better served
    writing the ignore rules they actually want than inheriting stale ones.

    A workspace is runtime state. The 132KB of authored content in it --
    `skills`, `subagents`, `tools` against 256MB of sessions and harness state --
    is what `KINGFISHER_SKILLS_DIR` and its two siblings exist to relocate, and
    versioning belongs there rather than around the sessions.
    """
    workspace = Path(workspace).expanduser().resolve()
    for name in LAYOUT_DIRS:
        (workspace / name).mkdir(parents=True, exist_ok=True)

    marker = workspace / MARKER
    if not marker.exists():
        marker.write_text("kingfisher workspace\n", encoding="utf-8")

    _place_example(workspace)
    return workspace


def _place_example(workspace: Path) -> None:
    """Put the catalogue example where `models.yaml` is read from.

    Here rather than in `seed`, which is where it lived. Seeding is about to
    become able to *refuse* -- a deployment that names no definitions has
    nothing to copy -- and this file must arrive anyway: `models.yaml` is
    required and has no fallback, and the error a deployment without one hits
    names this file as the place to look. Seeding's own comment said as much,
    that it is written "not conditional on a deployment having any", and that
    stops being true the moment seeding can decline.

    Laying out a workspace is the right owner because this is furniture rather
    than content. Nothing chooses it, nothing seeds it from somewhere else, and
    a workspace without it is missing a part of itself.

    Written when absent *or different*, which is neither of the two obvious
    rules. Always writing would touch the disk on every run for nothing. Only
    when absent would mean an upgrade never refreshed the example, so a
    deployment would keep reading last year's annotations for a file that had
    grown fields -- and re-seeding used to be what refreshed it.

    As `.example`, never as `models.yaml` itself: the one file that must not be
    overwritten is the one naming every endpoint this deployment reaches and
    whose credentials pay.
    """
    source = resources.files(PACKAGE).joinpath(EXAMPLE)
    if not source.is_file():  # a packaging fault, caught by a test
        return
    target = workspace / EXAMPLE
    text = source.read_text(encoding="utf-8")
    if target.is_file() and target.read_text(encoding="utf-8") == text:
        return
    target.write_text(text, encoding="utf-8")


def ensure_session_layout(session_dir: Path) -> Path:
    """Create one session's layout. Idempotent.

    This directory is the backend root, so it carries the names the agent
    addresses. Two sessions share a parent and nothing else, which is what
    makes isolation structural rather than a matter of path checking.

    Both lists, because a session needs its plumbing as much as its addressed
    names and only one of the two was written down. `build_backend` used to make
    `.home` and `skills/uploaded` itself, and `data` and `memory` a second time
    -- so nothing created a whole session in one pass, and the names lived in
    two places that could disagree.
    """
    session_dir = Path(session_dir).expanduser().resolve()
    for name in (*SESSION_DIRS, *SESSION_PLUMBING):
        (session_dir / name).mkdir(parents=True, exist_ok=True)

    # Scaffolded rather than empty: the memory prompt directs the agent to save
    # knowledge with `edit_file`, which replaces existing text — an empty file
    # offers nothing to anchor against.
    agents_md = session_dir / "memory" / "AGENTS.md"
    if not agents_md.exists() or not agents_md.read_text(encoding="utf-8").strip():
        agents_md.write_text(AGENTS_SCAFFOLD, encoding="utf-8")

    return session_dir


class LocalSessionRoot:
    """A session's directory, on this machine, staying where it is.

    What kingfisher did before there was a port for it, written down so that
    the port has an implementation rather than only a promise. `hold` creates
    nothing and releases nothing: the directory outlives the turn, and the
    session store is what makes that survivable rather than required.

    A provider whose root really is per-turn -- a mount, a volume -- does its
    work in the two halves this one leaves empty.
    """

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)

    @contextmanager
    def hold(self, session_id: str) -> Iterator[Path]:
        yield sessions_root(self.workspace) / session_id


def collect_artifacts(session_dir: Path) -> tuple[str, ...]:
    """What this session holds that is worth keeping, as relative paths.

    Present rather than changed. `execute` writes without any file tool seeing
    it -- running a script is how most of `/derived` is produced -- so the only
    sound view is the filesystem's, and the two ways to turn that into a change
    list are both worse: mtime and size can collide, which loses work silently,
    and hashing every file each turn costs the size of `/derived`.

    A caller persisting incrementally diffs this against the previous turn's
    manifest, which it already holds. That diff also names what was *deleted*,
    which a list of changes could not.

    Directories are omitted: an empty one carries nothing to persist, and it
    reappears when its files are restored.
    """
    session_dir = Path(session_dir)
    found: list[str] = []
    for name in ARTIFACT_DIRS:
        root = session_dir / name
        if not root.is_dir():
            continue
        found.extend(
            str(path.relative_to(session_dir)) for path in root.rglob("*") if path.is_file()
        )
    return tuple(sorted(found))


def session_bytes(session_dir: Path) -> int:
    """How much disk one session is holding, across everything in it.

    Everything, not just the artifact directories: run scratch counts, because
    the point is what the session costs the host rather than what is worth
    keeping. Read before a turn starts and never during -- `execute` writes
    without any file tool seeing it, so there is nothing to intercept mid-turn,
    and a filesystem quota is the only thing that could.
    """
    session_dir = Path(session_dir)
    if not session_dir.is_dir():
        return 0
    return sum(p.stat().st_size for p in session_dir.rglob("*") if p.is_file())


def _drop_write_bits(path: Path) -> None:
    path.chmod(path.stat().st_mode & ~0o222)


def _add_write_bits(path: Path) -> None:
    path.chmod(path.stat().st_mode | 0o200)


def _unlock_and_retry(func: Callable[[str], object], path: str, exc: BaseException) -> None:
    """Undo `protect_data` for one path so a sweep can finish, then retry it.

    `protect_data` drops the write bit off `data/` and everything under it, and
    deletion is governed by the *directory's* write bit rather than the file's.
    So a session that was ever given `--data` could not be removed at all: every
    reap failed on it with `Permission denied`, reported the failure, and left
    it on disk to fail again on the next sweep. Sessions that never received
    data swept fine, which is why this stayed invisible.

    Chmod belongs to this module -- `place_data` says so, and the reason is that
    reaching for `sudo` when a directory refused is what once put root-owned
    files in a workspace. This is the same unlock `writable_data` does, for the
    one other operation that needs it.

    Only `PermissionError` is handled, and only by retrying once. Anything else,
    including a path we do not own and therefore cannot chmod, is re-raised for
    `remove_tree` to report -- degrading the same way `protect_data` does, since
    a file owned by someone else was never ours to delete.
    """
    if not isinstance(exc, PermissionError):
        raise exc
    try:
        _add_write_bits(Path(path).parent)
    except OSError:
        raise exc from None
    func(path)


def _unreachable(path: Path, error: OSError) -> str:
    """One path we were not allowed to touch, said in one line."""
    return f"{path.name}: {error.strerror or error}"


def protect_data(session_dir: Path) -> tuple[str, ...]:
    """Make `data/` read-only at the OS level. Idempotent.

    Returns a description of every path whose mode could not be changed, and
    an empty tuple when all of them were.

    This is the layer the tool-level deny rule cannot provide: the kernel
    enforces it against `execute` too, and filesystem permissions in deepagents
    are applied only to the built-in file tools. Directories are included
    because deletion is governed by the *directory's* write bit, not the file's.

    A path we cannot chmod is reported, not raised. `chmod` refuses anyone who
    does not own the file, so a single input copied in by another user -- a
    `sudo` run, a file restored from a backup -- used to abort the run. And it
    aborted *every* run of that session afterwards, because this happens before
    anything else, which made one root-owned file a permanent brick.

    Degrading is safe in the case that actually arises: a file owned by someone
    else is one this process cannot write either, so the mode change being
    skipped was never what protected it. Where it is not safe, the deny rule is
    still in force and the caller is told which paths are bare.
    """
    data = Path(session_dir) / "data"
    if not data.is_dir():
        return ()

    # Children first, then the directory itself.
    failures = []
    for path in (*sorted(data.rglob("*"), reverse=True), data):
        try:
            _drop_write_bits(path)
        except OSError as exc:
            failures.append(_unreachable(path, exc))
    return tuple(failures)


@contextmanager
def writable_data(session_dir: Path) -> Iterator[Path]:
    """Temporarily make `data/` writable, for loading inputs.

        with writable_data(session.directory) as data:
            shutil.copy(source, data / "sales.csv")

    Takes the session, not the workspace. `/data` is per-session -- one
    caller's data, isolated by being a different root rather than by a path
    check -- and this argument was called `workspace` for long enough that the
    smoke seeded `workspace/data`, where no agent would ever look.

    The directory itself must become writable or there is nowhere to put the
    inputs, so a failure there is raised. Existing files are best-effort for
    the same reason `protect_data` is: one we do not own is one we could not
    have overwritten anyway, and refusing to accept a new input because an
    unrelated old one is someone else's would be its own bug.
    """
    data = Path(session_dir) / "data"
    data.mkdir(parents=True, exist_ok=True)
    _add_write_bits(data)
    for path in data.rglob("*"):
        with suppress(OSError):
            _add_write_bits(path)
    try:
        yield data
    finally:
        protect_data(session_dir)


class DataError(ValueError):
    """A caller-supplied file cannot be placed, in `/data` or in a turn's input."""


def _checked(sources: tuple[Path, ...]) -> dict[str, Path]:
    """Every source keyed by the name it will land under.

    Raises before anything is copied, so a request that names a file twice or
    names one that is not there leaves nothing half-placed behind.

    Shared by both destinations deliberately. Every rule here is about what the
    caller asked for rather than about where it goes, so applying them to
    `/data` and not to a turn's input was an accident of which one happened to
    be written first -- and it cost the second one both guarantees.
    """
    seen: dict[str, Path] = {}
    for source in sources:
        name = Path(source).name
        if name in {"", ".", ".."}:
            msg = f"{source}: has no filename to place it under"
            raise DataError(msg)
        if not Path(source).is_file():
            msg = f"{source}: no such file"
            raise DataError(msg)
        if name in seen:
            # Keeping the last one silently loses a file the caller asked for.
            msg = f"{name}: supplied twice, from {seen[name]} and {source}"
            raise DataError(msg)
        seen[name] = Path(source)
    return seen


def check_placeable(sources: tuple[Path, ...]) -> None:
    """Raise if these files could not be placed, without placing them.

    The copying half of `place_inputs` has to happen after a turn directory
    exists, because that is where the files go. The *refusing* half must happen
    before, or a typo leaves a turn behind -- which it did: `--data` named a
    missing file and left nothing, `--input` named one and left `t001`, against
    a docstring promising neither would.

    So the check is callable on its own and the service runs it while it is
    still allowed to reject. `place_inputs` repeats it rather than trusting a
    caller to have asked, which costs three `is_file` calls and keeps the
    function safe to call directly.
    """
    _checked(sources)


def place_inputs(
    sources: tuple[Path, ...],
    input_dir: Path,
    *,
    contents: Mapping[str, bytes] | None = None,
) -> tuple[str, ...]:
    """Copy a turn's supplied files into its `input/`, and name what landed.

    The transient counterpart to `place_data`: these belong to one turn, where
    `/data` survives into the next. That is the only difference, and it is why
    there is no `writable_data` dance here -- a turn directory is ours and was
    made moments ago.

    This lived inline in the service as a `mkdir` and a bare `shutil.copy`, one
    layer up from where the filesystem is supposed to be touched. It was the
    only place in the application layer doing its own I/O, and it had silently
    missed both of `_checked`'s guarantees for as long as it existed.
    """
    checked = _checked(sources)
    if not checked and not contents:
        return ()

    input_dir.mkdir(exist_ok=True)
    for name, source in checked.items():
        shutil.copy(source, input_dir / name)
    # Fetched by id rather than read from a path, and written here for the same
    # reason the copies are: a turn's input directory is ours and was made
    # moments ago. `within` is what makes a store-supplied key safe to join.
    for name, content in (contents or {}).items():
        within(input_dir, name).write_bytes(content)
    return tuple(checked) + tuple(contents or ())


@dataclass(frozen=True)
class DataPlacement:
    """What `place_data` did. `replaced` is a subset of `placed`."""

    placed: tuple[str, ...] = ()
    replaced: tuple[str, ...] = ()


def place_data(
    sources: tuple[Path, ...],
    session_dir: Path,
    *,
    contents: Mapping[str, bytes] | None = None,
) -> DataPlacement:
    """Copy caller-supplied files into a session's `/data`, and re-harden it.

    The durable counterpart to a turn's `input/`: these survive the turn and
    are there on the next one. That is the whole distinction, and the only
    reason both exist.

    Written through `writable_data`, whose `finally` drops the write bits
    again -- including when a copy raises. Nothing outside this module should
    ever chmod `/data`, because reaching for `sudo` when the directory refused
    a copy is what put root-owned files in a workspace and made one session
    unusable for good.

    Everything is checked before anything is copied -- see `_checked`, which
    the turn's input directory now shares.
    """
    if not sources and not contents:
        return DataPlacement()

    seen = _checked(sources)
    arriving = tuple(seen) + tuple(contents or ())
    existing = {p.name for p in (Path(session_dir) / "data").glob("*")}
    # One `writable_data` block for both, not two. Its `finally` drops the write
    # bits again, and taking them twice would mean a window between the two
    # where `/data` is writable for no reason.
    with writable_data(session_dir) as data:
        for name, source in seen.items():
            shutil.copy(source, data / name)
        for name, content in (contents or {}).items():
            within(data, name).write_bytes(content)

    return DataPlacement(
        placed=arriving,
        replaced=tuple(name for name in arriving if name in existing),
    )


#: Where a session keeps the agent it opened with, under `state_dir`.
#:
#: That root is the one place the agent itself never addresses. A run able to
#: rewrite this could change the instructions it is running under, halfway
#: through the conversation those instructions produced.
AGENT_SNAPSHOTS = "agents"


def agent_snapshot(state_dir: Path, session_id: str) -> Path:
    """The path a session's agent definition is kept at."""
    return Path(state_dir) / AGENT_SNAPSHOTS / f"{session_id}.yaml"


def remember_agent(state_dir: Path, session_id: str, document: str) -> None:
    """Keep the agent definition this session opened with.

    Written once and never rewritten. A later turn naming the same agent must be
    built from what the session started with rather than from whatever the file
    says by then -- a deploy mid-conversation is ordinary, and an agent's prompt
    changing under a history that already happened is not.

    The document rather than the parsed spec: there is a reader for one already,
    and a file somebody can diff against the catalogue is worth more than a
    serialisation nobody else reads.
    """
    path = agent_snapshot(state_dir, session_id)
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")


def agent_started_with(state_dir: Path, session_id: str) -> str | None:
    """The agent document this session opened with, or `None` if it kept none.

    `None` covers two ordinary cases and no surprising ones: a session opened
    before agents existed, and a deployment whose repository cannot hand over
    the document it parsed.
    """
    path = agent_snapshot(state_dir, session_id)
    return path.read_text(encoding="utf-8") if path.is_file() else None


@dataclass(frozen=True)
class MemoryBacking:
    """What is underneath a workspace, for a deployment that must keep nothing.

    Facts rather than a verdict. `kingfisher doctor` decides what they mean; this
    reads them, because a deployment that has asserted "nothing at rest on this
    machine" is relying on a configuration nothing in the process can see.

    Measured, and the arrangement is not what the obvious reading predicts. A
    memory filesystem *larger* than the container's memory limit does not fail
    when it fills: the kernel swaps its pages out. That is data at rest, arrived
    at silently, with the write succeeding and no error anywhere. With swap off
    the same overrun becomes an OOM kill, which takes every session in the
    container. Only when the filesystem is smaller than the limit does a full
    one give a clean `ENOSPC` -- which is a thing kingfisher can refuse on.

    `None` where the question cannot be asked: not Linux, no cgroup, or a
    workspace on an ordinary disk where none of this applies.
    """

    #: The filesystem type under the workspace, e.g. `tmpfs`, `ext4`, `apfs`.
    filesystem: str | None = None
    #: Its total size in bytes.
    size_bytes: int | None = None
    #: What this process is allowed to use, from the cgroup.
    limit_bytes: int | None = None
    #: Whether the cgroup permits swapping. `True` is the dangerous answer.
    swap_enabled: bool | None = None

    @property
    def in_memory(self) -> bool:
        """Whether the workspace is on a memory filesystem at all."""
        return self.filesystem in {"tmpfs", "ramfs"}

    @property
    def fits(self) -> bool | None:
        """Whether the filesystem is small enough to fill without killing this.

        `None` when either number is unknown, which is not the same as `False`
        and must not be reported as one.
        """
        if self.size_bytes is None or self.limit_bytes is None:
            return None
        return self.size_bytes < self.limit_bytes


def _mounted_filesystem(path: Path) -> str | None:
    """The filesystem type under `path`, from `/proc/mounts`.

    The longest matching mount point wins, because `/` matches everything and a
    workspace is almost always under something more specific.
    """
    mounts = Path("/proc/mounts")
    if not mounts.is_file():
        return None
    best: tuple[int, str] | None = None
    with suppress(OSError):
        for line in mounts.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split()
            if len(parts) < 3:  # noqa: PLR2004 -- device, mountpoint, type
                continue
            point, kind = parts[1], parts[2]
            under = str(path) == point or str(path).startswith(point.rstrip("/") + "/")
            if under and (best is None or len(point) > best[0]):
                best = (len(point), kind)
    return best[1] if best else None


def _cgroup_number(name: str) -> int | None:
    """One cgroup v2 value, or `None` for absent, `max`, or unreadable."""
    raw = Path("/sys/fs/cgroup") / name
    if not raw.is_file():
        return None
    with suppress(OSError, ValueError):
        text = raw.read_text(encoding="utf-8").strip()
        return None if text == "max" else int(text)
    return None


def memory_backing(workspace: Path) -> MemoryBacking:
    """Read what is underneath this workspace, as far as the platform will say.

    Everything here is absent outside Linux and outside a container, and an
    all-`None` answer is the honest one rather than a failure: a laptop is not
    misconfigured for not being a cgroup.
    """
    workspace = Path(workspace)
    swap = _cgroup_number("memory.swap.max")
    return MemoryBacking(
        filesystem=_mounted_filesystem(workspace),
        size_bytes=_size_of(workspace),
        limit_bytes=_cgroup_number("memory.max"),
        # `memory.swap.max` of 0 is swapping disabled; any other number, or the
        # file being absent on a host that has swap, permits it.
        swap_enabled=None if not Path("/sys/fs/cgroup/memory.swap.max").is_file() else swap != 0,
    )


def _size_of(path: Path) -> int | None:
    """The total size of the filesystem holding `path`."""
    with suppress(OSError):
        stat = os.statvfs(path)
        return stat.f_blocks * stat.f_frsize
    return None
