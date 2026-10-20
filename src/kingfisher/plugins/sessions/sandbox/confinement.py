"""Keeping the shell out of everything that is not the workspace.

`virtual_mode` roots the file tools at a session and does nothing to `execute`, which
deepagents says can "access any file on the filesystem" and pairs with a human reviewer.
An unattended harness has none, so the boundary comes from the operating system.
"""

from __future__ import annotations

import ctypes
import os
import platform
import shutil
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path

from kingfisher.core.config import Config, ConfigError
from kingfisher.core.layout import Layout

from .sandbox_exec import (
    SANDBOX_PROFILE,
    fenced_sessions,
    profile,
    profile_path,
    wrapper,
    write_atomically,
)

#: `landlock_create_ruleset`: 444 on every architecture that has it, so no table.
_LANDLOCK_CREATE_RULESET = 444
#: Ask for the ABI version, with a NULL attribute pointer and a zero size.
_LANDLOCK_ABI_QUERY = 1


def landlock_abi() -> int | None:
    """The Landlock ABI this kernel supports, or `None` where there is none."""
    if platform.system() != "Linux":
        return None
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        version = libc.syscall(_LANDLOCK_CREATE_RULESET, None, 0, _LANDLOCK_ABI_QUERY)
    except (OSError, AttributeError):  # pragma: no cover -- not reachable on Linux
        return None
    return version if version > 0 else None


#: Pick whatever the platform offers, and say so when it offers nothing.
AUTO = "auto"
#: The runtime already confines this process, as a container mounting only the
#: workspace does. Nothing is wrapped or warned about.
EXTERNAL = "external"
#: bubblewrap: a mount namespace with no network. `AUTO` falls back to it where
#: Landlock cannot run; naming it chooses it even where Landlock would, which closes the
#: shell's network. Probed either way: it needs a seccomp profile permitting user
#: namespaces.
BUBBLEWRAP = "bubblewrap"
#: Deliberately unconfined. Warned about on every start, because an exposure
#: nobody is reminded of is one nobody fixes.
OFF = "off"

MODES = (AUTO, BUBBLEWRAP, EXTERNAL, OFF)

#: What `Confinement.mechanism` can say: what ended up confining the command, where a
#: mode is what a deployment asked for. Named, not spelled, wherever it is compared: a
#: renamed one read as "not a fence" is a shell unfenced and reported confined.
LANDLOCK = "Landlock"
SANDBOX_EXEC = "sandbox-exec"

#: The two this process builds and runs itself. `SANDBOX_EXEC` is not one: it confines
#: by wrapping the command string.
LINUX_FENCES = (LANDLOCK, BUBBLEWRAP)


@dataclass(frozen=True)
class Confinement:
    """How to run one command, and what to say about it at startup."""

    #: Wraps a command so it runs confined. Identity when nothing is applied.
    wrap: Callable[[str], str]
    #: What `LocalSessions` warns with and `doctor` reports. Empty when the shell is
    #: confined, here or elsewhere.
    warning: str = ""
    #: The deployment asserted a boundary this code cannot see.
    elsewhere: bool = False
    #: What is doing the confining, named. Empty when nothing is.
    mechanism: str = ""
    #: A deployment provided the `CommandRunner`, which this process cannot inspect.
    supplied: bool = False
    #: The profile the command runs under, where there is one. Carried because its name
    #: is a digest of its rules, so nothing else can spell it.
    profile: Path | None = None

    @property
    def confined(self) -> bool:
        """Whether *this* process confines the command; `elsewhere` is the other way."""
        return self.warning == "" and self.mechanism != ""


def with_supplied_runner(confined: Confinement, *, local: bool) -> Confinement:
    """What is confining the shell, once a deployment supplies the runner.

    A runner that is not local gets no wrap, so its confinement must stop claiming one:
    reporting a fence that is not running is worse than reporting nothing.
    """
    if local:
        return replace(confined, supplied=True)
    return Confinement(wrap=_unwrapped, elsewhere=True, supplied=True)


def _unwrapped(command: str) -> str:
    return command


#: What `sandlock` requires for its full ruleset. A number here because the check has to
#: answer where `sandlock` is not installed.
REQUIRED_LANDLOCK_ABI = 6


def sandlock_minimum() -> int | None:
    """The ABI the installed `sandlock` demands, or `None` when it cannot say.

    `None` covers not installed and a native half that will not answer: either way, no
    fence. Asked, because `REQUIRED_LANDLOCK_ABI` cannot notice a release that raised it.
    """
    try:
        import sandlock  # noqa: PLC0415

        return int(sandlock.min_landlock_abi())
    except (ImportError, AttributeError, OSError, TypeError, ValueError):
        return None


def landlock_ready() -> bool:
    """Whether this host can actually fence a command, asked rather than assumed."""
    abi = landlock_abi()
    if abi is None or abi < REQUIRED_LANDLOCK_ABI:
        return False
    # Against the installed library's own minimum too: against the constant alone, a
    # release raising it has `auto` pick Landlock and every command die inside `confine`.
    wants = sandlock_minimum()
    return wants is not None and abi >= wants


def _bubblewrap() -> Confinement:
    """The bubblewrap mode, probed rather than trusted."""
    from .bubblewrap import bubblewrap_available  # noqa: PLC0415

    if bubblewrap_available():
        return Confinement(wrap=_unwrapped, mechanism=BUBBLEWRAP)
    return Confinement(
        wrap=_unwrapped,
        warning=(
            "KINGFISHER_SHELL_SANDBOX=bubblewrap was asked for and cannot be used "
            "here: either `bwrap` is not installed, or this container's seccomp "
            "profile denies `clone` with CLONE_NEWUSER, which is what Docker's "
            "default does. It needs a profile permitting that one rule; "
            "`--security-opt seccomp=unconfined` also works and gives up far more. "
            "The agent's shell is unconfined until one of those is fixed."
        ),
    )


def _linux() -> Confinement:
    """The Linux chain: Landlock, then bubblewrap, then a warning.

    Landlock first: it needs nothing of the operator, and a fence that does is off in
    most deployments. It also denies the path resolution `mount(2)` needs, so a fenced
    process cannot spend `SYS_ADMIN`. bubblewrap only where Landlock cannot run, where
    the alternative is no fence at all.
    """
    if landlock_ready():
        return Confinement(wrap=_unwrapped, mechanism=LANDLOCK)
    # Through `_bubblewrap`, not a second probe: two would disagree, and `doctor` would
    # name a fence that is not running.
    fallen_back = _bubblewrap()
    if fallen_back.confined:
        return fallen_back
    return Confinement(wrap=_unwrapped, warning=_no_landlock_here())


def _no_landlock_here() -> str:
    """Why this Linux host is unfenced, in the terms that decide what to do."""
    abi = landlock_abi()
    if abi is None:
        reason = f"this kernel ({platform.release()}) offers no Landlock"
    elif abi < REQUIRED_LANDLOCK_ABI:
        reason = (
            f"this kernel ({platform.release()}) has Landlock ABI {abi}, below the "
            f"{REQUIRED_LANDLOCK_ABI} a full ruleset needs"
        )
    elif (wants := sandlock_minimum()) is not None:
        reason = (
            f"the installed `sandlock` needs Landlock ABI {wants} and this kernel "
            f"({platform.release()}) offers {abi}"
        )
    else:
        reason = (
            "`sandlock` is not installed, or cannot run here "
            "(pip install 'kingfisher[fence]')"
        )
    return (
        f"the agent's shell is unconfined: {reason}, so it can read this host's "
        "files, including other sessions'. Run it in a container that mounts only "
        "the workspace and set KINGFISHER_SHELL_SANDBOX=external."
    )


def _keys_file(cfg: Config) -> tuple[Path, ...]:
    """The keys file, where one is named, and nothing where none is."""
    return () if cfg.models.keys_file is None else (cfg.models.keys_file,)


@dataclass(frozen=True)
class ShellSettings:
    """How kingfisher's own shell is fenced, and what it may run besides the system's.

    `LocalSessions` reads `KINGFISHER_SHELL_SANDBOX` and `KINGFISHER_SHELL_PATH_EXTRA`
    unless handed these, so the variables mean the same to the command and a deployment.
    """

    # One of `MODES`. On by default because an exposure nobody opted into is one nobody
    # knows they have: unconfined, the shell read this deployment's API keys, `~/.aws`
    # and the GitHub CLI's token, and an offered tool can send them.
    sandbox: str = AUTO
    #: Directories put on the shell's `PATH` ahead of the system's, and readable to it.
    path_extra: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # Refused, not dropped: an entry that is not absolute resolves against the
        # server's working directory, and every fence grants it to the shell to read.
        relative = [entry for entry in self.path_extra if not Path(entry).is_absolute()]
        if relative:
            msg = (
                "KINGFISHER_SHELL_PATH_EXTRA (ShellSettings.path_extra) takes absolute "
                f"directories, got {', '.join(map(repr, relative))}"
            )
            raise ConfigError(msg)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> ShellSettings:
        """What the two variables say, in `environ` or in this process's environment."""
        values = os.environ if environ is None else environ
        sandbox = (values.get("KINGFISHER_SHELL_SANDBOX") or "").strip() or AUTO
        # Here as well as in `resolve`: `LocalSessions` reads this as it is built, so a
        # typo stops the start naming the setting, where the fence raises on the first turn.
        if sandbox not in MODES:
            msg = (
                f"KINGFISHER_SHELL_SANDBOX must be one of {', '.join(MODES)}, "
                f"got {sandbox!r}"
            )
            raise ConfigError(msg)
        return cls(
            sandbox=sandbox,
            path_extra=tuple(
                part
                for part in (values.get("KINGFISHER_SHELL_PATH_EXTRA") or "").split(":")
                if part
            ),
        )


def shell_confinement(
    cfg: Config, shell: ShellSettings, *, skills: tuple[Path, ...] | None = None
) -> Confinement:
    """The confinement these shell settings give this deployment."""
    return resolve(
        shell.sandbox,
        workspace=cfg.workspace,
        sessions_root=cfg.sessions_root,
        kingfisher_dir=cfg.kingfisher_dir,
        extra=shell.path_extra,
        skills=(cfg.skills_dir, *cfg.skills_mounts.values()) if skills is None else skills,
        definitions=tuple(cfg.catalogue_roots.values()),
        # The keys file joins here, not in `authored_files`, which is the definitions
        # folder's: a shell that edits it picks the key the next start sends, or refuses
        # the start. macOS only: the Linux fences grant nothing above the session.
        authored=(*cfg.authored_files.values(), *_keys_file(cfg)),
        # So a renamed inputs folder and saved results are the ones the profile denies.
        layout=cfg.layout,
    )


#: The mechanisms that keep the shell out of `.harness` on kingfisher's own authority:
#: `_session_folder_denial` in the macOS profile, the read-only bind in `bubblewrap.py`, and
#: Landlock granting each session directory but never the session itself.
FENCES_HARNESS = (SANDBOX_EXEC, BUBBLEWRAP, LANDLOCK)


def harness_unfenced(shell: ShellSettings, confined: Confinement) -> str | None:
    """Why nothing kingfisher runs keeps the shell out of `.harness`, or `None`.

    `confined` is what `shell_confinement` gave `shell`.
    """
    if confined.mechanism in FENCES_HARNESS:
        return None
    return (
        f"KINGFISHER_SHELL_SANDBOX is {shell.sandbox!r}, and nothing kingfisher "
        "runs keeps the agent's shell out of .harness here"
    )


def toolchain_roots(extra: tuple[str, ...] = ()) -> tuple[Path, ...]:
    """Where the interpreter the shell was told to run actually lives."""
    roots = [Path(sys.prefix), Path(sys.base_prefix)]
    roots += [Path(e) for e in extra]
    return tuple(dict.fromkeys(p.resolve() for p in roots))


def readable_roots(workspace: Path, sessions_root: Path, extra: tuple[str, ...] = (),
                   skills: tuple[Path, ...] = ()) -> tuple[Path, ...]:
    """What has to stay readable for the shell to remain useful.

    The sessions root among them: reached through a link it is not under the workspace,
    and under the home folder a shell that cannot read its session cannot start in it.
    """
    roots = [Path(workspace), fenced_sessions(sessions_root), *toolchain_roots(extra),
             *map(Path, skills)]
    return tuple(dict.fromkeys(p.resolve() for p in roots))


def shell_readable(cfg: Config, shell: ShellSettings, mechanism: str) -> tuple[Path, ...]:
    """What `mechanism` lets the shell read, from the lists each fence is built from.

    Not the session directory: the question is about a deployment's file, which no
    session holds.
    """
    skills = (cfg.skills_dir, *cfg.skills_mounts.values())
    if mechanism == SANDBOX_EXEC:
        return readable_roots(cfg.workspace, cfg.sessions_root, shell.path_extra, skills)
    if mechanism in LINUX_FENCES:
        # Each fence's own list, not one shared: bubblewrap binds no `/proc` or `/dev`,
        # and one list serving both is how a mechanism gains a path it meant to deny.
        from . import bubblewrap, fence  # noqa: PLC0415

        system = fence.SYSTEM_PATHS if mechanism == LANDLOCK else bubblewrap.SYSTEM_PATHS
        granted = [*map(Path, system), *toolchain_roots(shell.path_extra), *map(Path, skills)]
        return tuple(dict.fromkeys(p.resolve() for p in granted))
    return ()


def shell_reads(
    path: Path, *, mechanism: str, home: Path, readable: tuple[Path, ...]
) -> str | None:
    """Why the agent's shell can read `path` under `mechanism`, or `None` where it cannot.

    By each fence's shape: `sandbox-exec` denies `home` and gives `readable` back, and
    the Linux fences grant `readable` and nothing else.
    """
    target = Path(path).resolve()
    under = next((root for root in readable if target.is_relative_to(root)), None)
    if mechanism == SANDBOX_EXEC:
        if not target.is_relative_to(Path(home).resolve()):
            return "it is outside the home folder, which is all the macOS sandbox hides"
        return f"it is under {under}, which the macOS sandbox gives back" if under else None
    if mechanism in LINUX_FENCES:
        return f"it is under {under}, which the {mechanism} fence grants" if under else None
    return "nothing kingfisher runs confines the agent's shell here"


def writable_roots(workspace: Path, sessions_root: Path) -> tuple[Path, ...]:
    """Everywhere the shell is allowed to write: the workspace, and the sessions root
    where that is outside it."""
    root = Path(workspace).resolve()
    sessions = fenced_sessions(sessions_root)
    return (root,) if sessions.is_relative_to(root) else (root, sessions)


def protected_roots(
    kingfisher_dir: Path, skills: tuple[Path, ...], definitions: tuple[Path, ...]
) -> tuple[Path, ...]:
    """Everywhere inside a writable root the shell must still not write.

    `kingfisher_dir` whole: the marker, the profile and the skills view are the
    harness's, so nothing a session writes may live there.
    """
    roots = (
        Path(kingfisher_dir),
        *map(Path, skills),
        *definitions,
    )
    return tuple(dict.fromkeys(p.resolve() for p in roots))


def resolve(  # noqa: PLR0913 -- one keyword per kind of path the rules name
    # `subpath` directories and `path` files are not one list
    mode: str, *, workspace: Path, sessions_root: Path, kingfisher_dir: Path,
    extra: tuple[str, ...] = (), skills: tuple[Path, ...] = (),
    definitions: tuple[Path, ...] = (),
    authored: tuple[Path, ...] = (),
    layout: Layout,
) -> Confinement:
    """Choose a confinement for this deployment, writing any profile it needs."""
    if mode == BUBBLEWRAP:
        return _bubblewrap()

    if mode == EXTERNAL:
        return Confinement(wrap=_unwrapped, elsewhere=True)
    if mode == OFF:
        return Confinement(
            wrap=_unwrapped,
            warning="the agent's shell is unconfined: it can read this host's files, "
                    "including credentials. Set KINGFISHER_SHELL_SANDBOX=auto to confine it.",
        )
    if mode != AUTO:
        msg = f"unknown shell sandbox mode {mode!r}; expected one of {MODES}"
        raise ValueError(msg)

    if platform.system() == "Linux":
        return _linux()

    if platform.system() != "Darwin" or not shutil.which("sandbox-exec"):
        return Confinement(
            wrap=_unwrapped,
            warning=f"no shell confinement is wired for {platform.system()}, so the "
                    "agent's shell can read this host's files, including credentials. "
                    "Run it in a container that mounts only the workspace and set "
                    "KINGFISHER_SHELL_SANDBOX=external.",
        )

    home = Path.home().resolve()
    rules = partial(
        profile,
        home=home,
        # Apart from the writable roots: the session-folder patterns are built under the
        # sessions root itself.
        sessions_root=sessions_root,
        readable=readable_roots(workspace, sessions_root, extra, skills),
        writable=writable_roots(workspace, sessions_root),
        # The catalogue is instructions the agent follows: writable, a skill would be the
        # agent's to rewrite for every later request. The layout's deny rule covers the
        # file tools; this covers the shell, which bypasses tool permissions.
        protected=protected_roots(kingfisher_dir, skills, definitions),
        # Resolved: the profile matches real paths, and `/tmp` or `/var` is really under
        # `/private`. A file not there yet is named anyway, so the shell cannot create it.
        protected_files=tuple(dict.fromkeys(Path(p).resolve() for p in authored)),
        layout=layout,
    )
    # Named for every rule but the one naming the file itself, which cannot be known
    # first; the profile then refuses writes to itself, so the shell cannot rewrite it.
    path = profile_path(kingfisher_dir, rules(itself=Path(SANDBOX_PROFILE)))
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomically(path, rules(itself=path))
    return Confinement(wrap=wrapper(path), mechanism=SANDBOX_EXEC, profile=path)


