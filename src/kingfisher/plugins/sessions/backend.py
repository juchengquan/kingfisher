"""Kingfisher's own sessions: a folder per session on this host, and the backend over it.

The core never names anything here; a deployment hands `LocalSessions` to `Kingfisher` as
it would its own.
"""

from __future__ import annotations

import re
import time
import uuid
import warnings
from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from typing import Any

from deepagents.backends import CompositeBackend
from deepagents.backends.composite import (
    _remaining_grep_budget,
    _remap_file_info_path,
    _remap_grep_path,
    _route_glob_pattern,
)

from kingfisher.core.agent.host_paths import reject_host_path
from kingfisher.core.agent.prompting import ShellPrompt
from kingfisher.core.config import Config, ConfigError
from kingfisher.core.definitions.catalogue import Definitions
from kingfisher.core.interfaces import CommandRunner, Sessions, refuse_unless_subclass
from kingfisher.core.layout import Layout
from kingfisher.plugins.sessions.sandbox import confinement

from .folders import (
    LocalSessionDirs,
    ensure_session_layout,
    refuse_linked_folders,
    session_bytes,
    session_dir,
)
from .links import RootedBackend
from .permissions import protect_inputs, writable_inputs
from .shell import (
    ConfinedLocalShellBackend,
    fence_for,
    kingfishers_shell,
    shell_env,
    shell_skills,
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


def _listing(result: Any, path: str) -> Any:
    """One directory's own entries: what it holds, each named once.

    deepagents answers `/` with a row for every route, nested ones included, while its own
    `ls` does not recurse -- so `/scratchpad/tool_results/` arrived as an entry of `/`,
    beside the folder that holds it. Those rows are dropped rather than moved under the
    directory they belong to, because nothing needs them: measured with every route
    mounted and a bundle shipped, the only rows this was ever asked to move were two whose
    folder nothing had made, and a route whose folder exists is listed by the backend that
    holds it anyway. #1036 is what measured the cost of moving one: `ls /scratchpad` named
    four folders where two existed, and the model opening either of the others was told
    `path_not_found` about a path the listing had just offered it.
    """
    if result.entries is None:
        return result
    here = path if path.endswith("/") else f"{path}/"
    kept = sorted(
        (one for one in result.entries if _directly_under(one.get("path", ""), here)),
        key=lambda one: one.get("path", ""),
    )
    return _once(replace(result, entries=kept), key=_by_path, field="entries")


def _below(routes: Iterable[tuple[str, Any]], path: str) -> list[tuple[str, str, Any]]:
    """Each route nested strictly below `path`: its own prefix, that prefix relative to
    `path`, and its backend."""
    here = path if path.endswith("/") else f"{path}/"
    return [
        (prefix, prefix[len(here) :], backend)
        for prefix, backend in routes
        if prefix.startswith(here) and prefix.rstrip("/") != here.rstrip("/")
    ]


def _asks_below(path: str | None, result: Any) -> bool:
    """Whether the routes below `path` are still to be asked.

    `None` and `/` are the composite's own merge, which asks every route already, and a
    search that failed is handed back as it is.
    """
    return path is not None and path != "/" and not getattr(result, "error", None)


def _globbed_below(result: Any, pattern: str, path: str | None, routes: Any) -> Any:
    """`result` with what every route nested below `path` matches as well.

    The composite routes a scoped search to the one backend owning the longest matching
    prefix and stops there, so a route nested deeper is never asked -- measured, a
    bundled skill under `/skills/subagents/<bundle>/` was found by a search of the
    session and missed by one of `/skills`, which is where the prompt sends an agent
    looking for a procedure.

    Built on deepagents' own helpers, as `PermittedBackend` is: which routes a pattern
    can match is their anchoring rule to decide, and the path a route's result is named
    by is their spelling, so a scoped search means what their root search means. A second
    copy of either is the one that drifts.

    An error comes back as an error, as it does from their root search: a search that
    answered what it could and said nothing of the route that failed would be the partial
    answer `_listing` stopped giving. A route whose folder nothing has made is not that
    case -- measured, a missing root answers a search with no matches and no error, where
    `ls` calls it `path_not_found` -- so `blobs`, mounted and never made, costs a search
    nothing.
    """
    if not _asks_below(path, result):
        return result
    found = list(result.matches or [])
    for prefix, relative, backend in _below(routes, str(path)):
        if (adapted := _route_glob_pattern(pattern, relative)) is None:
            continue
        answer = backend.glob(adapted, "/")
        if getattr(answer, "error", None):
            return answer
        found += [_remap_file_info_path(one, prefix) for one in (answer.matches or [])]
    return replace(result, matches=found)


async def _aglobbed_below(result: Any, pattern: str, path: str | None, routes: Any) -> Any:
    """`_globbed_below` for an async turn, which asks each route the async way."""
    if not _asks_below(path, result):
        return result
    found = list(result.matches or [])
    for prefix, relative, backend in _below(routes, str(path)):
        if (adapted := _route_glob_pattern(pattern, relative)) is None:
            continue
        answer = await backend.aglob(adapted, "/")
        if getattr(answer, "error", None):
            return answer
        found += [_remap_file_info_path(one, prefix) for one in (answer.matches or [])]
    return replace(result, matches=found)


class WorkspaceScopedBackend(CompositeBackend):
    """A `CompositeBackend` that refuses host paths instead of re-rooting them, in every
    call that names a path.

    `ls`, `glob` and `grep` are deduplicated: they merge every backend's answer, and
    `/inputs` and `/memory` are routes *inside* the default backend's root, so each file
    there came back twice. Each override is written for the async tool too, which an async
    turn goes through and a suite asking only the sync half cannot see.
    """

    def ls(self, path: str) -> Any:
        self._refuse_host_path(path)
        return _listing(super().ls(path), path)

    async def als(self, path: str) -> Any:
        self._refuse_host_path(path)
        return _listing(await super().als(path), path)

    def glob(self, pattern: str, path: str | None = None) -> Any:
        self._refuse_host_path(path)
        found = _globbed_below(super().glob(pattern, path), pattern, path, self.sorted_routes)
        return _once(found, key=_by_path)

    async def aglob(self, pattern: str, path: str | None = None) -> Any:
        self._refuse_host_path(path)
        found = await _aglobbed_below(
            await super().aglob(pattern, path), pattern, path, self.sorted_routes
        )
        return _once(found, key=_by_path)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> Any:
        self._refuse_host_path(path)
        found = super().grep(pattern, path, glob, max_count=max_count)
        if _asks_below(path, found):
            matches = list(found.matches or [])
            truncated = found.truncated
            for prefix, _relative, backend in _below(self.sorted_routes, str(path)):
                if (left := _remaining_grep_budget(max_count, len(matches))) == 0:
                    truncated = True
                    break
                answer = self._grep_backend(backend, pattern, "/", glob, left)
                if answer.error:
                    return answer
                matches += [_remap_grep_path(one, prefix) for one in (answer.matches or [])]
                truncated = truncated or answer.truncated
            found = replace(found, matches=matches, truncated=truncated)
        return _once(found, key=_by_line)

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> Any:
        self._refuse_host_path(path)
        found = await super().agrep(pattern, path, glob, max_count=max_count)
        if _asks_below(path, found):
            matches = list(found.matches or [])
            truncated = found.truncated
            for prefix, _relative, backend in _below(self.sorted_routes, str(path)):
                if (left := _remaining_grep_budget(max_count, len(matches))) == 0:
                    truncated = True
                    break
                answer = await self._agrep_backend(backend, pattern, "/", glob, left)
                if answer.error:
                    return answer
                matches += [_remap_grep_path(one, prefix) for one in (answer.matches or [])]
                truncated = truncated or answer.truncated
            found = replace(found, matches=matches, truncated=truncated)
        return _once(found, key=_by_line)

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
        # its summarizer keeps, an attachment's payload. Working material, so not
        # `/outputs`, which would hand each to the caller as an artifact, nor the default
        # `/`, outside every folder the layout names. Each is a kept record, denied to the
        # file tools and to the shell: kingfisher reads none of them back, but the prompt
        # sends the model to the history, and that reader is the one being protected.
        # deepagents reads this only off a `CompositeBackend`, so it is set here.
        super().__init__(default=default, routes=routes, artifacts_root=layout.scratch_route)
        #: What a host path is refused against; not the session, which the refusal's
        #: suggestion needs separately.
        self.workspace = Path(workspace)
        self._session_dir = Path(session_dir)
        #: What `protect_inputs` could not harden, for the turn to report.
        self.unprotected: tuple[str, ...] = ()

    def _get_backend_and_key(self, key: str) -> tuple[Any, str]:
        self._refuse_host_path(key)
        return super()._get_backend_and_key(key)

    def _refuse_host_path(self, path: str | None) -> None:
        # Called by the searches as well: upstream's `ls`, `glob` and `grep` route by
        # prefix without `_get_backend_and_key`, so a host path there was re-rooted and
        # answered "not found" where `read_file` was told the path to use.
        if path is not None:
            reject_host_path(
                path, self.workspace, session_dir=self._session_dir,
                scratch_route=self.artifacts_root,
            )


class InputsBackend(RootedBackend):
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


def _refuse_root_without_runner(runner: Any, session_root: object) -> None:
    """Kingfisher's own shell exports its session folder, and another root would be wrong."""
    if runner is None and session_root is not None:
        msg = (
            "session_root says where a runner's shell sees the session, and no runner was "
            "given: kingfisher's own shell exports its session folder itself"
        )
        raise TypeError(msg)


def _require_layout(session_dir: Path, layout: Layout) -> None:
    """Refuse a session directory that has not been made yet, or whose folders are links."""
    refuse_linked_folders(session_dir, layout)
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
    session_root: str | None = None,
    shell: confinement.ShellSettings | None = None,
) -> WorkspaceScopedBackend:
    """Kingfisher's own backend, rooted at one session directory on this host.

    `shell_prompt` describes the shell `runner` provides, and `session_root` is where
    that shell sees `session_dir` and exports it as `KINGFISHER_SESSION_ROOT`; each is
    refused without a runner.
    Typed to the class rather than deepagents' protocol: a deployment adjusting what this
    built reads `default` and `routes` off it, and the protocol carries neither.
    """
    _refuse_wording_without_runner(runner, shell_prompt)
    _refuse_root_without_runner(runner, session_root)
    definitions = catalogue or Definitions.from_config(cfg)
    skills_dir = definitions.skills.root

    _require_layout(session_dir, cfg.layout)
    every_skills_dir = shell_skills(cfg, definitions)

    settings = shell if shell is not None else confinement.ShellSettings.from_env()
    confined = confinement.shell_confinement(cfg, settings, skills=every_skills_dir)
    env = shell_env(cfg, session_dir, path_extra=settings.path_extra, catalogue=definitions)
    fence = None
    if runner is None:
        fence = fence_for(
            cfg, session_dir, confined, every_skills_dir, env, path_extra=settings.path_extra
        )
        chosen: CommandRunner | None = fence
    else:
        # Here rather than when `LocalSessions` is built: a runner is made per session.
        refuse_unless_subclass("runner=", runner, CommandRunner)
        # A supplied runner that is not local receives nothing this process applied, so
        # the confinement must stop claiming it.
        chosen = runner
        confined = confinement.with_supplied_runner(confined, local=runner.local)
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
        fence=fence,
        scratchpad=cfg.layout.scratchpad,
        # The deployment's runner, not `chosen`, which on Linux is kingfisher's own fence:
        # that one is handed `env` and the session directory, a deployment's only the
        # command.
        shell_prompt=(
            kingfishers_shell(fenced=confined.mechanism in confinement.FENCES_HARNESS)
            if runner is None
            else shell_prompt
        ),
        # What `shell_env` exports, where the shell is kingfisher's own.
        session_root=str(session_dir) if runner is None else session_root,
        root_dir=str(session_dir),
        env=env,
        timeout=cfg.execution_timeout_s,
    )
    # Checked against the layout's routes so one forgotten here raises at build, rather
    # than resolving to the default backend and ignoring its own deny rule.
    layout = cfg.layout
    backing = {
        layout.inputs_route: lambda: InputsBackend(session_dir, layout),
        layout.skills_route: lambda: RootedBackend(root_dir=str(skills_dir)),
        layout.memory_route: lambda: RootedBackend(root_dir=str(session_dir / layout.memory)),
        # Mounted so it can be refused, not as a way in: `read_only_permissions` denies
        # every operation, and a rule is expressible only against a routed path.
        layout.harness_route: lambda: RootedBackend(root_dir=str(session_dir / layout.harness)),
    }
    # Each kept record for the same reason, readable: the agent reads a result and the
    # conversation kept aside back from them, and only writing is denied. A mount does not
    # make the folder, so `blobs`, which nothing writes, stays absent until something does.
    backing.update({
        route: (lambda folder=folder: RootedBackend(root_dir=str(session_dir / folder)))
        for route, folder in zip(layout.kept_record_routes, layout.kept_records, strict=True)
    })
    missing = [path for path in layout.routed_paths() if path not in backing]
    if missing:  # pragma: no cover -- a table edit, caught by its own test
        msg = f"routes declared in kingfisher.core.layout with nothing to back them: {missing}"
        raise ConfigError(msg)

    routes: dict[str, Any] = {path: backing[path]() for path in layout.routed_paths()}
    # One per bundle and per mount, so the file tools read a subagent's or a mounted
    # party's skills too. Generated: the names are not known until a catalogue is read.
    routes.update({
        route: RootedBackend(root_dir=str(directory))
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
    _session_root: Callable[[Path], str] | None = None
    #: How this shell is fenced. `None` reads the environment at each opening.
    shell: confinement.ShellSettings | None = None

    def __init__(
        self,
        *,
        runner: Callable[[Path], CommandRunner] | None = None,
        shell_prompt: ShellPrompt | None = None,
        session_root: Callable[[Path], str] | None = None,
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
        # Per session for the same reason: where a runner's shell sees it depends on which.
        if session_root is not None and not callable(session_root):
            msg = (
                "session_root is asked per session, so it takes a callable: pass "
                "`lambda session_dir: where_your_runner_sees_it`"
            )
            raise TypeError(msg)
        # As well as in `backend_at`, so a deployment hears it at startup, not first turn.
        _refuse_wording_without_runner(runner, shell_prompt)
        _refuse_root_without_runner(runner, session_root)
        self._runner = runner
        self._shell_prompt = shell_prompt
        self._session_root = session_root
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
        root = self._session_root(directory) if self._session_root is not None else None
        built = backend_at(
            cfg,
            directory,
            catalogue=catalogue,
            runner=runner,
            shell_prompt=self._shell_prompt,
            session_root=root,
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
        return at - taken < stale_after


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
    # So a sweep can claim a session laid out without a `.harness`. Not `parents=True`:
    # a session deleted under a starting turn would be listed again, holding the claim.
    path.parent.mkdir(exist_ok=True)
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


def _claim_at(cfg: Config, session_id: str) -> Path:
    """Where kingfisher's own sessions keep one session's turn lock."""
    return _where(cfg, session_id) / cfg.layout.harness / CLAIM
