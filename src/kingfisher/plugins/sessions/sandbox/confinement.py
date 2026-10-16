"""Keeping the shell out of everything that is not the workspace.

`virtual_mode` roots the *file tools* at a session. It does nothing to `execute`,
which deepagents documents plainly: commands run through `subprocess.run(shell=True)`
and can "access any file on the filesystem (regardless of `virtual_mode`)", with the
advice to pair it with a human-in-the-loop. A harness that runs unattended has no
such reviewer, so the boundary has to come from the operating system instead.

What that boundary is worth closing was measured rather than assumed. With nothing in
place the agent's shell could read the deployment's own `.env` -- both API keys --
along with `~/.aws` and `~/.config/gh`, where the GitHub CLI keeps its token.
A workspace tool may fetch a URL, so reading and sending are one turn apart for
anything that gets an injection into a document.
"""

from __future__ import annotations

import ctypes
import platform
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path

from kingfisher.core.config import Config
from kingfisher.core.layout import HARNESS_OWNED, INPUTS, SANDBOX_PROFILE, SAVED_RESULTS
from kingfisher.plugins.sessions.sandbox.sandbox_exec import (
    fenced_sessions,
    profile,
    profile_path,
    wrapper,
    write_atomically,
)

#: `landlock_create_ruleset`, which is 444 on every architecture that has it --
#: the three Landlock calls were added to the syscall table in one go rather
#: than per-architecture, so there is no table to carry here.
_LANDLOCK_CREATE_RULESET = 444
#: Ask for the ABI version instead of creating anything. With this flag the call
#: takes a NULL attribute pointer and a zero size, and returns the version.
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
#: The runtime already confines this process -- a container mounting only the
#: workspace. Nothing is wrapped and nothing is warned about, because the
#: deployment has asserted the boundary exists somewhere this code cannot see.
EXTERNAL = "external"
#: bubblewrap: a mount namespace with no network. `AUTO` falls back to it where
#: Landlock cannot run; naming it chooses it even where Landlock would, which is
#: how a deployment closes the shell's network. Probed rather than trusted either
#: way, because it works only in a container whose seccomp profile permits user
#: namespaces. See `plugins/sessions/sandbox/bubblewrap.py`.
BUBBLEWRAP = "bubblewrap"
#: Deliberately unconfined. Warned about on every start, because an exposure
#: nobody is reminded of is one nobody fixes.
OFF = "off"

MODES = (AUTO, BUBBLEWRAP, EXTERNAL, OFF)

#: What `Confinement.mechanism` can say. A separate vocabulary from `MODES`, which
#: reads like a coincidence because bubblewrap is in both and is not one: a mode is
#: what a deployment asks for, and a mechanism is what ended up confining the command.
#: `AUTO` produces every one of these and is not any of them.
#:
#: Named because three files outside this one branched on the spellings -- `fence_for`
#: twice, `doctor` once -- and a mechanism renamed here would have gone on reading as
#: "not a fence" at each of them, which is a shell running unfenced and reported as
#: confined.
LANDLOCK = "Landlock"
SANDBOX_EXEC = "sandbox-exec"

#: The two this process builds and runs itself, which is the question `fence_for`
#: asks. `SANDBOX_EXEC` is not one: it confines by wrapping the command string.
LINUX_FENCES = (LANDLOCK, BUBBLEWRAP)


@dataclass(frozen=True)
class Confinement:
    """How to run one command, and what to say about it at startup."""

    #: Wraps a command so it runs confined. Identity when nothing is applied.
    wrap: Callable[[str], str]
    #: Empty when the shell is confined, or when a deployment has said it is
    #: confined elsewhere. Non-empty text is what `LocalSessions` warns with,
    #: once per process, and what `doctor` reports.
    warning: str = ""
    #: The deployment asserted a boundary this code cannot see -- a container mounting
    #: only the workspace. Nothing is wrapped and nothing is wrong.
    elsewhere: bool = False
    #: What is doing the confining, named. Empty when nothing is.
    mechanism: str = ""
    #: A deployment provided the `CommandRunner`, so what actually runs a command is
    #: code this process cannot inspect.
    supplied: bool = False
    #: The profile the command runs under, for the mechanism that reads one. Carried
    #: because its name is a digest of its rules, so nothing else can spell it.
    profile: Path | None = None

    @property
    def confined(self) -> bool:
        """Whether *this* process confines the command. See `elsewhere` for the
        other way a shell can be safe."""
        return self.warning == "" and (self.mechanism != "" or self.wrap is not _unwrapped)


def with_supplied_runner(confined: Confinement, *, local: bool) -> Confinement:
    """What is confining the shell, once a deployment supplies the runner.

    Measured before this existed: a runner declaring `local = False` ran the command
    with no wrap applied, and the `Confinement` still reported
    `mechanism='sandbox-exec'` and `confined=True`. The claim was false inside the
    process, not merely invisible to `doctor` -- anything reading it got a wrong
    answer, and a check that reports a fence which is not running is worse than one
    that reports nothing.
    """
    if local:
        return replace(confined, supplied=True)
    return Confinement(wrap=_unwrapped, elsewhere=True, supplied=True)


def _unwrapped(command: str) -> str:
    return command


#: What `sandlock` requires for its full ruleset -- its README says 6, and
#: `min_landlock_abi()` says so at runtime once it is installed. Kept as a number
#: here because the check has to answer on hosts where it is *not* installed,
#: which is the case the answer matters most in.
REQUIRED_LANDLOCK_ABI = 6


def sandlock_minimum() -> int | None:
    """The ABI the installed `sandlock` demands, or `None` when it cannot say.

    `None` covers both "not installed" and "imported, and the native half will not
    answer", which are one fact to a caller deciding whether a fence exists: a wheel
    that unpacked is not a fence. Asking the library is what makes this a check at
    all -- `REQUIRED_LANDLOCK_ABI` is a number in this file, and a number cannot
    notice the release that raised it.
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
    # Against the installed library's own minimum rather than the constant above. A
    # release that raises it leaves `REQUIRED_LANDLOCK_ABI` describing a version
    # nobody has, and the failure runs the wrong way: `auto` picks Landlock, `doctor`
    # reports a fence, and every command dies inside `confine` instead.
    wants = sandlock_minimum()
    return wants is not None and abi >= wants


def _bubblewrap() -> Confinement:
    """The bubblewrap mode, probed rather than trusted."""
    from kingfisher.plugins.sessions.sandbox.bubblewrap import bubblewrap_available  # noqa: PLC0415

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

    Landlock first because it costs nothing -- no capability, no container change, no
    relaxed syscall filter -- and because it fails in the right direction: measured,
    it denies the path resolution `mount(2)` needs, so a fenced process cannot spend
    `SYS_ADMIN` even where the container has it. A fence that requires the operator
    to do something is a fence that is off in most deployments.

    bubblewrap second, and only when Landlock cannot run at all. That is kernels
    below the ABI a full ruleset needs -- 6.12, where EKS nodes are commonly on 6.1
    -- and on exactly those nodes the alternative was *nothing*: the shell read every
    session's files and `doctor` said so. Something beats that.
    """
    if landlock_ready():
        return Confinement(wrap=_unwrapped, mechanism=LANDLOCK)
    # Through `_bubblewrap` rather than probing again, so there is one place
    # that decides whether bubblewrap works here. Two would eventually disagree,
    # and the disagreement would be `doctor` naming a fence that is not running.
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


def shell_confinement(cfg: Config, *, skills: tuple[Path, ...] | None = None) -> Confinement:
    """The confinement this deployment will actually use, from its `Config`."""
    return resolve(
        cfg.shell_sandbox,
        workspace=cfg.workspace,
        extra=cfg.shell_path_extra,
        skills=(cfg.skills_dir, *cfg.skills_mounts.values()) if skills is None else skills,
        definitions=tuple(cfg.catalogue_roots.values()),
        # The keys file joins them here rather than in `authored_files`, which names
        # what sits in the definitions folder -- and a keys file is kept out of that
        # shared folder. The reason to protect it is the one `profile` already
        # gives for `models.yaml`: a shell that can edit it picks the key the next start
        # sends, and one that can make it unparseable refuses the start outright.
        #
        # macOS only, and nothing is missing on Linux: there the whole workspace is
        # writable and this list is what carves exceptions out of it, while both Linux
        # fences grant the session and nothing above it.
        authored=(*cfg.authored_files.values(), *_keys_file(cfg)),
        # The one renamable leaf this profile names: `sessions/*/<inputs>` is denied
        # writes, so a renamed inputs folder is the one the kernel protects.
        session_inputs=cfg.layout.inputs,
        saved_results=cfg.layout.saved_results,
    )


#: The mechanisms that keep the shell out of `.harness` on kingfisher's own authority:
#: `_session_folder_denial` in the macOS profile, the read-only bind in `bubblewrap.py`, and
#: Landlock granting each session directory but never the session itself.
FENCES_HARNESS = (SANDBOX_EXEC, BUBBLEWRAP, LANDLOCK)


def harness_unfenced(cfg: Config) -> str | None:
    """Why nothing kingfisher runs keeps the shell out of `.harness`, or `None`."""
    if shell_confinement(cfg).mechanism in FENCES_HARNESS:
        return None
    return (
        f"KINGFISHER_SHELL_SANDBOX is {cfg.shell_sandbox!r}, and nothing kingfisher "
        "runs keeps the agent's shell out of .harness here"
    )


def toolchain_roots(extra: tuple[str, ...] = ()) -> tuple[Path, ...]:
    """Where the interpreter the shell was told to run actually lives."""
    roots = [Path(sys.prefix), Path(sys.base_prefix)]
    roots += [Path(e) for e in extra]
    return tuple(dict.fromkeys(p.resolve() for p in roots if str(p)))


def readable_roots(workspace: Path, extra: tuple[str, ...] = (),
                   skills: tuple[Path, ...] = ()) -> tuple[Path, ...]:
    """What has to stay readable for the shell to remain useful.

    The sessions root among them, for the reason `fenced_sessions` gives: reached
    through a link it is not under the workspace, and under the home folder a shell
    that cannot read its own session cannot even start in it.
    """
    roots = [Path(workspace), fenced_sessions(workspace), *toolchain_roots(extra),
             *map(Path, skills)]
    return tuple(dict.fromkeys(p.resolve() for p in roots if str(p)))


def shell_readable(cfg: Config, mechanism: str) -> tuple[Path, ...]:
    """What `mechanism` lets the shell read, from the lists each fence is built from.

    The session directory is left out: what asks this is about a file of the
    deployment's, which no session's directory holds.
    """
    skills = (cfg.skills_dir, *cfg.skills_mounts.values())
    if mechanism == SANDBOX_EXEC:
        return readable_roots(cfg.workspace, cfg.shell_path_extra, skills)
    if mechanism in LINUX_FENCES:
        # Each fence's own list, not one shared: bubblewrap binds no `/proc` or `/dev`,
        # and one list serving both is how a mechanism gains a path it meant to deny.
        from kingfisher.plugins.sessions.sandbox import bubblewrap, fence  # noqa: PLC0415

        system = fence.SYSTEM_PATHS if mechanism == LANDLOCK else bubblewrap.SYSTEM_PATHS
        granted = [*map(Path, system), *toolchain_roots(cfg.shell_path_extra), *map(Path, skills)]
        return tuple(dict.fromkeys(p.resolve() for p in granted if str(p)))
    return ()


def shell_reads(
    path: Path, *, mechanism: str, home: Path, readable: tuple[Path, ...]
) -> str | None:
    """Why the agent's shell can read `path` under `mechanism`, or `None` where it cannot.

    By each fence's shape rather than by asking one. `sandbox-exec` allows everything,
    denies `home` and gives `readable` back, so a file outside the home folder is
    readable with the sandbox on; the Linux fences grant `readable` and nothing else.
    Any other mechanism is no fence.
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


def writable_roots(workspace: Path) -> tuple[Path, ...]:
    """Everywhere the shell is allowed to write.

    The workspace, and the sessions root where that is somewhere else. `TMPDIR` used
    to be named beside it, because `KINGFISHER_SCRATCH_DIR` could move it out of the
    workspace; it lives inside the session instead, so naming it again would be naming
    a subpath of the first root -- which is why the second is named only when it is not
    one. `fenced_sessions` says what makes it possible for a session to be elsewhere,
    and why granting it is half a change.
    """
    root = Path(workspace).resolve()
    sessions = fenced_sessions(workspace)
    return (root,) if sessions.is_relative_to(root) else (root, sessions)


def protected_roots(
    workspace: Path, skills: tuple[Path, ...], definitions: tuple[Path, ...]
) -> tuple[Path, ...]:
    """Everywhere inside a writable root the shell must still not write.

    The harness's own directory is one of them, which is what lets the profile
    stop naming itself by path: everything in `.kingfisher` is the harness's --
    the marker, the profile that says what the shell may do, and the skills view
    it reads mounts through -- and none of it is a session's. It could not be a
    `subpath` deny while `TMPDIR` lived under it, because the denies are written
    after the allows and would have covered the one directory the shell must be
    able to write.
    """
    roots = (
        Path(workspace) / HARNESS_OWNED,
        *map(Path, skills),
        *definitions,
    )
    return tuple(dict.fromkeys(p.resolve() for p in roots))


def resolve(  # noqa: PLR0913 -- one keyword per kind of path the rules name, as
    # `profile` has: directories denied by `subpath` and files by `path` are not one list
    mode: str, *, workspace: Path,
    extra: tuple[str, ...] = (), skills: tuple[Path, ...] = (),
    definitions: tuple[Path, ...] = (),
    authored: tuple[Path, ...] = (),
    session_inputs: str = INPUTS,
    saved_results: tuple[str, ...] = SAVED_RESULTS,
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
        # Named separately from the writable roots it is usually the first
        # of: `_session_folder_denial` builds a pattern under it, which needs the
        # workspace itself rather than whatever happens to be writable.
        workspace=workspace,
        readable=readable_roots(workspace, extra, skills),
        writable=writable_roots(workspace),
        # The catalogue is instructions the agent follows, so a writable one made a
        # skill something the agent could rewrite for every later request, including
        # in the other deployments sharing it. Read at the tool level too, by the deny
        # rule `kingfisher.core.layout` declares for this route; both are needed, because
        # the shell bypasses tool permissions entirely.
        protected=protected_roots(workspace, skills, definitions),
        # Resolved, because the profile matches real paths and a workspace under
        # `/tmp` or `/var` is really under `/private`. A file that does not exist
        # yet is named anyway: the shell must not be the one to create it.
        protected_files=tuple(dict.fromkeys(Path(p).resolve() for p in authored)),
        session_inputs=session_inputs,
        saved_results=saved_results,
    )
    # Named for every rule but the one naming the file itself, which cannot be known
    # before the name is; the profile then refuses writes to itself, so the rules
    # cannot be rewritten by the shell they bind. See `profile`.
    path = profile_path(workspace, rules(itself=Path(SANDBOX_PROFILE)))
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomically(path, rules(itself=path))
    return Confinement(wrap=wrapper(path), mechanism=SANDBOX_EXEC, profile=path)


