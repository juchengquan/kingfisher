"""A Linux fence for `execute`, and the policy it runs under.

`confinement.py` had nothing for Linux: `auto` and `off` did the same thing
there, and `external` -- "the runtime is the fence" -- was the only honest
setting. That was true while a container held one tenant and stopped being true
when it held several. Measured in the prototype: tenant B's shell read tenant
A's file with `cat ../<A>/derived/secret.txt`, exit 0, while B's *file tools*
were correctly refused. `virtual_mode` roots the file tools at a session; it
does nothing to `execute`.

Landlock is the mechanism, through `sandlock`, and it was chosen against two
measured alternatives. **bubblewrap** works only with
`--security-opt seccomp=unconfined`: disabling the syscall filter to gain path
isolation, in a box shared by tenants. **Per-session Unix users** via `setpriv`
works but denies by *ownership* -- every file, forever, including ones the agent
creates. Landlock denies by default, which is the direction a security mechanism
should fail in, and needs no privileges and no relaxed seccomp: measured at
0.2ms -> 0.8ms per command under Docker's default profile.

Everything here is generated. The first policy this author wrote by hand
**failed open**: it granted `/tmp` writable while the workspace was mounted
under `/tmp`, so the fence covered nothing -- and every read succeeded, so it
looked like it worked. `Sandbox` has forty fields. A hand-written policy that is
wrong is indistinguishable from one that is right until someone reads another
tenant's file, which is why a deployment configures a *mode* here and never a
policy.

What this does not cover is worth saying in the same breath. **The network is
open**: a fenced shell that cannot read another tenant's file can still open any
socket, and `http_fetch` is a registered tool, so reading and sending are one
turn apart for anything that takes an injection. **Registered tools run in
kingfisher's own process** and are not fenced by this at all -- a fence there
would confine kingfisher. This makes a shared container safer. It is not the
tenancy boundary; a pod per tenant is.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kingfisher.infrastructure.sandbox.linux import MAX_OUTPUT_BYTES, outcome, present
from kingfisher.layout import HARNESS, SESSION_DIRS, SESSION_PLUMBING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from kingfisher.domain.ports import CommandResult

#: What a shell needs to be a shell. Landlock denies by default and the fence is
#: applied before `exec`, so a policy without these cannot start `/bin/sh` at
#: all -- the command fails with something that looks like a broken image rather
#: than a denied path. Taken from `sandlock`'s own quick-start rather than
#: assembled here, with `/sbin` added because a Debian shell reaches for it.
#:
#: Filtered against the host by `present`, and that is not tidiness: the finding
#: that made it necessary is recorded there, and it cost this fence a release
#: where every command came back looking like a broken image.
SYSTEM_PATHS: tuple[str, ...] = (
    "/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc", "/proc", "/dev",
)


def _session_writable(session_dir: Path) -> list[Path]:
    """Everywhere inside one session the shell may write: its directories, one by one.

    **Never the session directory itself, and that is the whole of why this is a
    list rather than one path.** Landlock rules only ever grant. A write walks up
    from the file to the first rule that allows it, so a read-only grant on
    `.harness` is stepped straight over and a writable grant on the directory
    above answers in its place -- measured on a real kernel, where the fenced
    shell overwrote the agent definition its own session is pinned to. Granting
    the contents and not the container is what leaves that walk nothing to find.

    The cost is that the shell cannot write into its own working directory, which
    is the session; `tests/linux/test_fence_escapes.py` holds it, so changing it
    back is a decision rather than an accident.
    """
    return [
        Path(session_dir) / name
        for name in (*SESSION_DIRS, *SESSION_PLUMBING)
        if name != HARNESS
    ]


def policy_for(
    session_dir: Path,
    *,
    readable: Iterable[Path | str] = (),
    writable: Iterable[Path | str] = (),
) -> Any:
    """The `Sandbox` one session's commands run under, generated from it.

    Filesystem fields and nothing else, because that is all `confine` accepts.
    Measured: `cwd`, `workdir`, `clean_env` and `env` each make it raise
    `ConfinementError` -- rejected rather than silently ignored, which is the right
    behaviour and is how this was found. The working directory and the environment
    are the runner's business instead, which is where they can be given to
    `subprocess` anyway.
    """
    # Imported here, not at the top. `sandlock` ships Linux-only wheels, and
    # this module is imported on macOS every time `default_backend` runs -- a
    # top-level import would make the package unusable there to gain nothing.
    # `ty: ignore` for the same reason: it is not installed on the machine
    # this is developed on, and an optional Linux-only dependency that
    # resolved everywhere would not be optional.
    from sandlock import Sandbox  # noqa: PLC0415

    return Sandbox(
        # The session is readable whole -- the shell starts in it, and one that
        # cannot list its own working directory gets swapped out for no fence at
        # all. Writable is narrower than readable here, and `_session_writable`
        # says why it has to be.
        fs_readable=present([*SYSTEM_PATHS, *readable, session_dir]),
        fs_writable=present([*_session_writable(session_dir), *writable]),
    )


class LandlockRunner:
    """Runs one session's commands behind that policy.

    The fence goes on between `fork` and `exec`, which means this owns the process
    launch. `Sandbox.run` would have owned it instead and was tried first: measured
    in a container on Linux 6.12 with ABI 6 and `SYS_ADMIN` available, `sandlock`'s
    own quick-start example returns `sandlock_create failed` -- it needs more than
    Landlock, and Docker's default seccomp does not give it. `confine` in the same
    container works, both directions: the session's own files readable and writable,
    another tenant's `Permission denied`.

    **`preexec_fn` is documented as unsafe in a threaded program**, and this one is
    threaded wherever a deployment runs turns on threads. The hazard is a child that
    deadlocks because another thread held an allocator lock at `fork`. It is accepted
    here rather than hidden, because the alternative -- a launcher process that
    confines itself and then `exec`s -- costs an interpreter start per command and a
    serialised policy, and should be built if a deadlock is ever seen rather than in
    anticipation of one.
    """

    #: This one runs here, so kingfisher's own confinement still applies to the
    #: command before it arrives. On Linux that wrap is the identity -- the fence
    #: is applied to the process rather than to the string -- but saying so is
    #: what keeps the rule one rule rather than a platform's accident.
    local = True

    def __init__(
        self,
        policy: Any,
        *,
        cwd: Path | str,
        env: Mapping[str, str] | None = None,
        max_output_bytes: int = MAX_OUTPUT_BYTES,
    ) -> None:
        self.policy = policy
        self.cwd = str(cwd)
        #: Passed to `subprocess` rather than into the policy: `confine` rejects
        #: environment settings outright. Empty is not "inherit" -- an explicit
        #: mapping is what keeps this process's credentials out of the agent's
        #: shell, which no filesystem fence would catch.
        self.env = dict(env or {})
        self.max_output_bytes = max_output_bytes

    def run(self, command: str, *, timeout: int | None = None) -> CommandResult:
        """Run `command` through a shell, fenced."""
        from sandlock import confine  # noqa: PLC0415

        def fence() -> None:
            confine(self.policy)

        def launch() -> subprocess.CompletedProcess[str]:
            return subprocess.run(  # noqa: S602 -- `execute` is a shell by definition
                command,
                shell=True,
                cwd=self.cwd,
                env=self.env,
                preexec_fn=fence,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )

        return outcome(
            launch,
            timeout=timeout,
            max_output_bytes=self.max_output_bytes,
            unstartable="the Landlock fence could not be applied",
        )
