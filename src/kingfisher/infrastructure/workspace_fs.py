"""The filesystem, doing what `domain.layout` describes.

Everything here touches the world: mkdir, chmod, rmtree. It was in `domain/`,
where a test asserted it imported nothing from langchain and passed happily
while shelling out to git and dropping write bits off files. "No foreign
imports" is not the same boundary as "no side effects", and only the second
one makes a domain layer worth having.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from kingfisher.config import Config, ConfigError
from kingfisher.domain.layout import (
    AGENTS_SCAFFOLD,
    ARTIFACT_DIRS,
    LAYOUT_DIRS,
    MARKER,
    SESSION_DIRS,
)

#: What a catalogue is made of, named once so a caller can quote it in an error
#: rather than writing the three out again.
CATALOGUE_KINDS: tuple[str, ...] = ("skills", "subagents", "tools")


@dataclass(frozen=True)
class Catalogue:
    """Where this deployment's definitions are read from: three directories.

    A type rather than a mapping so the three names are checkable. `.skils` is
    an `unresolved-attribute` before the code runs; `["skils"]` is a `KeyError`
    while it does, and in this codebase a missing catalogue key surfaces as an
    empty catalogue -- the silent emptiness this module's neighbours keep
    refusing.

    It is not fewer lookups. `catalogue or Catalogue.from_config(cfg)` appears
    once in each of the four entry points that accept an optional one, exactly
    as the mapping did, and `build_agent` still resolves once and passes the
    result down. The count was never the problem; the anonymity was.

    Thin on purpose. It holds the directories and does not read them --
    `skill_store`, `subagent_store` and `tool_store` still take a `Path`, and
    still do the reading. Making it read as well would leave `load_all` public
    regardless, because a request's *uploaded* subagents come from the session
    rather than from here, and two ways to load a subagent that differ only in
    where they look is worse than one function called twice.

    `Config.catalogue_roots` still answers with a mapping and is deliberately
    not this type. `Config` is a record a deployment fills in, and it sits above
    the layers precisely so it never imports one; making it return a
    `Catalogue` would have it reach into `infrastructure`.
    """

    skills: Path
    subagents: Path
    tools: Path

    @classmethod
    def from_config(cls, cfg: Config) -> Catalogue:
        """The deployment's own directories, without staging anything.

        The fallback for a caller that was handed no catalogue -- `build_agent`
        called directly, `--list`, a test. One construction where there used to
        be a dict lookup per kind per call site.
        """
        roots = cfg.catalogue_roots
        return cls(skills=roots["skills"], subagents=roots["subagents"], tools=roots["tools"])


def resolve_catalogue(
    cfg: Config, supplied: Catalogue | Mapping[str, Path] | None = None
) -> Catalogue:
    """Where this deployment's definitions are read from, settled once.

    Called at construction and nowhere else, so a deployment that stages its
    catalogue from somewhere else pays for that once per `Kingfisher` rather
    than once per turn.

    The two cases differ in who owns the directories, and therefore in what a
    missing one means:

    * **Derived from `cfg`** -- kingfisher's own, so they are created. This is
      what `ensure_layout` already does for a workspace that has not relocated
      them, and doing it here extends that to one that has. `KINGFISHER_SKILLS_DIR`
      pointing somewhere that does not exist yet used to yield an empty
      catalogue and a clean start; only `skills_dir` was ever created, by
      `build_backend`, and its two siblings were not.
    * **Supplied by the caller** -- theirs, so they must already be there.
      Creating one would hide a staging failure behind a catalogue that is
      merely empty, and an agent told about no skills at all is exactly the
      silent-emptiness this module's neighbours keep refusing.

    Raises `ConfigError` either way, because a catalogue that cannot be read is
    a wiring mistake and this is the last moment it is cheap to say so.
    """
    if supplied is None:
        derived = cfg.catalogue_roots
        for path in derived.values():
            path.mkdir(parents=True, exist_ok=True)
        return Catalogue.from_config(cfg)

    # Either shape. A deployment stages directories and hands over a mapping,
    # which is the documented seam; something that already holds a `Catalogue`
    # -- another kingfisher, a test fixture -- should not have to take it apart
    # to pass it back. Both are validated the same way below.
    roots = (
        {"skills": supplied.skills, "subagents": supplied.subagents, "tools": supplied.tools}
        if isinstance(supplied, Catalogue)
        else supplied
    )

    if missing := tuple(kind for kind in CATALOGUE_KINDS if kind not in roots):
        msg = (
            f"catalogue_roots is missing {', '.join(missing)}; it names all of "
            f"{', '.join(CATALOGUE_KINDS)}, since a deployment that leaves one out "
            "means an empty one rather than the configured one"
        )
        raise ConfigError(msg)

    roots = {kind: Path(roots[kind]) for kind in CATALOGUE_KINDS}
    if absent := tuple(f"{kind} ({path})" for kind, path in roots.items() if not path.is_dir()):
        msg = (
            f"catalogue_roots names {', '.join(absent)}, which is not a directory; "
            "a supplied catalogue is staged by whoever supplies it, and kingfisher "
            "will not create one in case the staging is what failed"
        )
        raise ConfigError(msg)
    return Catalogue(**roots)


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

    return workspace


def ensure_session_layout(session_dir: Path) -> Path:
    """Create one session's layout. Idempotent.

    This directory is the backend root, so it carries the names the agent
    addresses. Two sessions share a parent and nothing else, which is what
    makes isolation structural rather than a matter of path checking.
    """
    session_dir = Path(session_dir).expanduser().resolve()
    for name in SESSION_DIRS:
        (session_dir / name).mkdir(parents=True, exist_ok=True)

    # Scaffolded rather than empty: the memory prompt directs the agent to save
    # knowledge with `edit_file`, which replaces existing text — an empty file
    # offers nothing to anchor against.
    agents_md = session_dir / "memory" / "AGENTS.md"
    if not agents_md.exists() or not agents_md.read_text(encoding="utf-8").strip():
        agents_md.write_text(AGENTS_SCAFFOLD, encoding="utf-8")

    return session_dir


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


def place_inputs(sources: tuple[Path, ...], input_dir: Path) -> tuple[str, ...]:
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
    if not checked:
        return ()

    input_dir.mkdir(exist_ok=True)
    for name, source in checked.items():
        shutil.copy(source, input_dir / name)
    return tuple(checked)


@dataclass(frozen=True)
class DataPlacement:
    """What `place_data` did. `replaced` is a subset of `placed`."""

    placed: tuple[str, ...] = ()
    replaced: tuple[str, ...] = ()


def place_data(sources: tuple[Path, ...], session_dir: Path) -> DataPlacement:
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
    if not sources:
        return DataPlacement()

    seen = _checked(sources)
    existing = {p.name for p in (Path(session_dir) / "data").glob("*")}
    with writable_data(session_dir) as data:
        for name, source in seen.items():
            shutil.copy(source, data / name)

    return DataPlacement(
        placed=tuple(seen),
        replaced=tuple(name for name in seen if name in existing),
    )
