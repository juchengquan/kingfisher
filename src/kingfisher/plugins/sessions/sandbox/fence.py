"""A Linux fence for `execute` through Landlock (`sandlock`), and the policy it runs under.

Landlock denies by default, the direction a security mechanism should fail in, and needs
no privileges and no relaxed seccomp. Not the tenancy boundary, which is a pod per tenant:
the network stays open, and registered tools run in kingfisher's own process, unfenced.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kingfisher.core.layout import Layout

from .linux import present
from .process import MAX_OUTPUT_BYTES, run_command

if TYPE_CHECKING:
    import threading
    from collections.abc import Mapping

    from kingfisher.core.interfaces import CommandResult

#: What a shell needs to be a shell: without these `/bin/sh` cannot start, which looks
#: like a broken image rather than a denied path. `sandlock`'s quick-start list, plus
#: `/sbin`, which a Debian shell reaches for.
SYSTEM_PATHS: tuple[str, ...] = (
    "/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc", "/proc", "/dev",
)


def _session_writable(session_dir: Path, layout: Layout) -> list[Path]:
    """Everywhere inside one session the shell may write: its directories, one by one.

    Never the session itself: a Landlock rule only grants, and a write walks up to the
    first rule allowing it, so a writable session would step over a read-only `.harness`.
    The cost, a shell that cannot write into its working directory, is held by
    `tests/linux/test_fence_escapes.py`. `inputs` is left out because it is the caller's,
    and a grant here is the only thing that would let the shell change it.
    """
    return [Path(session_dir) / name for name in layout.session_dirs if name != layout.inputs]


def policy_for(
    session_dir: Path,
    layout: Layout,
    *,
    readable: Iterable[Path | str] = (),
    writable: Iterable[Path | str] = (),
) -> Any:
    """The `Sandbox` one session's commands run under, generated from it.

    Generated, never hand-written: a wrong policy looks like a right one until another
    tenant's file is read, so a deployment configures a mode, not a policy. Filesystem
    fields only: `confine` raises `ConfinementError` on `cwd`, `workdir`, `clean_env` and
    `env`, which are the runner's to give `subprocess`.
    """
    # Here, not at the top: `sandlock` ships Linux-only wheels, and this module must
    # import on macOS.
    from sandlock import Sandbox  # noqa: PLC0415

    return Sandbox(
        # The session readable whole: a shell that cannot list its working directory
        # gets swapped out for no fence at all.
        fs_readable=present([*SYSTEM_PATHS, *readable, session_dir]),
        fs_writable=present([
            *_session_writable(session_dir, layout),
            *writable,
        ]),
    )


class LandlockRunner:
    """Runs one session's commands behind that policy.

    Owns the launch and confines between `fork` and `exec`, because `Sandbox.run` needs
    more than Landlock and fails under Docker's default seccomp. `preexec_fn` is unsafe
    in a threaded program: a child can deadlock on a lock held at `fork`. Accepted, since
    a self-confining launcher costs an interpreter start per command; build one if a
    deadlock is ever seen.
    """

    #: Runs here, so the confinement's wrap still applies: the identity on Linux, but
    #: one rule rather than a platform's accident.
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
        #: Given to `subprocess`, since `confine` rejects environment settings. Empty is
        #: not "inherit": this process's credentials stay out of the agent's shell.
        self.env = dict(env or {})
        self.max_output_bytes = max_output_bytes

    def run(
        self,
        command: str,
        *,
        timeout: int | None = None,
        stop: threading.Event | None = None,
    ) -> CommandResult:
        """Run `command` through a shell, fenced."""
        from sandlock import confine  # noqa: PLC0415

        def fence() -> None:
            confine(self.policy)

        return run_command(
            command,
            env=self.env,
            timeout=timeout,
            cwd=self.cwd,
            max_output_bytes=self.max_output_bytes,
            preexec_fn=fence,
            unstartable="the Landlock fence could not be applied",
            stop=stop,
        )
