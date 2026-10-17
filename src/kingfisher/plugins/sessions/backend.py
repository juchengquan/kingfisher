"""Kingfisher's own sessions: a folder per session on this host, and the backend over
it. The shell that backend runs, and the fences around it, are `shell`'s.

The core never names anything here. A deployment hands `LocalSessions` to
`Kingfisher` as it would its own.
"""

from __future__ import annotations

import time
import warnings
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from deepagents.backends import CompositeBackend, FilesystemBackend

from kingfisher.core.agent.backend import skill_mounts
from kingfisher.core.agent.host_paths import reject_host_path
from kingfisher.core.agent.prompting import ShellPrompt, kingfishers_shell
from kingfisher.core.config import Config, ConfigError
from kingfisher.core.definitions.catalogue import Definitions, refuse_unmountable
from kingfisher.core.interfaces import CommandRunner, Sessions
from kingfisher.core.layout import CLAIM, DEFAULT_LAYOUT
from kingfisher.core.session.model import session_dir, still_held
from kingfisher.plugins.sessions.folders import (
    LocalSessionDirs,
    ensure_session_layout,
    session_bytes,
)
from kingfisher.plugins.sessions.permissions import protect_inputs, writable_inputs
from kingfisher.plugins.sessions.sandbox import confinement
from kingfisher.plugins.sessions.shell import (
    ConfinedLocalShellBackend,
    fence_for,
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

    First and not last, which decides what a reader sees: the row a real directory
    produced carries its modification time, and the route's own row is a stand-in with
    an empty one.
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
    """A match's identity: the file it names."""
    return one.get("path")


def _by_line(one: Mapping[str, Any]) -> Any:
    """A grep match's identity, which is the line and not the file."""
    return one.get("path"), one.get("line"), one.get("text")


def _directly_under(path: str, here: str) -> bool:
    """Whether `path` names something `here` holds, rather than itself or something
    deeper.

    The empty remainder is the directory asked about. Left in, a route listed its own
    stand-in row among its contents -- `ls /inputs` answered `/inputs/` and the file
    beside it -- which is how this was found.
    """
    if not path.startswith(here):
        return False
    rest = path[len(here) :].rstrip("/")
    return bool(rest) and "/" not in rest


def _listing(result: Any, path: str, routes: Iterable[tuple[str, Any]]) -> Any:
    """One directory's own entries: what it holds, each named once.

    `ls` is non-recursive by deepagents' own documentation, and at the root it appends
    a stand-in row for every route -- so a route nested two deep arrived as a top-level
    entry of `/`, next to the folder that holds it. The stand-in is kept and moved to
    the directory it belongs under, rather than dropped: with nothing written there yet
    the real folder does not exist, so that row is the only place the model hears the
    route is reachable at all.
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

    `ls`, `glob` and `grep` are deduplicated, on both paths. They merge every
    backend's answer, and two of the routes here point *inside* the default backend's
    own root -- `/inputs` and `/memory` are real directories under the session -- so
    each saw the same file twice: measured, one file supplied with `--input` came back
    as two matches with one path between them, on every pattern.

    Each override is written twice because deepagents offers each tool twice, and an
    async turn goes through the second one. The sync three were deduplicated for a
    release while `aglob`, `agrep` and `als` were not -- so the same session answered
    one way through `run` and another through the async path, which no suite asking
    only the sync half can see.
    """

    def ls(self, path: str) -> Any:
        """What this directory holds, each named once."""
        return _listing(super().ls(path), path, self.sorted_routes)

    async def als(self, path: str) -> Any:
        """The same, for an async turn."""
        return _listing(await super().als(path), path, self.sorted_routes)

    def glob(self, pattern: str, path: str | None = None) -> Any:
        """Every match, minus the ones a second backend already gave."""
        return _once(super().glob(pattern, path), key=_by_path)

    async def aglob(self, pattern: str, path: str | None = None) -> Any:
        """The same, for an async turn."""
        return _once(await super().aglob(pattern, path), key=_by_path)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> Any:
        """The same, keyed by the line rather than the file."""
        return _once(super().grep(pattern, path, glob, max_count=max_count), key=_by_line)

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> Any:
        """The same, for an async turn."""
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
        artifacts_root: str = DEFAULT_LAYOUT.scratch_route,
    ) -> None:
        # Where deepagents writes on its own -- a tool result too long to send, the
        # history its summarizer keeps. They are the agent's working material: returned
        # to nobody, swept with the session, and where `system.md` tells it to look.
        # `/outputs` would hand each one to the caller as an artifact, and the default,
        # `/`, puts both folders at the session root, in none the layout names. Under
        # Landlock the shell can rewrite them here, and may: nothing kingfisher reads
        # back trusts them. deepagents reads this only off a `CompositeBackend`, so it
        # is set here, and a subclass calling this `__init__` keeps it. The default is
        # the module's scratch route; `backend_at` passes the configured layout's.
        super().__init__(default=default, routes=routes, artifacts_root=artifacts_root)
        #: The deployment's workspace, which is what a host path is refused against.
        #: Separate from the session below, and they were one value: see
        #: `reject_host_path`, where the session is what the suggestion needs.
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

    `upload_files` is how kingfisher places a caller's files, and the only way in.
    The agent never reaches it: its file tools write through `write` and `edit`,
    which the permission bits still refuse, and a tool's backend refuses `/inputs`
    before the call arrives here. Unlocking inside the backend rather than around
    the call is what lets `place_inputs` work on any backend -- a remote one has no
    permission bits to lift, and meets the same promise its own way.
    """

    def __init__(self, session_dir: Path, inputs: str = DEFAULT_LAYOUT.inputs) -> None:
        super().__init__(root_dir=str(Path(session_dir) / inputs))
        self._session_dir = Path(session_dir)
        self._inputs = inputs

    def upload_files(self, files: list[tuple[str, bytes]]) -> Any:
        with writable_inputs(self._session_dir, self._inputs):
            return super().upload_files(files)


def _require_layout(
    session_dir: Path,
    session_dirs: tuple[str, ...] = DEFAULT_LAYOUT.session_dirs,
    saved_results: tuple[str, ...] = DEFAULT_LAYOUT.saved_results,
) -> None:
    """Refuse a session directory that has not been made yet."""
    missing = [
        name
        for name in (*session_dirs, DEFAULT_LAYOUT.harness, *saved_results)
        if not (session_dir / name).is_dir()
    ]
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

    What `LocalSessions` builds for a session id, and what a deployment building
    on the default calls to get it for a directory of its choosing. `shell_prompt` is
    what the agent is told about the shell `runner` provides, and is refused without
    one.

    Typed to the class rather than to deepagents' protocol because a deployment
    adjusting what this built reads `default` and `routes` off it, and the protocol
    carries neither.
    """
    _refuse_wording_without_runner(runner, shell_prompt)
    skills = (catalogue or Definitions.from_config(cfg)).skills
    skills_dir = skills.root
    # Before anything is mounted: a label becomes a route segment below, and a bad
    # one would otherwise reach the composite as a path.
    refuse_unmountable(skills_dir, skills.mounts)

    _require_layout(session_dir, cfg.layout.session_dirs, cfg.layout.saved_results)
    # Not created where it is missing: it is in the definitions folder, which a
    # start only reads, and a missing one means no skills. Both Linux fences grant
    # only the paths that exist, and the route below answers a read with "not
    # found" either way.
    # The view as well as what it points at: every fence checks the resolved path,
    # so the targets need their grant, and the Linux one also has to reach the
    # links themselves.
    view = skills_view(skills, cfg.kingfisher_dir) if skills.mounts else None
    every_skills_dir = (skills_dir, *skills.mounts.values(), *((view,) if view else ()))

    settings = shell if shell is not None else confinement.ShellSettings.from_env()
    confined = confinement.shell_confinement(cfg, settings, skills=every_skills_dir)
    env = shell_env(cfg, session_dir, path_extra=settings.path_extra, catalogue=catalogue)
    if runner is None:
        chosen = fence_for(
            cfg, session_dir, confined, every_skills_dir, env, path_extra=settings.path_extra
        )
    else:
        # Said once, here, rather than left for a reader to work out from two
        # places. What confines the shell depends on what runs the command, and
        # a supplied runner that is not local receives nothing this process
        # applied -- so the confinement has to stop claiming otherwise.
        chosen = runner
        confined = confinement.with_supplied_runner(confined, local=runs_locally(runner))
    if confined.warning:
        # After the runner is settled, not beside `shell_confinement`: a runner that
        # is not local withdraws the warning, and saying it earlier would tell that
        # deployment its host is exposed when nothing runs on it. This line rather
        # than the caller's, so the default filter says it once per process instead
        # of once per place that builds a backend.
        # What it means for the people one workspace serves, said where a program
        # running kingfisher sees it: most never run `doctor`.
        said = confined.warning + (
            " A workspace may hold many sessions, and each session's agent can then also "
            "read and change the other sessions' files."
        )
        warnings.warn(said, stacklevel=1)
    confined_shell = ConfinedLocalShellBackend(
        confined,
        runner=chosen,
        # The deployment's runner and not `chosen`, which is kingfisher's own fence on
        # Linux: that one is handed `env` and the session directory, and the
        # deployment's is handed the command and nothing else.
        shell_prompt=kingfishers_shell() if runner is None else shell_prompt,
        root_dir=str(session_dir),
        env=env,
        timeout=cfg.execution_timeout_s,
    )
    # What backs each path. Keyed by the table rather than written as one dict
    # so that a route declared in `kingfisher.core.layout` and forgotten here raises when
    # the backend is built, instead of reaching a turn as a path that resolves
    # to the default backend and quietly ignores its own deny rule.
    layout = cfg.layout
    backing = {
        layout.inputs_route: lambda: InputsBackend(session_dir, layout.inputs),
        layout.skills_route: lambda: FilesystemBackend(root_dir=str(skills_dir)),
        layout.memory_route: lambda: FilesystemBackend(
            root_dir=str(session_dir / layout.memory)
        ),
        # Mounted so it can be refused. Every operation through it is denied by
        # `read_only_permissions`, and a rule is only expressible against a path
        # the composite routes -- so the mount is what makes the refusal legal,
        # not a way in.
        layout.harness_route: lambda: FilesystemBackend(root_dir=str(session_dir / layout.harness)),
        # Mounted for the reason `.harness` is: a rule is only expressible against a
        # path the composite routes, so the mount is what makes the refusal legal.
        # Unlike `.harness` it is readable -- the agent is told to read a result back
        # out of it -- and only writing is denied.
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
    # One per bundle and per mount, so a subagent's own skills and a mounted party's
    # are readable by the file tools that read every other skill. Generated rather
    # than declared: the names are not known until a catalogue is read, which is
    # what `family=True` marks.
    routes.update({
        route: FilesystemBackend(root_dir=str(directory))
        for route, directory in skill_mounts(catalogue or Definitions.from_config(cfg))
    })

    return WorkspaceScopedBackend(
        default=confined_shell,
        routes=routes,
        session_dir=session_dir,
        workspace=cfg.workspace,
        artifacts_root=layout.scratch_route,
    )


class LocalSessions(Sessions):
    """Kingfisher's own `Sessions`: each session a directory under
    `<workspace>/sessions`, named for its id, each backend `backend_at` that directory.

    `LocalSessions` runs each session's commands here, under kingfisher's own
    fence. A deployment whose commands run somewhere else builds one with `runner=`,
    and its sessions are still kingfisher's own: `Kingfisher.origins` asks whether the
    backends are exactly this class rather than a subclass, because a subclass can
    return its backend with anything changed.
    """

    #: On the class as well as set in `__init__`, so a subclass that writes its own
    #: `__init__` without calling this one still opens sessions.
    _runner: Callable[[Path], CommandRunner] | None = None
    _shell_prompt: ShellPrompt | None = None
    #: How this shell is fenced. `None` reads the environment at each opening, which is
    #: what a subclass that skips `__init__` gets.
    shell: confinement.ShellSettings | None = None

    def __init__(
        self,
        *,
        runner: Callable[[Path], CommandRunner] | None = None,
        shell_prompt: ShellPrompt | None = None,
        shell: confinement.ShellSettings | None = None,
    ) -> None:
        # A callable, and only a callable. A runner is built for one session --
        # kingfisher's own Landlock fence is, because its policy is generated from
        # the session -- and a shared instance could not know which session it was
        # running for, would be one fence for every tenant where the runner *is* the
        # isolation, and would be called from several threads at once because turns
        # overlap. A deployment with one to share writes `lambda session_dir: shared`.
        if runner is not None and not callable(runner):
            msg = (
                "runner is built per session, so it takes a callable: pass "
                "`lambda session_dir: your_runner` if you have one to share"
            )
            raise TypeError(msg)
        # Here as well as in `backend_at`, so a deployment hears it at startup rather
        # than on its first turn.
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
        directory = ensure_session_layout(
            _where(cfg, session_id),
            cfg.layout.session_dirs,
            memory=cfg.layout.memory,
            # Named as well as being in the tuple, because `keep_tmp_private` cannot
            # pick one directory out of it. Omitted, the configured scratchpad was laid
            # out `0o755` where the default one is `0o700` -- and TMPDIR points at it.
            scratchpad=cfg.layout.scratchpad,
            saved_results=cfg.layout.saved_results,
        )
        # Kernel-level, because the deny rule covers only the file tools. What it
        # could not harden is reported by the turn rather than raised: raising here
        # used to make one file owned by another user a session unusable for good.
        unprotected = protect_inputs(directory, cfg.layout.inputs)
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

        Only for exactly this class: a subclass can return its backend with anything
        changed, so it is answered as somewhere this cannot see.
        """
        return cfg.sessions_root if type(self) is LocalSessions else None

    # The turn lock is a directory under the session's `.harness`. `mkdir` because it is
    # the one operation that fails when the name is taken, which is what a lock is --
    # `write` overwrites, and two turns would both take it.

    def claim(
        self, cfg: Config, session_id: str, /, *, stale_after: float, now: float | None = None
    ) -> bool:
        dirs, path = LocalSessionDirs(), _claim_at(cfg, session_id)
        if dirs.create_exclusive(path):
            return True
        if self.held(cfg, session_id, stale_after=stale_after, now=now):
            return False
        dirs.remove_tree(path)
        return dirs.create_exclusive(path)

    def release(self, cfg: Config, session_id: str, /) -> None:
        LocalSessionDirs().remove_tree(_claim_at(cfg, session_id))

    def held(
        self, cfg: Config, session_id: str, /, *, stale_after: float, now: float | None = None
    ) -> bool:
        # One vanishing between the two looks counts as held: its age is zero, so a race
        # resolves toward refusing rather than toward taking a slot whose owner may be
        # about to write.
        at = time.time() if now is None else now
        try:
            taken = _claim_at(cfg, session_id).stat().st_mtime
        except FileNotFoundError:
            return False
        return bool(still_held(((CLAIM, taken),), stale_after=stale_after, now=at))


def _where(cfg: Config, session_id: str) -> Path:
    """Where kingfisher's own backends keep one session of this workspace."""
    return session_dir(cfg.workspace, session_id)


def _claim_at(cfg: Config, session_id: str) -> Path:
    """Where kingfisher's own sessions keep one session's turn lock."""
    return _where(cfg, session_id) / cfg.layout.harness / CLAIM
