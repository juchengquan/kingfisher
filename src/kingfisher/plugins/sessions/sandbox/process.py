"""How a shell command runs on this host, fenced or not: in a process group of its own,
read only up to a cap, and gone when it ends, whatever it started."""

from __future__ import annotations

import os
import select
import selectors
import signal
import subprocess
import sys
import time
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from kingfisher.core.interfaces import CommandResult

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable, Mapping, Sequence

#: `LocalShellBackend`'s own limit: a fence must not change how much output a turn sees.
MAX_OUTPUT_BYTES = 100_000

#: The cut is in characters, as upstream's is, and a character is at most four bytes:
#: keeping this many bytes of each stream per character of the cap cuts where it would.
_WIDEST_CHARACTER = 4

_CHUNK = 65_536

#: How often a host that cannot watch for an exit asks instead.
_POLL_S = 0.05


def run_command(  # noqa: PLR0913 -- one argument per thing a launch differs by
    command: str | Sequence[str],
    *,
    env: Mapping[str, str],
    timeout: int | None,
    cwd: str | None = None,
    max_output_bytes: int = MAX_OUTPUT_BYTES,
    preexec_fn: Callable[[], None] | None = None,
    unstartable: str | None = None,
    stop: threading.Event | None = None,
) -> CommandResult:
    """Run `command` -- through `/bin/sh` when it is a string -- and say what came back.

    When the shell exits, everything it started is killed with it, and a timeout kills
    the whole group: a process left behind writes into the session after the turn that
    started it has let go of it. `unstartable` names the fence a launch failure means;
    `stop`, once set, ends the command as running out of time would.
    """
    try:
        process = subprocess.Popen(  # noqa: S603 -- a shell command is the whole job
            command,
            shell=isinstance(command, str),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(env),
            cwd=cwd,
            # Unsafe beside threads, and the only place Landlock can confine before exec.
            preexec_fn=preexec_fn,  # noqa: PLW1509
            start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError) as failed:
        # `SubprocessError` too: a `preexec_fn` that raises comes back wrapped in one.
        if unstartable is None:
            output = f"Error executing command ({type(failed).__name__}): {failed}"
        else:
            # Said, because empty output reads as a broken image.
            output = f"[fence] the command did not run: {unstartable} ({failed})"
        return CommandResult(output=output, exit_code=1)

    keep = (max_output_bytes + 1) * _WIDEST_CHARACTER
    try:
        stdout, stderr, timed_out = _collect(process, timeout, keep, stop)
    finally:
        _kill_group(process.pid)
        for pipe in (process.stdout, process.stderr):
            if pipe is not None:
                pipe.close()
        process.wait()

    if timed_out:
        return CommandResult(
            # Upstream's wording for a timeout the caller set, which `execute` always is.
            output=(
                f"Error: Command timed out after {timeout} seconds (custom timeout). "
                "The command may be stuck or require more time."
            ),
            exit_code=124,
        )
    return shaped(
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
        process.returncode,
        max_output_bytes,
    )


def shaped(stdout: str, stderr: str, exit_code: int, max_output_bytes: int) -> CommandResult:
    """What deepagents' `LocalShellBackend.execute` makes of a finished command, to the
    character: macOS turns saw exactly this before they ran through here."""
    parts = [stdout] if stdout else []
    if stderr:
        parts.extend(f"[stderr] {line}" for line in stderr.strip().split("\n"))
    output = "\n".join(parts) if parts else "<no output>"

    truncated = len(output) > max_output_bytes
    if truncated:
        output = output[:max_output_bytes]
        output += f"\n\n... Output truncated at {max_output_bytes} bytes."
    if exit_code != 0:
        output = f"{output.rstrip()}\n\nExit code: {exit_code}"
    return CommandResult(output=output, exit_code=exit_code, truncated=truncated)


def _collect(
    process: subprocess.Popen[bytes],
    timeout: int | None,
    keep: int,
    stop: threading.Event | None,
) -> tuple[bytes, bytes, bool]:
    """Both streams, `keep` bytes of each at most, until the shell has exited and the
    pipes have closed, or until `timeout`; and whether it ran out of time.

    Read as it arrives rather than collected whole: `yes` for three seconds is gigabytes,
    in this process, beside every other session's turn.
    """
    deadline = None if timeout is None else time.monotonic() + timeout
    kept: dict[Any, bytearray] = {process.stdout: bytearray(), process.stderr: bytearray()}
    exit_watch = _ExitWatch(process.pid)
    exited = exit_watch.happened
    if exited:
        _kill_group(process.pid)
    selector = selectors.DefaultSelector()
    try:
        for pipe in kept:
            selector.register(pipe, selectors.EVENT_READ)
        if exit_watch.source is not None:
            selector.register(exit_watch.source, selectors.EVENT_READ)
        while not exited or len(selector.get_map()) > 0:
            left = None if deadline is None else deadline - time.monotonic()
            if (left is not None and left <= 0) or (stop is not None and stop.is_set()):
                return bytes(kept[process.stdout]), bytes(kept[process.stderr]), True
            # Nothing wakes the wait when `stop` is set, so it is asked as often as polling.
            polled = exit_watch.source is None or stop is not None
            wait = min(left or _POLL_S, _POLL_S) if polled else left
            for key, _ in selector.select(wait):
                if key.fileobj is exit_watch.source:
                    selector.unregister(key.fileobj)
                    exited = True
                    # Now, not at the end: what holds a pipe open would hold the read too.
                    _kill_group(process.pid)
                    continue
                chunk = os.read(key.fd, _CHUNK)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                held = kept[key.fileobj]
                held += chunk[: max(keep - len(held), 0)]
            if exit_watch.source is None and not exited and process.poll() is not None:
                # Collected before the kill on such a host, so its id could in principle
                # be reused first; a kernel without pidfd or kqueue offers nothing better.
                exited = True
                _kill_group(process.pid)
    finally:
        selector.close()
        exit_watch.close()
    return bytes(kept[process.stdout]), bytes(kept[process.stderr]), False


class _ExitWatch:
    """Readable once a process has exited, without collecting it.

    Uncollected, its id stays its group's, so the kill that follows cannot reach a
    stranger that was handed the id. `os.waitid` would say the same, and macOS lacks it.
    """

    def __init__(self, pid: int) -> None:
        self.source: Any = None
        self.happened = False
        try:
            if sys.platform == "darwin":
                self.source = select.kqueue()
                self.source.control(
                    [
                        select.kevent(
                            pid,
                            filter=select.KQ_FILTER_PROC,
                            flags=select.KQ_EV_ADD,
                            fflags=select.KQ_NOTE_EXIT,
                        )
                    ],
                    0,
                )
            elif sys.platform == "linux":
                self.source = os.pidfd_open(pid)
        except ProcessLookupError:
            # macOS refuses to watch a process that has already exited.
            self.close()
            self.happened = True
        except OSError:
            # A kernel before pidfd: asked by polling instead.
            self.close()

    def close(self) -> None:
        if isinstance(self.source, int):
            os.close(self.source)
        elif self.source is not None:
            self.source.close()
        self.source = None


def _kill_group(pgid: int) -> None:
    """Kill every process still in the group the command started.

    A process that called `setsid()` has left it and is not reached; recorded in
    `docs/decisions.md` under *Confining the shell*.
    """
    # Nothing left, or, on macOS, only the uncollected leader.
    with suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, signal.SIGKILL)
