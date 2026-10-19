"""Kingfisher's own sessions: a folder per session on this host, and the backend over it.

The core never names anything here; a deployment hands `LocalSessions` to `Kingfisher` as
it would its own.
"""

from __future__ import annotations

import re
import time
import uuid
import warnings
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from typing import Any

from deepagents.backends import CompositeBackend, FilesystemBackend

from kingfisher.core.agent.host_paths import reject_host_path
from kingfisher.core.agent.prompting import ShellPrompt
from kingfisher.core.config import Config, ConfigError
from kingfisher.core.definitions.catalogue import Definitions
from kingfisher.core.interfaces import CommandRunner, Sessions
from kingfisher.core.layout import Layout
from kingfisher.plugins.sessions.folders import (
    LocalSessionDirs,
    ensure_session_layout,
    session_bytes,
    session_dir,
)
from kingfisher.plugins.sessions.permissions import protect_inputs, writable_inputs
from kingfisher.plugins.sessions.sandbox import confinement
from kingfisher.plugins.sessions.shell import (
    ConfinedLocalShellBackend,
    fence_for,
    kingfishers_shell,
    runs_locally,
    shell_env,
    skills_view,
)


def _refuse_wording_without_runner(runner: Any, shell_prompt: ShellPrompt | None) -> None:
    """Kingfisher's own shell says what it is, and wording for it could only be wrong."""
    if runner is None and shell_prompt is not None:
        msg = (
            "shell_prompt describes the shell a runner provides, and none was given: "
            "kingfisher's own shell describes itself. Instructions for every agent go in "
            "INSTRUCTIONS.md beside the definitions, or instructions= on Kingfisher"
        )
        raise TypeError(msg)


def _once(result: Any, *, key: Callable[[Any], Any], field: str = "matches") -> Any:
    """A result with its repeated rows dropped, first occurrence kept.

    First, not last: a real directory's row carries its modification time, and a route's
    stand-in row an empty one.
    """
    rows = getattr(result, field)
    if rows is None:
        return result
    seen: set[Any] = set()
    kept = []
    for one in rows:
        identity = key(one)
        if identity in seen:
            continue
        seen.add(identity)
        kept.append(one)
    return replace(result, **{field: kept})


def _by_path(one: Mapping[str, Any]) -> Any:
    return one.get("path")


def _by_line(one: Mapping[str, Any]) -> Any:
    """A grep match's identity, which is the line and not the file."""
    return one.get("path"), one.get("line"), one.get("text")


def _directly_under(path: str, here: str) -> bool:
    """Whether `path` names something `here` holds, rather than itself or something deeper.

    The empty remainder is `here` itself, which a route would otherwise list among its
    contents.
    """
    if not path.startswith(here):
        return False
    rest = path[len(here) :].rstrip("/")
    return bool(rest) and "/" not in rest


def _listing(result: Any, path: str, routes: Iterable[tuple[str, Any]]) -> Any:
    """One directory's own entries: what it holds, each named once.

    At the root deepagents appends a stand-in row for every route, nested ones included.
    Each is moved under the directory it belongs to rather than dropped: before anything
    is written there, that row is the only sign the route is reachable.
    """
    if result.entries is None:
        return result
    here = path if path.endswith("/") else f"{path}/"
    kept = [one for one in result.entries if _directly_under(one.get("path", ""), here)]
    named = {one.get("path", "") for one in kept}
    kept += [
        {"path": prefix, "is_dir": True, "size": 0, "modified_at": ""}
        for prefix, _ in routes
        if _directly_under(prefix, here) and prefix not in named
    ]
    kept.sort(key=lambda one: one.get("path", ""))
    return _once(replace(result, entries=kept), key=lambda one: one.get("path"), field="entries")


class WorkspaceScopedBackend(CompositeBackend):
    """A `CompositeBackend` that refuses host paths instead of re-rooting them.

    `ls`, `glob` and `grep` are deduplicated: they merge every backend's answer, and
    `/inputs` and `/memory` are routes *inside* the default backend's root, so each file
    there came back twice. Each override is written for the async tool too, which an async
    turn goes through and a suite asking only the sync half cannot see.
    """

    def ls(self, path: str) -> Any:
        return _listing(super().ls(path), path, self.sorted_routes)

    async def als(self, path: str) -> Any:
        return _listing(await super().als(path), path, self.sorted_routes)

    def glob(self, pattern: str, path: str | None = None) -> Any:
        return _once(super().glob(pattern, path), key=_by_path)

    async def aglob(self, pattern: str, path: str | None = None) -> Any:
        return _once(await super().aglob(pattern, path), key=_by_path)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> Any:
        return _once(super().grep(pattern, path, glob, max_count=max_count), key=_by_line)

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> Any:
        return _once(
            await super().agrep(pattern, path, glob, max_count=max_count), key=_by_line
        )

    def __init__(
        self,
        default: Any,
        routes: dict[str, Any],
        *,
        session_dir: Path,
        workspace: Path,
        layout: Layout,
    ) -> None:
        # Where deepagents writes on its own: a tool result too long to send, the history
        # its summarizer keeps. Working material, so not `/outputs`, which would hand each
        # to the caller as an artifact, nor the default `/`, outside every folder the layout
        # names. The shell may rewrite them: nothing kingfisher reads back trusts them.
        # deepagents reads this only off a `CompositeBackend`, so it is set here.
        super().__init__(default=default, routes=routes, artifacts_root=layout.scratch_route)
        #: What a host path is refused against; not the session, which the refusal's
        #: suggestion needs separately.
        self.workspace = Path(workspace)
        self._session_dir = Path(session_dir)
        #: What `protect_inputs` could not harden, for the turn to report.
        self.unprotected: tuple[str, ...] = ()

    def _get_backend_and_key(self, key: str) -> tuple[Any, str]:
        reject_host_path(
            key, self.workspace, session_dir=self._session_dir, scratch_route=self.artifacts_root
        )
        return super()._get_backend_and_key(key)


class InputsBackend(FilesystemBackend):
    """A session's `/inputs`, which is read-only on disk and opened only for an upload.

    `upload_files` is the only way in, and the agent's file tools never reach it. Unlocked
    inside the backend rather than around the call, so `place_inputs` works on any
    backend: a remote one has no permission bits to lift.
    """

    def __init__(self, session_dir: Path, layout: Layout) -> None:
        super().__init__(root_dir=str(Path(session_dir) / layout.inputs))
        self._session_dir = Path(session_dir)
        self._layout = layout

    def upload_files(self, files: list[tuple[str, bytes]]) -> Any:
        with writable_inputs(self._session_dir, self._layout):
            return super().upload_files(files)


def _require_layout(session_dir: Path, layout: Layout) -> None:
    """Refuse a session directory that has not been made yet."""
    missing = [name for name in layout.made_dirs if not (session_dir / name).is_dir()]
    if missing:
        msg = (
            f"session directory {session_dir} is missing {', '.join(missing)}; "
            "call ensure_session_layout on it before building a backend"
        )
        raise ValueError(msg)


def backend_at(  # noqa: PLR0913 -- one keyword per thing a session's backend is built from
    cfg: Config,
    session_dir: Path,
    *,
    catalogue: Definitions | None = None,
    runner: CommandRunner | None = None,
    shell_prompt: ShellPrompt | None = None,
    shell: confinement.ShellSettings | None = None,
) -> WorkspaceScopedBackend:
    """Kingfisher's own backend, rooted at one session directory on this host.

    `shell_prompt` describes the shell `runner` provides, and is refused without one.
    Typed to the class rather than deepagents' protocol: a deployment adjusting what this
    built reads `default` and `routes` off it, and the protocol carries neither.
    """
    _refuse_wording_without_runner(runner, shell_prompt)
    definitions = catalogue or Definitions.from_config(cfg)
    skills = definitions.skills
    skills_dir = skills.root

    _require_layout(session_dir, cfg.layout)
    # The skills folder is not created where missing: a start only reads the definitions
    # folder, and a missing one means no skills. The view as well as its targets: every
    # fence checks the resolved path, and the Linux ones also have to reach the links.
    view = skills_view(skills_dir, skills.mounts, cfg.kingfisher_dir) if skills.mounts else None
    every_skills_dir = (skills_dir, *skills.mounts.values(), *((view,) if view else ()))

    settings = shell if shell is not None else confinement.ShellSettings.from_env()
    confined = confinement.shell_confinement(cfg, settings, skills=every_skills_dir)
    env = shell_env(cfg, session_dir, path_extra=settings.path_extra, catalogue=catalogue)
    if runner is None:
        chosen = fence_for(
            cfg, session_dir, confined, every_skills_dir, env, path_extra=settings.path_extra
        )
    else:
        # A supplied runner that is not local receives nothing this process applied, so
        # the confinement must stop claiming it.
        chosen = runner
        confined = confinement.with_supplied_runner(confined, local=runs_locally(runner))
    if confined.warning:
        # After the runner is settled: one that is not local withdraws the warning.
        # `stacklevel=1` so the default filter says it once per process. The sessions
        # sentence is here because most programs running kingfisher never run `doctor`.
        said = confined.warning + (
            " A workspace may hold many sessions, and each session's agent can then also "
            "read and change the other sessions' files."
        )
        warnings.warn(said, stacklevel=1)
    confined_shell = ConfinedLocalShellBackend(
        confined,
        runner=chosen,
        # The deployment's runner, not `chosen`, which on Linux is kingfisher's own fence:
        # that one is handed `env` and the session directory, a deployment's only the
        # command.
        shell_prompt=kingfishers_shell() if runner is None else shell_prompt,
        root_dir=str(session_dir),
        env=env,
        timeout=cfg.execution_timeout_s,
    )
    # Checked against the layout's routes so one forgotten here raises at build, rather
    # than resolving to the default backend and ignoring its own deny rule.
    layout = cfg.layout
    backing = {
        layout.inputs_route: lambda: InputsBackend(session_dir, layout),
        layout.skills_route: lambda: FilesystemBackend(root_dir=str(skills_dir)),
        layout.memory_route: lambda: FilesystemBackend(
            root_dir=str(session_dir / layout.memory)
        ),
        # Mounted so it can be refused, not as a way in: `read_only_permissions` denies
        # every operation, and a rule is expressible only against a routed path.
        layout.harness_route: lambda: FilesystemBackend(root_dir=str(session_dir / layout.harness)),
        # Mounted for the same reason, but readable: the agent reads a result back from
        # it, and only writing is denied.
        layout.tool_results_route: lambda: FilesystemBackend(
            root_dir=str(session_dir / layout.scratchpad / layout.tool_results)
        ),
        layout.offloads_route: lambda: FilesystemBackend(
            root_dir=str(session_dir / layout.scratchpad / layout.offloads)
        ),
    }
    missing = [path for path in layout.routed_paths() if path not in backing]
    if missing:  # pragma: no cover -- a table edit, caught by its own test
        msg = f"routes declared in kingfisher.core.layout with nothing to back them: {missing}"
        raise ConfigError(msg)

    routes: dict[str, Any] = {path: backing[path]() for path in layout.routed_paths()}
    # One per bundle and per mount, so the file tools read a subagent's or a mounted
    # party's skills too. Generated: the names are not known until a catalogue is read.
    routes.update({
        route: FilesystemBackend(root_dir=str(directory))
        for route, directory in definitions.skill_mounts()
    })

    return WorkspaceScopedBackend(
        default=confined_shell,
        routes=routes,
        session_dir=session_dir,
        workspace=cfg.workspace,
        layout=layout,
    )


class LocalSessions(Sessions):
    """Kingfisher's own `Sessions`: a directory per session under `<workspace>/sessions`,
    each backend `backend_at` that directory."""

    #: On the class as well, so a subclass whose `__init__` skips this one still opens
    #: sessions.
    _runner: Callable[[Path], CommandRunner] | None = None
    _shell_prompt: ShellPrompt | None = None
    #: How this shell is fenced. `None` reads the environment at each opening.
    shell: confinement.ShellSettings | None = None

    def __init__(
        self,
        *,
        runner: Callable[[Path], CommandRunner] | None = None,
        shell_prompt: ShellPrompt | None = None,
        shell: confinement.ShellSettings | None = None,
    ) -> None:
        # A callable only: a runner is built per session, as kingfisher's own fence is. A
        # shared instance would not know its session, would be one fence for every tenant
        # where the runner *is* the isolation, and would be called from overlapping turns.
        if runner is not None and not callable(runner):
            msg = (
                "runner is built per session, so it takes a callable: pass "
                "`lambda session_dir: your_runner` if you have one to share"
            )
            raise TypeError(msg)
        # As well as in `backend_at`, so a deployment hears it at startup, not first turn.
        _refuse_wording_without_runner(runner, shell_prompt)
        self._runner = runner
        self._shell_prompt = shell_prompt
        self.shell = shell if shell is not None else confinement.ShellSettings.from_env()

    def open(
        self,
        cfg: Config,
        session_id: str,
        /,
        *,
        catalogue: Definitions | None = None,
    ) -> WorkspaceScopedBackend:
        directory = ensure_session_layout(_where(cfg, session_id), cfg.layout)
        # Kernel-level, because the deny rule covers only the file tools. Reported by the
        # turn, not raised: one file owned by another user would end the session for good.
        unprotected = protect_inputs(directory, cfg.layout)
        runner = self._runner(directory) if self._runner is not None else None
        built = backend_at(
            cfg,
            directory,
            catalogue=catalogue,
            runner=runner,
            shell_prompt=self._shell_prompt,
            shell=self.shell,
        )
        built.unprotected = unprotected
        return built

    def sessions(self, cfg: Config) -> tuple[tuple[str, float], ...]:
        return LocalSessionDirs().listing(cfg.sessions_root)

    def mark_used(self, cfg: Config, session_id: str) -> None:
        LocalSessionDirs().mark_used(_where(cfg, session_id))

    def size(self, cfg: Config, session_id: str) -> int:
        return session_bytes(_where(cfg, session_id))

    def delete(self, cfg: Config, session_id: str) -> str | None:
        return LocalSessionDirs().remove_tree(_where(cfg, session_id))

    def where(self, cfg: Config) -> Path | None:
        """Where this keeps its sessions on this host, for the origins line.

        Exactly this class, not `isinstance`: a subclass can return its backend with
        anything changed.
        """
        return cfg.sessions_root if type(self) is LocalSessions else None

    # The turn lock is a directory: `mkdir` fails when the name is taken, where `write`
    # overwrites and two turns would both take it.

    def claim(
        self, cfg: Config, session_id: str, /, *, stale_after: float, now: float | None = None
    ) -> str | None:
        dirs, path = LocalSessionDirs(), _claim_at(cfg, session_id)
        token = uuid.uuid4().hex
        if _take(dirs, path, token):
            return token
        judged = _identity(path)
        if judged is None:
            # Released between the two looks: one more try, as for a slot never held.
            return token if _take(dirs, path, token) else None
        if self.held(cfg, session_id, stale_after=stale_after, now=now):
            return None
        # Moved aside under a name only the claim judged stale has, rather than deleted:
        # a second caller who judged the same claim renames onto that name and fails,
        # where a delete would take whatever fresh claim had replaced it.
        aside = path.with_name(f"{CLAIM}.{judged}.stale")
        try:
            path.rename(aside)
        except OSError:
            return None
        if aside.is_dir():
            # Before the slot can be taken again: an empty directory is a rename target.
            (aside / _TAKEN).touch()
        if _identity(aside) != judged:
            # Its owner released it and a new turn claimed between the two looks, which
            # only a turn outliving `stale_after` allows: that claim goes back.
            with suppress(OSError):
                aside.rename(path)
            return None
        return token if _take(dirs, path, token) else None

    def release(self, cfg: Config, session_id: str, token: str, /) -> None:
        path = _claim_at(cfg, session_id)
        if _identity(path) != token:
            # Taken over by a later turn, or never held: either way not this one's.
            return
        gone = path.with_name(f"{CLAIM}.{token}.released")
        try:
            path.rename(gone)
        except OSError:
            return
        if _identity(gone) != token:
            with suppress(OSError):
                gone.rename(path)
            return
        LocalSessionDirs().remove_tree(gone)

    def held(
        self, cfg: Config, session_id: str, /, *, stale_after: float, now: float | None = None
    ) -> bool:
        at = time.time() if now is None else now
        try:
            taken = _claim_at(cfg, session_id).stat().st_mtime
        except FileNotFoundError:
            return False
        return bool(still_held(((CLAIM, taken),), stale_after=stale_after, now=at))


def _where(cfg: Config, session_id: str) -> Path:
    """Where kingfisher's own backends keep one session of this workspace."""
    return session_dir(cfg.sessions_root, session_id)


#: The turn lock's name, a directory in the session's `.harness`.
CLAIM = "claim"

#: The file in a claim naming the turn that took it, and in a claim moved aside, that
#: it was.
_TOKEN, _TAKEN = "token", "taken"


def _take(dirs: LocalSessionDirs, path: Path, token: str) -> bool:
    """Create the claim, naming its taker; `False` where one is there."""
    if not dirs.create_exclusive(path):
        return False
    (path / _TOKEN).write_text(token, encoding="utf-8")
    return True


def _identity(path: Path) -> str | None:
    """Which claim is at `path`: its token, or `None` where there is none.

    One without a token -- written before claims had them, or by a taker that died
    between the two writes -- is named by its inode, which two callers judging it agree
    on. Not its mtime: marking a claim taken writes into it, which moves that.
    """
    try:
        token = (path / _TOKEN).read_text(encoding="utf-8")
    except OSError:
        token = ""
    if re.fullmatch(r"[0-9a-f]{32}", token):
        return token
    try:
        seen = path.stat()
    except FileNotFoundError:
        return None
    return f"untokened-{seen.st_ino}"


def still_held(
    entries: Sequence[tuple[str, float]], *, stale_after: float, now: float
) -> tuple[str, ...]:
    """Which of these claims somebody could still be holding.

    Not every claim: one left by a dead process would exempt its session from retention
    for good.
    """
    return tuple(name for name, taken in entries if now - taken < stale_after)


def _claim_at(cfg: Config, session_id: str) -> Path:
    """Where kingfisher's own sessions keep one session's turn lock."""
    return _where(cfg, session_id) / cfg.layout.harness / CLAIM
