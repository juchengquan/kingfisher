"""The other Linux fence, for kernels Landlock cannot reach and a network to close.

Landlock stays first. This one needs only user namespaces, so it works below Landlock's
ABI 6 (Linux 6.12; EKS nodes commonly run 6.1), and `--unshare-all` closes the shell's
network -- for a script the agent writes, not for an offered tool that fetches a URL,
which runs in this process. The price is one seccomp rule, since Docker's default denies
`clone` with `CLONE_NEWUSER`.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

from kingfisher.core.layout import Layout

from .linux import present
from .process import MAX_OUTPUT_BYTES, run_command

if TYPE_CHECKING:
    import threading
    from collections.abc import Iterable, Mapping, Sequence

    from kingfisher.core.interfaces import CommandResult

#: Bound read-only so a shell can be a shell. No `/proc`: a fresh one cannot be mounted
#: over Docker's locked masked paths, and a bound one lets the shell read other
#: processes' command lines; `df`, `ps` and `uptime` degrade. No `/dev`: `--dev` builds
#: a fresh minimal one rather than exposing the container's.
SYSTEM_PATHS: tuple[str, ...] = ("/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc")


@cache
def bubblewrap_available() -> bool:
    """Whether bubblewrap can actually build a sandbox on this host."""
    if platform.system() != "Linux" or shutil.which("bwrap") is None:
        return False
    try:
        # `/` read-only, so this asks only whether a namespace can be made, not which
        # paths a real policy binds.
        done = subprocess.run(  # noqa: S603 -- fixed argv, no shell, no caller input
            [str(shutil.which("bwrap")), "--ro-bind", "/", "/", "--unshare-all", "/bin/true"],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def argv_for(
    session_dir: Path,
    layout: Layout,
    *,
    readable: Iterable[Path | str] = (),
    writable: Iterable[Path | str] = (),
) -> list[str]:
    """The sandbox one session's commands run in, generated from it."""
    argv = ["bwrap"]
    for path in present(SYSTEM_PATHS):
        argv += ["--ro-bind", path, path]
    for path in present(readable):
        argv += ["--ro-bind", str(path), str(path)]
    # Read-only beneath its folders: a file the shell leaves at the session's top is
    # handed back to nobody, and the command said it succeeded.
    for path in present([session_dir]):
        argv += ["--ro-bind", str(path), str(path)]
    shell_writes = [Path(session_dir) / name for name in layout.shell_writable]
    for path in present([*shell_writes, *writable]):
        argv += ["--bind", str(path), str(path)]
    # After the folders' own binds, and that order is the mechanism: bwrap applies binds
    # in sequence, so a read-only subpath bind lands on top of the writable one. `present`
    # drops an absent one, which is why `ensure_session_layout` makes them first. `inputs`
    # is the caller's, and its write bits are the shell's to lift; a saved result is what
    # a later turn reads back as a tool's answer.
    for path in present([Path(session_dir) / name for name in layout.shell_read_only]):
        argv += ["--ro-bind", str(path), str(path)]
    argv += [
        "--dev", "/dev",
        # User, mount, pid, ipc, uts, cgroup *and* net: the network closes with this
        # mode rather than being another setting for `doctor` to describe.
        "--unshare-all",
        # So a killed turn does not leave the sandbox running.
        "--die-with-parent",
        "--chdir", str(session_dir),
    ]
    return argv


class BubblewrapRunner:
    """Runs one session's commands inside that sandbox."""

    local = True

    def __init__(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        max_output_bytes: int = MAX_OUTPUT_BYTES,
    ) -> None:
        self.argv = list(argv)
        #: Explicit rather than inherited, so this process's credentials do not reach the
        #: agent's shell, which no filesystem fence would catch.
        self.env = dict(env or {})
        self.max_output_bytes = max_output_bytes

    def run(
        self,
        command: str,
        *,
        timeout: int | None = None,
        stop: threading.Event | None = None,
    ) -> CommandResult:
        """Run `command` through a shell, inside the sandbox."""
        return run_command(
            [*self.argv, "/bin/sh", "-c", command],
            env=self.env,
            timeout=timeout,
            max_output_bytes=self.max_output_bytes,
            unstartable="bubblewrap failed",
            stop=stop,
        )
