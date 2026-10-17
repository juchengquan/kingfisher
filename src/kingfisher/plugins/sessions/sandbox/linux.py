"""What the two Linux fences share: which paths exist, and what a command comes back as.

Not each fence's own path list: they differ on purpose, and one list serving both is how
a mechanism gains a path the other meant to deny.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from kingfisher.core.interfaces import CommandResult

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

#: `LocalShellBackend`'s own limit: a fence must not change how much output a turn sees.
MAX_OUTPUT_BYTES = 100_000


def present(paths: Iterable[Path | str]) -> list[str]:
    """The ones that exist, as strings.

    Both fences refuse to build over an absent path (`/lib64` on arm64 Debian), and a
    fence that never applied reads as a broken image.
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

    `launch` is a callable because that is the half the mechanisms differ on: one
    confines between `fork` and `exec`, the other prepends a sandbox to the argv.
    """
    try:
        done = launch()
    except subprocess.TimeoutExpired:
        return CommandResult(
            # What an unfenced timeout says: a fence changes what a turn sees only by
            # denying a path.
            output=f"Error: Command timed out after {timeout} seconds.",
            exit_code=124,
        )
    except (OSError, subprocess.SubprocessError) as failed:
        # Said, because empty output reads as a broken image. `SubprocessError` too: a
        # `preexec_fn` that raises comes back wrapped in one.
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
