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

from kingfisher.domain.ports import CommandResult

if TYPE_CHECKING:
    from collections.abc import Mapping

#: What a shell needs to be a shell. Landlock denies by default and the fence is
#: applied before `exec`, so a policy without these cannot start `/bin/sh` at
#: all -- the command fails with something that looks like a broken image rather
#: than a denied path. Taken from `sandlock`'s own quick-start rather than
#: assembled here, with `/sbin` added because a Debian shell reaches for it.
#:
#: Filtered against the host by `_present`, and that is not tidiness. Measured:
#: `/lib64` does not exist on arm64 Debian, and naming it made `sandlock_create`
#: fail outright -- so the fence did not build, and every command came back with
#: exit -1 and no output, which reads as a broken image rather than as a fence
#: that was never applied. A list of paths compiled into this file is a claim
#: about every image kingfisher will ever run in, and it was wrong on the second
#: one it met.
SYSTEM_PATHS: tuple[str, ...] = (
    "/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc", "/proc", "/dev",
)

#: Matches `LocalShellBackend`'s own limit, so a fenced command and an unfenced
#: one truncate at the same place. A fence that changed how much output a turn
#: could see would be a fence that changed the agent's behaviour.
MAX_OUTPUT_BYTES = 100_000


def _present(paths: Iterable[Path | str]) -> list[str]:
    """The ones that exist, as strings.

    Dropping rather than naming, because `sandlock` rejects the whole policy
    when a path in it does not exist -- and a rejected policy is not a loose
    fence, it is no fence and no command either. Safe to drop: a path that does
    not exist grants nothing, so the fence is exactly as tight without it.

    The risk this accepts is a typo becoming silence. It is bounded by the rule
    above it -- a deployment never writes one of these, so the only paths that
    reach here are ones this repository generated from a session that exists.
    """
    return [str(path) for path in paths if Path(path).exists()]


def policy_for(
    session_dir: Path,
    *,
    readable: Iterable[Path | str] = (),
    writable: Iterable[Path | str] = (),
) -> Any:
    """The `Sandbox` one session's commands run under, generated from it.

    Writable is the session and nothing else. Readable is what a shell needs
    plus the shared catalogue, because skills are workspace-level and their
    scripts are run by the shell against `$KINGFISHER_SKILLS` -- a fence that
    hid them would break the feature it was protecting.

    Filesystem fields and nothing else, because that is all `confine` accepts.
    Measured: `cwd`, `workdir`, `clean_env` and `env` each make it raise
    `ConfinementError` -- rejected rather than silently ignored, which is the
    right behaviour and is how this was found. The working directory and the
    environment are the runner's business instead, which is where they can be
    given to `subprocess` anyway.
    """
    # Imported here, not at the top. `sandlock` ships Linux-only wheels, and
    # this module is imported on macOS every time `build_backend` runs -- a
    # top-level import would make the package unusable there to gain nothing.
    # `ty: ignore` for the same reason: it is not installed on the machine
    # this is developed on, and an optional Linux-only dependency that
    # resolved everywhere would not be optional.
    from sandlock import Sandbox  # noqa: PLC0415

    return Sandbox(
        fs_readable=_present([*SYSTEM_PATHS, *readable]),
        fs_writable=_present([session_dir, *writable]),
    )


class LandlockRunner:
    """Runs one session's commands behind that policy.

    A `CommandRunner`, so it replaces *running the command* and nothing else.
    The shell backend it sits behind is also the filesystem for every path no
    route matches, and those stay kingfisher's.

    The fence goes on between `fork` and `exec`, which means this owns the
    process launch. `Sandbox.run` would have owned it instead and was tried
    first: measured in a container on Linux 6.12 with ABI 6 and `SYS_ADMIN`
    available, `sandlock`'s own quick-start example returns
    `sandlock_create failed` -- it needs more than Landlock, and Docker's
    default seccomp does not give it. `confine` in the same container works,
    both directions: the session's own files readable and writable, another
    tenant's `Permission denied`.

    **`preexec_fn` is documented as unsafe in a threaded program**, and this one
    is threaded: `astream` runs turns on worker threads. The hazard is a child
    that deadlocks because another thread held an allocator lock at `fork`. It
    is accepted here rather than hidden, because the alternative -- a launcher
    process that confines itself and then `exec`s -- costs an interpreter start
    per command and a serialised policy, and should be built if a deadlock is
    ever seen rather than in anticipation of one.
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
        """Run `command` through a shell, fenced.

        `shell=True` because that is what `execute` means and what the unfenced
        path does. The fence is applied to the shell itself, so everything it
        starts inherits it -- Landlock rulesets only ever narrow, and a child
        cannot widen its own.
        """
        from sandlock import confine  # noqa: PLC0415

        def fence() -> None:
            confine(self.policy)

        try:
            done = subprocess.run(  # noqa: S602 -- `execute` is a shell by definition
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
        except subprocess.TimeoutExpired:
            return CommandResult(
                output=f"Error: Command timed out after {timeout} seconds.",
                # The shell's own, so a caller cannot tell a fenced timeout from
                # an unfenced one -- which is the point: the fence is not
                # supposed to change what a turn sees except by denying a path.
                exit_code=124,
            )
        except (OSError, subprocess.SubprocessError) as failed:
            # The fence failing to *build* used to arrive as an exit code with
            # two empty byte strings, which is what a command with no output
            # looks like -- so a fence that never applied read as a broken
            # image. Both types are caught because a `preexec_fn` that raises
            # comes back as a `SubprocessError` wrapping the child's exception
            # rather than as the exception itself.
            #
            # Failing closed either way: the child is already dead, so there is
            # no unfenced run to leak. What this decides is only whether anyone
            # is told why -- and mostly they cannot be. `subprocess` discards
            # the child's exception and raises "Exception occurred in
            # preexec_fn." with no detail, so the message says which side failed
            # rather than pretending to a reason it was not given.
            return CommandResult(
                output=(
                    f"[fence] the command did not run: the Landlock fence could not be "
                    f"applied ({failed})"
                ),
                exit_code=1,
            )
        return self._shaped(done.stdout + done.stderr, done.returncode)

    def _shaped(self, output: str, exit_code: int) -> CommandResult:
        """Truncated where an unfenced command would truncate.

        A fence that changed how much output a turn could see would be a fence
        that changed the agent's behaviour, which is how a fence gets turned off.
        """
        truncated = len(output.encode("utf-8")) > self.max_output_bytes
        if truncated:
            output = output[: self.max_output_bytes]
            output += f"\n\n... Output truncated at {self.max_output_bytes} bytes."
        return CommandResult(output=output, exit_code=exit_code, truncated=truncated)
