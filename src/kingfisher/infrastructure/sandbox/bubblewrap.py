"""The other Linux fence, for kernels Landlock cannot reach and a network to close.

`fence.py` is the first choice and stays it: Landlock needs no privileges, no relaxed
seccomp and no namespaces, and it *blocks* `mount`, so a fenced process cannot spend
`SYS_ADMIN` even in a container that has it. This exists because it has two
properties Landlock does not, and both were measured rather than read.

**It works where Landlock does not.** Landlock needs ABI 6, which is Linux 6.12+. EKS
nodes are commonly on 6.1, where `kingfisher doctor` reports "below what a full
ruleset needs" and the shell runs unfenced. bubblewrap needs only user namespaces.

**It closes the shell's network.** `--unshare-all` includes the network namespace: a
socket that connects unfenced fails fenced. Nothing kingfisher ships needs the shell
to reach the network -- `http_fetch` is a registered tool and runs in this process,
untouched by any of this.

Which is also the honest limit. The exfiltration path the fence design names --
*"reading and sending are one turn apart for anything that gets an injection"* --
runs through that tool and is **not** closed by this. What is closed is a script the
agent writes and runs reaching out on its own.

**The price is one seccomp rule, and it is worth naming precisely.** Measured under
`strace`, the denial is a single call::

**No `/proc`, deliberately.** A fresh `proc` cannot be mounted here -- Docker's
masked paths inside `/proc` are locked mounts, and the kernel refuses a new one that
would hide them -- so the choice is between binding the container's real `/proc` and
having none. Measured with a token generated at runtime: through a bound `/proc` a
sandboxed shell reads **other processes' command lines**, and with none it reads
nothing. `/proc/1/environ` was denied either way by the user namespace mapping, but a
command line is enough. Python and `pip` work without it; `df`, `ps` and `uptime`
degrade, which is the cost.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

from kingfisher.infrastructure.sandbox.linux import MAX_OUTPUT_BYTES, outcome, present
from kingfisher.layout import HARNESS

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from kingfisher.domain.ports import CommandResult

#: Bound read-only so a shell can be a shell. `/proc` is absent for the reason
#: the module explains, and `/dev` is not here because `--dev` builds a fresh
#: minimal one rather than exposing the container's.
SYSTEM_PATHS: tuple[str, ...] = ("/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc")


@cache
def bubblewrap_available() -> bool:
    """Whether bubblewrap can actually build a sandbox on this host."""
    if platform.system() != "Linux" or shutil.which("bwrap") is None:
        return False
    try:
        # `/` read-only, so this asks one question -- can a namespace be made -- and not
        # a second one about which paths a real policy binds. The first version bound
        # `/usr` and ran `/bin/true`, which is not in `/usr`: it reported "unavailable"
        # on a host where bubblewrap worked perfectly. Wrong in the safe direction, and
        # wrong.
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
    for path in present([session_dir, *writable]):
        argv += ["--bind", str(path), str(path)]
    # After the session's own bind, and that order is the whole mechanism: bwrap
    # applies binds in sequence, so a read-only bind of a subpath lands on top of
    # the writable one underneath it. Before it, the session bind would cover
    # this again. `present` drops it if it is absent, which is why
    # `ensure_session_layout` makes it before a fence is built.
    for path in present([Path(session_dir) / HARNESS]):
        argv += ["--ro-bind", str(path), str(path)]
    argv += [
        # A fresh minimal /dev rather than the container's.
        "--dev", "/dev",
        # User, mount, pid, ipc, uts, cgroup *and* net. The network is closed in
        # this mode and is not a separate setting: choosing bubblewrap is
        # already an assertion about an unusual container, and one more property
        # in that assertion is cheaper than another axis for `doctor` to describe.
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
        #: Passed to `subprocess`, which hands it to `bwrap`, which passes it on.
        #: Explicit rather than inherited, so this process's credentials do not
        #: reach the agent's shell -- something no filesystem fence would catch.
        self.env = dict(env or {})
        self.max_output_bytes = max_output_bytes

    def run(self, command: str, *, timeout: int | None = None) -> CommandResult:
        """Run `command` through a shell, inside the sandbox."""

        def launch() -> subprocess.CompletedProcess[str]:
            return subprocess.run(  # noqa: S603 -- generated argv, and the shell is the point
                [*self.argv, "/bin/sh", "-c", command],
                env=self.env,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )

        return outcome(
            launch,
            timeout=timeout,
            max_output_bytes=self.max_output_bytes,
            unstartable="bubblewrap failed",
        )
