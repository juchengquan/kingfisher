"""Filesystem + shell backend."""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import uuid
import warnings
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, Protocol

from deepagents.backends import CompositeBackend, FilesystemBackend, LocalShellBackend
from deepagents.backends.protocol import ExecuteResponse

from kingfisher.config import Config, ConfigError
from kingfisher.domain.ports import CommandRunner, SkillRepository
from kingfisher.infrastructure.catalogue import Definitions, refuse_unmountable

# Re-exported: `ports.md` and `BACKEND_CONTRACT` have named this path to adapter
# authors, and a deployment's own backend may import it from here.
from kingfisher.infrastructure.harness.host_paths import (
    HostPathError as HostPathError,  # noqa: PLC0414 -- the re-export above
)
from kingfisher.infrastructure.harness.host_paths import reject_host_path
from kingfisher.infrastructure.sandbox import confinement
from kingfisher.kinds.subagents.spec import SubagentError
from kingfisher.layout import (
    BUNDLED_SKILLS_ROUTE,
    DATA,
    DATA_ROUTE,
    HARNESS,
    HARNESS_OWNED,
    HARNESS_ROUTE,
    MEMORY,
    MEMORY_ROUTE,
    RESERVED_SKILL_FOLDER,
    SCRATCH,
    SESSION_DIRS,
    SKILLS_ROUTE,
    SKILLS_VIEW,
    routed_paths,
)

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
        "HOME": str(session_dir / SCRATCH),
        "LANG": "en_US.UTF-8",
        "LC_ALL": "en_US.UTF-8",
        "TMPDIR": str(session_dir / SCRATCH),
    }
    skills = (catalogue or Definitions.from_config(cfg)).skills
    env["KINGFISHER_SKILLS"] = str(skills_view(skills, cfg.workspace))
    return env


def skills_view(skills: SkillRepository, workspace: Path) -> Path:
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
        **kwargs: Any,
    ) -> None:
        self.confinement = confined
        self.runner = runner
        super().__init__(**kwargs)

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
    it from 75 loaded modules to 103 -- measured -- to share one `getattr`.
    """
    return getattr(runner, "local", True)


def _once(result: Any, *, key: Callable[[Any], Any]) -> Any:
    """A result with its repeated matches dropped, first occurrence kept."""
    if result.matches is None:
        return result
    seen: set[Any] = set()
    kept = []
    for one in result.matches:
        identity = key(one)
        if identity in seen:
            continue
        seen.add(identity)
        kept.append(one)
    return replace(result, matches=kept)


class WorkspaceScopedBackend(CompositeBackend):
    """A `CompositeBackend` that refuses host paths instead of re-rooting them.

    `glob` and `grep` are deduplicated. They merge every backend's answer, and two
    of the routes here point *inside* the default backend's own root -- `/data` and
    `/memory` are real directories under the session -- so each saw the same file
    twice: measured, one file supplied with `--data` came back as two matches with
    one path between them, on every pattern.
    """

    def glob(self, pattern: str, path: str | None = None) -> Any:
        """Every match, minus the ones a second backend already gave."""
        return _once(super().glob(pattern, path), key=lambda one: one.get("path"))

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> Any:
        """The same, keyed by the line rather than the file."""
        return _once(
            super().grep(pattern, path, glob, max_count=max_count),
            key=lambda one: (one.get("path"), one.get("line"), one.get("text")),
        )

    def __init__(
        self,
        default: Any,
        routes: dict[str, Any],
        *,
        workspace: Path,
    ) -> None:
        super().__init__(default=default, routes=routes)
        self.workspace = workspace

    def _get_backend_and_key(self, key: str) -> tuple[Any, str]:
        reject_host_path(key, self.workspace)
        return super()._get_backend_and_key(key)


def _bundles_with_skills(catalogue: Definitions) -> tuple[Any, ...]:
    """Every bundle that has skills to mount, or none."""
    try:
        bundles = catalogue.subagents.bundles
    except SubagentError:
        # A catalogue that will not parse has no bundles to mount, and this is
        # not the place that says so. `--list` exists to be run *because*
        # something is broken -- it catches the loader error and prints it over
        # the rest of the inventory -- and it builds a backend on the way.
        # Raising here took that listing down with it, which is the same shape
        # as warming inside `resolve_definitions`, and a test caught that one
        # too. `warm()` still refuses at startup, so nothing is being excused.
        return ()
    return tuple(bundle for bundle in bundles.values() if bundle.skills)


def bundled_skills_route(where: str) -> str:
    """The route one bundle's skills are mounted at."""
    return f"{BUNDLED_SKILLS_ROUTE}{where}/"


def bundled_skill_mounts(where: str, directories: tuple[Path, ...]) -> tuple[tuple[str, Path], ...]:
    """`(route, directory)` for each of one bundle's skill directories.

    One directory keeps the bundle's own route. Several are numbered beneath it in
    the order the definition listed them, because a directory's name is no label:
    a package's are all called `skills`. Beneath rather than beside, so each stays
    under `/skills/` and inherits its read-only rule.
    """
    if len(directories) == 1:
        return ((bundled_skills_route(where), directories[0]),)
    return tuple(
        (bundled_skills_route(f"{where}/{n}"), directory)
        for n, directory in enumerate(directories, start=1)
    )


def skills_sources(folders: tuple[str, ...] = ()) -> list[tuple[str, str]]:
    """Every place the agent should look for skills, labelled."""
    if RESERVED_SKILL_FOLDER in folders:
        # Refused rather than skipped: `subagents/` under the skills root would
        # shadow *every* bundle at once, so the skills of every delegate that has
        # any would silently stop being found. A folder that vanishes is the
        # failure this package keeps naming, and the fix is one rename.
        msg = (
            f"the skills catalogue has a folder called {RESERVED_SKILL_FOLDER!r}, "
            f"which is where each subagent's own skills are mounted "
            f"({BUNDLED_SKILLS_ROUTE}). Rename it -- left as it is, it would hide "
            "every bundled skill in the deployment"
        )
        raise ConfigError(msg)
    return [
        (SKILLS_ROUTE, "catalogue"),
        *((f"{SKILLS_ROUTE}{name}/", name) for name in folders),
    ]


#: The one memory file the agent is told to read. `/memory/` is a route so a
#: request that declined memory can be given a deny rule for it.
MEMORY_SOURCES = [f"{MEMORY_ROUTE}AGENTS.md"]


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
    # -- measured, before the fence: tenant B read tenant A's `derived/secret.txt` with
    # `cat ../<A>/...`, exit 0.
    readable = [*confinement.toolchain_roots(cfg.shell_path_extra), *skills]
    # Nothing beyond the session, which both fences grant already: `TMPDIR` is
    # inside it now, so the writable set is exactly the session this turn owns.
    writable: list[Path] = []

    if confined.mechanism == confinement.BUBBLEWRAP:
        from kingfisher.infrastructure.sandbox.bubblewrap import (  # noqa: PLC0415
            BubblewrapRunner,
            argv_for,
        )

        return BubblewrapRunner(
            argv_for(session_dir, readable=readable, writable=writable),
            env=env,
        )

    # Imported here for the reason the module explains: `sandlock` is a
    # Linux-only optional install, and this function is called on every turn on
    # every platform.
    from kingfisher.infrastructure.sandbox.fence import LandlockRunner, policy_for  # noqa: PLC0415

    return LandlockRunner(
        policy_for(session_dir, readable=readable, writable=writable),
        cwd=session_dir,
        env=env,
    )


def _require_layout(session_dir: Path) -> None:
    """Refuse a session directory that has not been made yet."""
    missing = [
        name
        for name in (*SESSION_DIRS, HARNESS)
        if not (session_dir / name).is_dir()
    ]
    if missing:
        msg = (
            f"session directory {session_dir} is missing {', '.join(missing)}; "
            "call ensure_session_layout on it before building a backend"
        )
        raise ValueError(msg)


class BackendFactory(Protocol):
    """How a deployment says what filesystem its agents run on.

    Typed against the call and not the return, which is the asymmetry worth
    knowing. deepagents decides what a backend *is* with `isinstance` against its
    own abstract base class, so a protocol describing the return would describe the
    requirement wrongly -- an object satisfying it exactly would still be handed no
    shell. `refuse_unusable_backend` asks that question at the only time it can be
    answered, which is once there is an object to ask about.

    What a type can settle is the call, and there is one mistake here it is the only
    thing that can catch. A factory written without `runner` drops the
    `CommandRunner` the deployment wired, and nothing downstream can tell: the
    backend that comes back is well-formed and passes every check, and merely runs
    its commands somewhere the deployment did not choose.
    """

    def __call__(
        self,
        cfg: Config,
        session_dir: Path,
        # Positional-only, or this would be dictating parameter *names*: a protocol
        # matches those, so without the slash a deployment whose factory reads
        # `(config, where)` fails to satisfy it for no reason anybody could act on.
        # Both are passed positionally, so nothing is given up.
        /,
        *,
        catalogue: Definitions | None = None,
        runner: CommandRunner | None = None,
    ) -> Any: ...


def default_backend(
    cfg: Config,
    session_dir: Path,
    *,
    catalogue: Definitions | None = None,
    runner: CommandRunner | None = None,
) -> WorkspaceScopedBackend:
    """Kingfisher's own backend, rooted at one session.

    Typed to the class rather than to deepagents' protocol because a deployment
    adjusting what this built reads `default` and `routes` off it, and the protocol
    carries neither.
    """
    skills = (catalogue or Definitions.from_config(cfg)).skills
    skills_dir = skills.root
    # Before anything is mounted: a label becomes a route segment below, and a bad
    # one would otherwise reach the composite as a path.
    refuse_unmountable(skills_dir, skills.mounts)

    _require_layout(session_dir)
    # `FilesystemBackend` wants the root to exist. A *supplied* catalogue was
    # already refused by `resolve_definitions` if it did not, so this only ever
    # creates a derived one -- and stays here for the callers that build a
    # backend directly, without a service to have resolved anything for them.
    skills_dir.mkdir(parents=True, exist_ok=True)
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
        warnings.warn(confined.warning, stacklevel=1)
    shell = ConfinedLocalShellBackend(
        confined,
        runner=chosen,
        root_dir=str(session_dir),
        env=env,
        timeout=cfg.execution_timeout_s,
    )
    # What backs each path. Keyed by the table rather than written as one dict
    # so that a route declared in `kingfisher.layout` and forgotten here raises when
    # the backend is built, instead of reaching a turn as a path that resolves
    # to the default backend and quietly ignores its own deny rule.
    backing = {
        DATA_ROUTE: lambda: FilesystemBackend(root_dir=str(session_dir / DATA)),
        SKILLS_ROUTE: lambda: FilesystemBackend(root_dir=str(skills_dir)),
        MEMORY_ROUTE: lambda: FilesystemBackend(root_dir=str(session_dir / MEMORY)),
        # Mounted so it can be refused. Every operation through it is denied by
        # `read_only_permissions`, and a rule is only expressible against a path
        # the composite routes -- so the mount is what makes the refusal legal,
        # not a way in.
        HARNESS_ROUTE: lambda: FilesystemBackend(root_dir=str(session_dir / HARNESS)),
    }
    missing = [path for path in routed_paths() if path not in backing]
    if missing:  # pragma: no cover -- a table edit, caught by its own test
        msg = f"routes declared in kingfisher.layout with nothing to back them: {missing}"
        raise ConfigError(msg)

    routes: dict[str, Any] = {path: backing[path]() for path in routed_paths()}
    # One per bundle, so a delegate's own skills are readable by the file tools
    # that read every other skill -- and read-only for the same two reasons,
    # since both enforcement points are scoped to `/skills/` and this sits
    # underneath it. Generated rather than declared: the names are not known
    # until a catalogue is read, which is what `family=True` marks.
    routes.update({
        route: FilesystemBackend(root_dir=str(directory))
        for bundle in _bundles_with_skills(catalogue or Definitions.from_config(cfg))
        for route, directory in bundled_skill_mounts(bundle.where, bundle.skills)
    })
    # One per mount, under `/skills/` for the bundles' reason. The registry lists a
    # mount under this same segment, so the deny rule for a skill in it lands here.
    routes.update({
        f"{SKILLS_ROUTE}{label}/": FilesystemBackend(root_dir=str(mount))
        for label, mount in skills.mounts.items()
    })

    return WorkspaceScopedBackend(default=shell, routes=routes, workspace=session_dir)
