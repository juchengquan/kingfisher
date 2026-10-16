"""Kingfisher's own sessions: a folder per session on this host, the backend over it,
and the fences around its shell.

The core never names anything here. A deployment hands `LocalSessions` to
`Kingfisher` as it would its own.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import time
import uuid
import warnings
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from deepagents.backends import CompositeBackend, FilesystemBackend, LocalShellBackend
from deepagents.backends.protocol import ExecuteResponse

from kingfisher.config import Config, ConfigError
from kingfisher.core.definitions.catalogue import Definitions, refuse_unmountable
from kingfisher.core.definitions.skills.catalogue import LocalSkillRepository
from kingfisher.domain.ports import CommandRunner
from kingfisher.domain.session import session_dir, sessions_root, still_held
from kingfisher.infrastructure.harness.backend import (
    Sessions,
    skill_mounts,
)

# Re-exported: `ports.md` has named this path to deployments writing their own
# backend, which may import it from here.
from kingfisher.infrastructure.harness.host_paths import (
    HostPathError as HostPathError,  # noqa: PLC0414 -- the re-export above
)
from kingfisher.infrastructure.harness.host_paths import reject_host_path
from kingfisher.infrastructure.prompting import ShellPrompt, kingfishers_shell
from kingfisher.layout import (
    CLAIM,
    HARNESS,
    HARNESS_OWNED,
    HARNESS_ROUTE,
    INPUTS,
    OFFLOADS,
    SAVED_RESULTS,
    SCRATCH_ROUTE,
    SESSION_DIRS,
    SKILLS_ROUTE,
    SKILLS_VIEW,
)
from kingfisher.plugins.sessions.folders import (
    LocalSessionDirs,
    ensure_session_layout,
    session_bytes,
)
from kingfisher.plugins.sessions.permissions import protect_inputs, writable_inputs
from kingfisher.plugins.sessions.sandbox import confinement

_BASE_PATH: tuple[str, ...] = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")


def shell_env(
    cfg: Config, session_dir: Path, *, catalogue: Definitions | None = None
) -> dict[str, str]:
    """The explicit allowlist handed to the shell — no credentials.

    That is all it does. Redirecting `HOME` moves where a path is *resolved*; the
    files stay where they are, and an absolute path still reaches them. Measured on
    this machine, the shell could read `~/.aws` and `~/.config/gh` right through it.
    Keeping those closed is `confinement`'s job, not this function's.
    """
    path_parts = [str(Path(sys.executable).parent), *cfg.shell_path_extra, *_BASE_PATH]
    env = {
        "PATH": ":".join(path_parts),
        # Inside the session, so `reap` sweeps what tools cache under `~` and
        # `session_bytes` counts it; above the session such caches reached 59MB
        # in one workspace with nothing sweeping them.
        "HOME": str(session_dir / cfg.layout.scratchpad),
        "LANG": "en_US.UTF-8",
        "LC_ALL": "en_US.UTF-8",
        "TMPDIR": str(session_dir / cfg.layout.scratchpad),
    }
    skills = (catalogue or Definitions.from_config(cfg)).skills
    env["KINGFISHER_SKILLS"] = str(skills_view(skills, cfg.workspace))
    return env


def skills_view(skills: LocalSkillRepository, workspace: Path) -> Path:
    """Where the shell finds the catalogue, so `/skills/X` is `$KINGFISHER_SKILLS/X`.

    The catalogue itself when nothing is mounted. With mounts, a directory of
    symlinks: one per entry of the catalogue and one per mount under its label,
    which `refuse_unmountable` has already made sure cannot collide.
    """
    if not skills.mounts:
        return skills.root
    root = skills.root
    links = {e.name: e.absolute() for e in sorted(root.iterdir())} if root.is_dir() else {}
    links.update({label: mount.absolute() for label, mount in skills.mounts.items()})
    # Named for what it holds, so a catalogue that gained a skill gets a new view
    # rather than a stale one, and two turns building the same view build one.
    listing = "\n".join(f"{name}\t{target}" for name, target in sorted(links.items()))
    view = (
        workspace / HARNESS_OWNED / SKILLS_VIEW
        / hashlib.sha256(listing.encode()).hexdigest()[:16]
    )
    if view.is_dir():
        return view
    # Built aside and renamed into place, so no turn ever reads a half-built view.
    # A rename onto a directory that another turn finished first fails, and that
    # turn's view is this one.
    staging = view.with_name(f"{view.name}.{os.getpid()}.{uuid.uuid4().hex}")
    staging.mkdir(parents=True)
    for name, target in links.items():
        (staging / name).symlink_to(target)
    try:
        staging.rename(view)
    except OSError:
        shutil.rmtree(staging)
    return view


class ConfinedLocalShellBackend(LocalShellBackend):
    """`LocalShellBackend` with every command run through a confinement.

    `None` means run it here, which is what upstream already does -- a default runner
    would be 110 lines of upstream's truncation, timeout and exit-code shaping,
    copied to be kept in step. The confinement is applied either way, before the
    runner sees the command, so a runner cannot forget to.
    """

    def __init__(
        self,
        confined: confinement.Confinement,
        *,
        runner: CommandRunner | None = None,
        shell_prompt: ShellPrompt | None = None,
        **kwargs: Any,
    ) -> None:
        self.confinement = confined
        self.runner = runner
        #: What this shell says of itself, which `shell_prompt_of` reads. Set by
        #: `backend_at`, which knows whether the runner is kingfisher's own.
        self.shell_prompt = shell_prompt
        super().__init__(**kwargs)

    @property
    def runs_here(self) -> bool:
        """Whether commands run on this host, which `shell_runs_here` reads."""
        return self.runner is None or runs_locally(self.runner)

    def execute(self, command: str, *, timeout: int | None = None) -> Any:
        if self.runner is None:
            return super().execute(self.confinement.wrap(command), timeout=timeout)
        # A confinement is a command prefix naming paths on *this* host, so
        # applying it to something that runs elsewhere produces a
        # `sandbox-exec -f /Users/.../shell.sb` shipped to a machine with no
        # such file -- which fails looking like a broken remote shell rather
        # than like a wrong prefix.
        outcome = self.runner.run(
            self.confinement.wrap(command) if runs_locally(self.runner) else command,
            timeout=timeout,
        )
        return ExecuteResponse(
            output=outcome.output,
            exit_code=outcome.exit_code,
            truncated=outcome.truncated,
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


def runs_locally(runner: Any) -> bool:
    """Whether a supplied runner runs the command on this machine.

    **The default is the safe one, and it is read here so it is one answer rather
    than two.** `CommandRunner` declares `local` as a property answering `True`, but a
    duck-typed runner -- the case this default exists for -- inherits nothing from a
    Protocol, so every reader has to supply the default itself. Two readers did: this
    module decides whether to wrap a command with it, and decides what the
    `Confinement` beside that command claims. The second was written out separately
    and held by nothing: flipped to `False` it left all 2,109 tests passing, while a
    supplied local runner that says nothing got `wrap=_unwrapped` and ran with no
    sandbox at all, under a `Confinement` reporting `confined` as false and warning
    about nothing.

    `testing.a_runner_says_where_it_runs` keeps its own reading on purpose. It is the
    kit a deployment checks its own adapter with, and importing this module would take
    it from 70 loaded modules to 3,104 -- measured -- to share one `getattr`, because it
    reaches deepagents and deepagents reaches the provider SDKs.
    """
    return getattr(runner, "local", True)


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
        artifacts_root: str = SCRATCH_ROUTE,
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

    def __init__(self, session_dir: Path, inputs: str = INPUTS) -> None:
        super().__init__(root_dir=str(Path(session_dir) / inputs))
        self._session_dir = Path(session_dir)
        self._inputs = inputs

    def upload_files(self, files: list[tuple[str, bytes]]) -> Any:
        with writable_inputs(self._session_dir, self._inputs):
            return super().upload_files(files)


def _fence_for(
    cfg: Config,
    session_dir: Path,
    confined: confinement.Confinement,
    skills: tuple[Path, ...],
    env: Mapping[str, str],
) -> CommandRunner | None:
    """The Linux fence, when the confinement says there is one."""
    if confined.mechanism not in confinement.LINUX_FENCES:
        return None

    # One answer for both fences rather than the same three lines twice. `argv_for` and
    # `policy_for` take the same arguments and mean the same thing by them, so a path
    # added to one branch and not the other fences the shell differently depending on
    # which mechanism the host happens to have -- and the suite would not catch it,
    # because a run only ever exercises the one mechanism its own kernel offers. That is
    # the divergence this function already avoids one question earlier by deriving from
    # `Confinement`.
    #
    # Not `readable_roots`, which is the obvious call and the wrong one: it also returns
    # the *workspace*, which is right where the home is denied and the workspace
    # re-allowed inside it, and catastrophic here. Sessions live under the workspace, so
    # granting it hands every tenant back the directory this fence exists to take away
    # -- measured, before the fence: tenant B read tenant A's `outputs/secret.txt` with
    # `cat ../<A>/...`, exit 0.
    readable = [*confinement.toolchain_roots(cfg.shell_path_extra), *skills]
    # Nothing beyond the session, which both fences grant already: `TMPDIR` is
    # inside it now, so the writable set is exactly the session this turn owns.
    writable: list[Path] = []

    if confined.mechanism == confinement.BUBBLEWRAP:
        from kingfisher.plugins.sessions.sandbox.bubblewrap import (  # noqa: PLC0415
            BubblewrapRunner,
            argv_for,
        )

        return BubblewrapRunner(
            argv_for(
                session_dir,
                readable=readable,
                writable=writable,
                inputs=cfg.layout.inputs,
                saved_results=cfg.layout.saved_results,
            ),
            env=env,
        )

    # Imported here for the reason the module explains: `sandlock` is a
    # Linux-only optional install, and this function is called on every turn on
    # every platform.
    from kingfisher.plugins.sessions.sandbox.fence import (  # noqa: PLC0415
        LandlockRunner,
        policy_for,
    )

    return LandlockRunner(
        policy_for(
            session_dir,
            readable=readable,
            writable=writable,
            session_dirs=cfg.layout.session_dirs,
            inputs=cfg.layout.inputs,
        ),
        cwd=session_dir,
        env=env,
    )


def _require_layout(
    session_dir: Path,
    session_dirs: tuple[str, ...] = SESSION_DIRS,
    saved_results: tuple[str, ...] = SAVED_RESULTS,
) -> None:
    """Refuse a session directory that has not been made yet."""
    missing = [
        name
        for name in (*session_dirs, HARNESS, *saved_results)
        if not (session_dir / name).is_dir()
    ]
    if missing:
        msg = (
            f"session directory {session_dir} is missing {', '.join(missing)}; "
            "call ensure_session_layout on it before building a backend"
        )
        raise ValueError(msg)


def backend_at(
    cfg: Config,
    session_dir: Path,
    *,
    catalogue: Definitions | None = None,
    runner: CommandRunner | None = None,
    shell_prompt: ShellPrompt | None = None,
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
    view = skills_view(skills, cfg.workspace) if skills.mounts else None
    every_skills_dir = (skills_dir, *skills.mounts.values(), *((view,) if view else ()))

    confined = confinement.shell_confinement(cfg, skills=every_skills_dir)
    env = shell_env(cfg, session_dir, catalogue=catalogue)
    if runner is None:
        chosen = _fence_for(cfg, session_dir, confined, every_skills_dir, env)
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
    shell = ConfinedLocalShellBackend(
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
    # so that a route declared in `kingfisher.layout` and forgotten here raises when
    # the backend is built, instead of reaching a turn as a path that resolves
    # to the default backend and quietly ignores its own deny rule.
    layout = cfg.layout
    backing = {
        layout.inputs_route: lambda: InputsBackend(session_dir, layout.inputs),
        SKILLS_ROUTE: lambda: FilesystemBackend(root_dir=str(skills_dir)),
        layout.memory_route: lambda: FilesystemBackend(
            root_dir=str(session_dir / layout.memory)
        ),
        # Mounted so it can be refused. Every operation through it is denied by
        # `read_only_permissions`, and a rule is only expressible against a path
        # the composite routes -- so the mount is what makes the refusal legal,
        # not a way in.
        HARNESS_ROUTE: lambda: FilesystemBackend(root_dir=str(session_dir / HARNESS)),
        # Mounted for the reason `.harness` is: a rule is only expressible against a
        # path the composite routes, so the mount is what makes the refusal legal.
        # Unlike `.harness` it is readable -- the agent is told to read a result back
        # out of it -- and only writing is denied.
        layout.tool_results_route: lambda: FilesystemBackend(
            root_dir=str(session_dir / layout.scratchpad / layout.tool_results)
        ),
        layout.offloads_route: lambda: FilesystemBackend(
            root_dir=str(session_dir / layout.scratchpad / OFFLOADS)
        ),
    }
    missing = [path for path in layout.routed_paths() if path not in backing]
    if missing:  # pragma: no cover -- a table edit, caught by its own test
        msg = f"routes declared in kingfisher.layout with nothing to back them: {missing}"
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
        default=shell,
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

    def __init__(
        self,
        *,
        runner: Callable[[Path], CommandRunner] | None = None,
        shell_prompt: ShellPrompt | None = None,
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
            cfg, directory, catalogue=catalogue, runner=runner, shell_prompt=self._shell_prompt
        )
        built.unprotected = unprotected
        return built

    def sessions(self, cfg: Config) -> tuple[tuple[str, float], ...]:
        return LocalSessionDirs().listing(sessions_root(cfg.workspace))

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
        return sessions_root(cfg.workspace) if type(self) is LocalSessions else None

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
    return _where(cfg, session_id) / HARNESS / CLAIM
