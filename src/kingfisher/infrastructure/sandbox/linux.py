"""What the two Linux fences share: which paths exist, and what a command comes back as.

Neither half belongs to either mechanism. `MAX_OUTPUT_BYTES` lived in `fence.py` and
`bubblewrap.py` imported it from there, which reads as bubblewrap depending on Landlock
for a number that is about `LocalShellBackend`; the shaping and the two failures were
written out in both runners and were identical to the byte.

What is *not* here is each fence's own path list. They differ on purpose -- bubblewrap
binds no `/proc` and builds a fresh `/dev` -- and one list serving both is how a
mechanism silently gains a path the other one meant to deny.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from kingfisher.domain.ports import CommandResult

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

#: Matches `LocalShellBackend`'s own limit, so a fenced command and an unfenced one
#: truncate at the same place. A fence that changed how much output a turn could see
#: would be a fence that changed the agent's behaviour.
MAX_OUTPUT_BYTES = 100_000


def present(paths: Iterable[Path | str]) -> list[str]:
    """The ones that exist, as strings.

    Both mechanisms need this and each found out the hard way. `/lib64` does not exist
    on arm64 Debian: naming it made `sandlock_create` fail outright, so the fence did
    not build and every command came back with exit -1 and no output -- which reads as
    a broken image rather than as a fence that was never applied. `bwrap` refuses a
    bind whose source is absent, which is the same finding arriving as a different
    error. A list of paths compiled into a file is a claim about every image kingfisher
    will ever run in, and it was wrong on the second one it met.
    """
    return [str(path) for path in paths if Path(path).exists()]


def outcome(
    launch: Callable[[], subprocess.CompletedProcess[str]],
    *,
    timeout: int | None,
    max_output_bytes: int,
    unstartable: str,
) -> CommandResult:
    """Run a fenced command through `launch`, and say what came back.

    Three exits, and only the first is the command's own. `launch` is a callable rather
    than an argv because that is the half the two mechanisms genuinely differ on -- one
    confines between `fork` and `exec` and the other prepends a sandbox to the argv --
    and it is the only half worth writing twice.
    """
    try:
        done = launch()
    except subprocess.TimeoutExpired:
        return CommandResult(
            # The shell's own exit code, so a caller cannot tell a fenced timeout from
            # an unfenced one -- which is the point: a fence is not supposed to change
            # what a turn sees except by denying a path.
            output=f"Error: Command timed out after {timeout} seconds.",
            exit_code=124,
        )
    except (OSError, subprocess.SubprocessError) as failed:
        # A fence failing to *build* used to arrive as an exit code with two empty byte
        # strings, which is what a command with no output looks like -- so a fence that
        # never applied read as a broken image. Both types are caught because a
        # `preexec_fn` that raises comes back as a `SubprocessError` wrapping the
        # child's exception rather than as the exception itself.
        return CommandResult(
            output=f"[fence] the command did not run: {unstartable} ({failed})",
            exit_code=1,
        )
    return shaped(done.stdout + done.stderr, done.returncode, max_output_bytes)


def shaped(output: str, exit_code: int, max_output_bytes: int) -> CommandResult:
    """Truncated where an unfenced command would truncate."""
    truncated = len(output.encode("utf-8")) > max_output_bytes
    if truncated:
        output = output[:max_output_bytes]
        output += f"\n\n... Output truncated at {max_output_bytes} bytes."
    return CommandResult(output=output, exit_code=exit_code, truncated=truncated)
