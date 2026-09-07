"""Keeping the shell out of everything that is not the workspace.

`virtual_mode` roots the *file tools* at a session. It does nothing to `execute`,
which deepagents documents plainly: commands run through `subprocess.run(shell=True)`
and can "access any file on the filesystem (regardless of `virtual_mode`)", with the
advice to pair it with a human-in-the-loop. A harness that runs unattended has no
such reviewer, so the boundary has to come from the operating system instead.

What that boundary is worth closing was measured rather than assumed. With nothing in
place the agent's shell could read the deployment's own `.env` -- both API keys --
along with `~/.aws` and `~/.config/gh`, where the GitHub CLI keeps its token.
`http_fetch` is a registered tool, so reading and sending are one turn apart for
anything that gets an injection into a document.
"""

from __future__ import annotations

import ctypes
import os
import platform
import shlex
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from kingfisher.config import Config

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
#: bubblewrap: a mount namespace with no network, for kernels Landlock cannot
#: reach. Named explicitly and never chosen by `AUTO`, for the same reason
#: `EXTERNAL` is: it depends on the container having been started with its
#: syscall filter relaxed, which is a fact about the deployment and not about
#: this host. `AUTO` selecting it would mean kingfisher betting on something it
#: cannot check, and losing that bet looks like a shell that reports confined
#: and fails at its first command. See `infrastructure/sandbox/bubblewrap.py`.
BUBBLEWRAP = "bubblewrap"
#: Deliberately unconfined. Warned about on every start, because an exposure
#: nobody is reminded of is one nobody fixes.
OFF = "off"

MODES = (AUTO, BUBBLEWRAP, EXTERNAL, OFF)


@dataclass(frozen=True)
class Confinement:
    """How to run one command, and what to say about it at startup."""

    #: Wraps a command so it runs confined. Identity when nothing is applied.
    wrap: Callable[[str], str]
    #: Empty when the shell is confined, or when a deployment has said it is
    #: confined elsewhere. Non-empty text is printed once at startup.
    warning: str = ""
    #: The deployment asserted a boundary this code cannot see -- a container mounting
    #: only the workspace. Nothing is wrapped and nothing is wrong.
    elsewhere: bool = False
    #: What is doing the confining, named. Empty when nothing is.
    mechanism: str = ""
    #: A deployment provided the `CommandRunner`, so what actually runs a command is
    #: code this process cannot inspect.
    supplied: bool = False

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


def landlock_ready() -> bool:
    """Whether this host can actually fence a command, asked rather than assumed."""
    if landlock_abi() is None or (landlock_abi() or 0) < REQUIRED_LANDLOCK_ABI:
        return False
    try:
        import sandlock  # noqa: F401, PLC0415
    except ImportError:
        return False
    return True


def _bubblewrap() -> Confinement:
    """The bubblewrap mode, probed rather than trusted."""
    from kingfisher.infrastructure.sandbox.bubblewrap import bubblewrap_available  # noqa: PLC0415

    if bubblewrap_available():
        return Confinement(wrap=_unwrapped, mechanism="bubblewrap")
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
        return Confinement(wrap=_unwrapped, mechanism="Landlock")
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
    else:
        reason = "`sandlock` is not installed (pip install 'kingfisher[fence]')"
    return (
        f"the agent's shell is unconfined: {reason}, so it can read this host's "
        "files, including other sessions'. Run it in a container that mounts only "
        "the workspace and set KINGFISHER_SHELL_SANDBOX=external."
    )


def shell_confinement(cfg: Config, *, skills: Path | None = None) -> Confinement:
    """The confinement this deployment will actually use, from its `Config`."""
    return resolve(
        cfg.shell_sandbox,
        workspace=cfg.workspace,
        state_dir=cfg.state_dir,
        extra=cfg.shell_path_extra,
        skills=cfg.skills_dir if skills is None else skills,
        definitions=tuple(cfg.catalogue_roots.values()),
    )


def profile(
    *,
    home: Path,
    readable: tuple[Path, ...],
    writable: tuple[Path, ...],
    itself: Path,
    protected: tuple[Path, ...] = (),
) -> str:
    """A `sandbox-exec` profile denying the operator's home, minus what runs code.

    Deny-the-home rather than allow-only-the-workspace, deliberately. An allow-list
    is the stronger shape and the one to grow into, but it fails closed on every path
    a workload happens to need -- fonts, certificates, a homebrew prefix -- and this
    is on by default. Denying the one directory where a person's credentials actually
    live closes the measured hole at a fraction of the breakage risk.

    The known cost, and it is now measured rather than guessed at: a program that
    writes to the operating system's own temp directory stops working, even when
    everything it was *told* to write is inside the workspace.

    `itself` is the path this text will be written to, and it is required rather
    than defaulted because a caller who forgets it gets no boundary at all:
    `sandbox-exec -f` re-reads the file for every command, so a shell that can
    write it runs the next command under rules of its own. It was writable --
    `state_dir` defaults inside the workspace and `writable_roots` returns the
    whole workspace, so the rules sat in the region they declared writable, and
    two commands took the home and the definition roots back.
    """
    lines = [
        "(version 1)",
        "(allow default)",
        f"(deny file-read* (subpath {_sb(home)}))",
    ]
    lines += [f"(allow file-read* (subpath {_sb(p)}))" for p in readable]
    # Re-allowing a directory inside the home re-opens the destination and not
    # the way to it, and something has to say the way is walkable. Metadata on
    # the exact directories in between, never a subpath: that is `stat`, which
    # is all a walk needs, and it leaks nothing -- the home stays unlistable and
    # every file in it stays unreadable. See `traversable`.
    lines += [
        f"(allow file-read-metadata (path {_sb(p)}))" for p in traversable(home, readable)
    ]
    lines.append('(deny file-write* (subpath "/"))')
    lines += [f"(allow file-write* (subpath {_sb(p)}))" for p in writable]
    # `2>/dev/null` is in half the commands an agent writes, and stdout and
    # stderr are themselves entries here.
    lines.append('(allow file-write* (subpath "/dev"))')
    # Last, because sandbox-exec takes the last matching rule. `protected` names
    # directories that must stay read-only even though they sit inside somewhere
    # writable -- which is the default layout for the skills catalogue, since it
    # lives in the workspace unless a deployment relocates it. Written after the
    # allows rather than instead of them: the workspace has to stay writable, and
    # only this carve-out inside it does not.
    lines += [f"(deny file-write* (subpath {_sb(p)}))" for p in protected]
    # After those, because it is the rule that keeps the rest enforceable, and
    # `path` rather than `subpath` so a relocated `TMPDIR` beside it stays
    # writable. It covers more than an overwrite: append, unlink and
    # rename-over are all `file-write*` against this name, and rename-over is
    # the one a `subpath` deny on the parent directory would have been reached
    # for to catch.
    lines.append(f"(deny file-write* (path {_sb(itself)}))")
    return "\n".join(lines) + "\n"


def _sb(path: Path) -> str:
    """A path as a sandbox-profile string literal."""
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def traversable(home: Path, readable: tuple[Path, ...]) -> tuple[Path, ...]:
    """The directories between the denied home and each root re-allowed inside it."""
    home = Path(home)
    found: dict[Path, None] = {}
    for root in readable:
        if root == home or not root.is_relative_to(home):
            continue
        for parent in root.parents:
            found.setdefault(parent, None)
            if parent == home:
                break
    return tuple(found)


def toolchain_roots(extra: tuple[str, ...] = ()) -> tuple[Path, ...]:
    """Where the interpreter the shell was told to run actually lives."""
    roots = [Path(sys.prefix), Path(sys.base_prefix)]
    roots += [Path(e) for e in extra]
    return tuple(dict.fromkeys(p.resolve() for p in roots if str(p)))


def readable_roots(workspace: Path, extra: tuple[str, ...] = (),
                   skills: Path | None = None) -> tuple[Path, ...]:
    """What has to stay readable for the shell to remain useful."""
    roots = [Path(workspace), *toolchain_roots(extra)]
    if skills is not None:
        roots.append(Path(skills))
    return tuple(dict.fromkeys(p.resolve() for p in roots if str(p)))


def writable_roots(workspace: Path) -> tuple[Path, ...]:
    """Everywhere the shell is allowed to write.

    One root now. `TMPDIR` used to be named beside it, because
    `KINGFISHER_SCRATCH_DIR` could move it out of the workspace; it lives inside
    the session instead, which is inside the workspace, so naming it again would
    be naming a subpath of what this already returns.
    """
    return (Path(workspace).resolve(),)


def protected_roots(skills: Path | None, definitions: tuple[Path, ...]) -> tuple[Path, ...]:
    """Everywhere inside a writable root the shell must still not write."""
    roots = (*((Path(skills),) if skills is not None else ()), *definitions)
    return tuple(dict.fromkeys(p.resolve() for p in roots))


def _sandbox_exec(profile_path: Path) -> Callable[[str], str]:
    def wrap(command: str) -> str:
        # The inner `/bin/sh -c` is what deepagents would have run anyway; the
        # quoting keeps the agent's command one argument rather than letting its
        # operators reach the outer shell.
        return f"sandbox-exec -f {shlex.quote(str(profile_path))} /bin/sh -c {shlex.quote(command)}"

    return wrap


def resolve(  # noqa: PLR0913 -- one parameter per root the profile has to name,
    # and each is separately relocatable by its own environment variable
    mode: str, *, workspace: Path, state_dir: Path,
    extra: tuple[str, ...] = (), skills: Path | None = None,
    definitions: tuple[Path, ...] = (),
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
    path = Path(state_dir) / "shell.sb"
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_atomically(
        path,
        profile(
            home=home,
            readable=readable_roots(workspace, extra, skills),
            writable=writable_roots(workspace),
            # The profile refuses writes to itself, so the rules cannot be
            # rewritten by the shell they bind. See `profile`.
            itself=path,
            # The catalogue is instructions the agent follows, and by default it sits
            # inside the workspace -- so "the workspace is writable" made a skill
            # something the agent could rewrite for every later request, including in
            # the other deployments sharing a relocated one. Read at the tool level too,
            # by the deny rule `kingfisher.layout` declares for this route; both are
            # needed, because the shell bypasses tool permissions entirely.
            protected=protected_roots(skills, definitions),
        ),
    )
    return Confinement(wrap=_sandbox_exec(path), mechanism="sandbox-exec")


def _write_atomically(path: Path, text: str) -> None:
    """Replace `path` with `text`, never leaving a partial file behind.

    A profile is rewritten every time a backend is built, and `sandbox-exec -f`
    reads it at the start of every command -- so a plain write, which truncates
    before it fills, gives a concurrent turn a window in which the file it is
    about to be bound by is half a profile. The content is identical between
    turns, every input to `profile` coming from one `Config`, so what this
    prevents is a torn read rather than a disagreement.

    Beside the profile rather than in a temp directory: `os.replace` is atomic
    only within a filesystem, and the state directory is relocatable.
    """
    scratch = path.with_name(f"{path.name}.{os.getpid()}")
    scratch.write_text(text, encoding="utf-8")
    scratch.replace(path)
