"""Keeping the shell out of everything that is not the workspace.

`virtual_mode` roots the *file tools* at a session. It does nothing to
`execute`, which deepagents documents plainly: commands run through
`subprocess.run(shell=True)` and can "access any file on the filesystem
(regardless of `virtual_mode`)", with the advice to pair it with a
human-in-the-loop. A harness that runs unattended has no such reviewer, so the
boundary has to come from the operating system instead.

What that boundary is worth closing was measured rather than assumed. With
nothing in place the agent's shell could read the deployment's own `.env` --
both API keys -- along with `~/.aws` and `~/.config/gh`, where the GitHub CLI
keeps its token. `http_fetch` is a registered tool, so reading and sending are
one turn apart for anything that gets an injection into a document.

Redirecting `HOME` at the workspace, which `shell_env` already does, is not this
guarantee and was never able to be: it only moves where tools *resolve* `~`. The
files stay where they are and an absolute path still reaches them.

Deployments differ in who provides the boundary, which is why this is a choice
rather than a constant. A container that mounts only the workspace has already
provided it and should not pay for a second one; a developer's machine has
provided nothing at all. `EXTERNAL` is the first case said out loud, so that
"nothing is wrapping the shell" can mean "the runtime already did it" instead of
being indistinguishable from nobody having thought about it.
"""

from __future__ import annotations

import platform
import shlex
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

#: Pick whatever the platform offers, and say so when it offers nothing.
AUTO = "auto"
#: The runtime already confines this process -- a container mounting only the
#: workspace. Nothing is wrapped and nothing is warned about, because the
#: deployment has asserted the boundary exists somewhere this code cannot see.
EXTERNAL = "external"
#: Deliberately unconfined. Warned about on every start, because an exposure
#: nobody is reminded of is one nobody fixes.
OFF = "off"

MODES = (AUTO, EXTERNAL, OFF)


@dataclass(frozen=True)
class Confinement:
    """How to run one command, and what to say about it at startup."""

    #: Wraps a command so it runs confined. Identity when nothing is applied.
    wrap: Callable[[str], str]
    #: Empty when the shell is confined, or when a deployment has said it is
    #: confined elsewhere. Non-empty text is printed once at startup.
    warning: str = ""

    @property
    def confined(self) -> bool:
        return self.warning == "" and self.wrap is not _unwrapped


def _unwrapped(command: str) -> str:
    return command


def profile(*, home: Path, readable: tuple[Path, ...], writable: tuple[Path, ...]) -> str:
    """A `sandbox-exec` profile denying the operator's home, minus what runs code.

    Deny-the-home rather than allow-only-the-workspace, deliberately. An
    allow-list is the stronger shape and the one to grow into, but it fails
    closed on every path a workload happens to need -- fonts, certificates, a
    homebrew prefix -- and this is on by default. Denying the one directory
    where a person's credentials actually live closes the measured hole at a
    fraction of the breakage risk.

    The re-allowed paths are not a convenience. A virtualenv's `python3` is
    typically a symlink onto an interpreter installed under the home -- uv puts
    it in `~/.local/share/uv/python/...` -- so denying the home without
    re-allowing `sys.base_prefix` leaves the agent unable to run Python at all.
    That was found by doing it.

    Writes are the other direction and take the opposite shape: an allow-list,
    denied from `/` down. `system.md` already tells the agent to stop rather
    than reach outside the workspace, and to write scratch under `$TMPDIR`
    rather than a literal `/tmp`. Both were ignored inside a single observed
    run -- it wrote `/tmp/preview.pdf` and twice tried `pip install`, which
    failed only because this venv happens to have no `pip`. Prose that the
    model overrides is not a boundary; this is the same rules with the kernel
    behind them.

    An allow-list is affordable here where it was not for reads, because what a
    shell legitimately writes to is short and known: the workspace, `$TMPDIR`,
    and the character devices that make `2>/dev/null` work.

    The known cost, stated rather than discovered later: a program that insists
    on writing outside the workspace stops working. Headless Chrome is the case
    to expect -- an observed run used it to render HTML, and it wants socket and
    profile directories of its own. Pointing such a tool at `$TMPDIR` or a
    directory inside the workspace is usually enough. This was not proven either
    way here, because Chrome hung on this machine unsandboxed too, and a
    baseline that does not run is not a comparison.
    """
    lines = [
        "(version 1)",
        "(allow default)",
        f"(deny file-read* (subpath {_sb(home)}))",
    ]
    lines += [f"(allow file-read* (subpath {_sb(p)}))" for p in readable]
    lines.append('(deny file-write* (subpath "/"))')
    lines += [f"(allow file-write* (subpath {_sb(p)}))" for p in writable]
    # `2>/dev/null` is in half the commands an agent writes, and stdout and
    # stderr are themselves entries here.
    lines.append('(allow file-write* (subpath "/dev"))')
    return "\n".join(lines) + "\n"


def _sb(path: Path) -> str:
    """A path as a sandbox-profile string literal."""
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def readable_roots(workspace: Path, extra: tuple[str, ...] = ()) -> tuple[Path, ...]:
    """What has to stay readable for the shell to remain useful.

    `sys.prefix` is the virtualenv, `sys.base_prefix` the interpreter it was
    built from; they differ exactly when a venv is in use, which is the case
    that breaks. `extra` carries `shell_path_extra`, since a deployment that
    added a directory to the agent's `PATH` meant for it to be runnable.
    """
    roots = [Path(workspace), Path(sys.prefix), Path(sys.base_prefix)]
    roots += [Path(e) for e in extra]
    return tuple(dict.fromkeys(p.resolve() for p in roots if str(p)))


def writable_roots(workspace: Path, scratch: Path) -> tuple[Path, ...]:
    """Everywhere the shell is allowed to write.

    `scratch` is named separately rather than assumed to sit inside the
    workspace, because `KINGFISHER_SCRATCH_DIR` and `KINGFISHER_STATE_DIR` can
    move it out. It is the directory handed to the shell as `TMPDIR`, so leaving
    it off would deny the one place the prompt tells the agent to put scratch.
    """
    roots = (Path(workspace), Path(scratch))
    return tuple(dict.fromkeys(p.resolve() for p in roots))


def _sandbox_exec(profile_path: Path) -> Callable[[str], str]:
    def wrap(command: str) -> str:
        # The inner `/bin/sh -c` is what deepagents would have run anyway; the
        # quoting keeps the agent's command one argument rather than letting its
        # operators reach the outer shell.
        return f"sandbox-exec -f {shlex.quote(str(profile_path))} /bin/sh -c {shlex.quote(command)}"

    return wrap


def resolve(mode: str, *, workspace: Path, state_dir: Path, scratch_dir: Path,
            extra: tuple[str, ...] = ()) -> Confinement:
    """Choose a confinement for this deployment, writing any profile it needs.

    The profile is written under the harness's own state directory rather than
    the workspace: it is host-side configuration, and a file the agent could
    edit is not a boundary.
    """
    if mode == EXTERNAL:
        return Confinement(wrap=_unwrapped)
    if mode == OFF:
        return Confinement(
            wrap=_unwrapped,
            warning="the agent's shell is unconfined: it can read this host's files, "
                    "including credentials. Set KINGFISHER_SHELL_SANDBOX=auto to confine it.",
        )
    if mode != AUTO:
        msg = f"unknown shell sandbox mode {mode!r}; expected one of {MODES}"
        raise ValueError(msg)

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
    path.write_text(
        profile(
            home=home,
            readable=readable_roots(workspace, extra),
            writable=writable_roots(workspace, scratch_dir),
        ),
        encoding="utf-8",
    )
    return Confinement(wrap=_sandbox_exec(path))
