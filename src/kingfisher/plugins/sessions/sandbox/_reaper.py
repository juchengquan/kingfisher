"""Run one shell command and leave nothing it started: what it orphans comes back to this
process, which kills and collects it when the command ends or when it is asked to stop.

Run by source, as `python -I -c`, never imported: the fence reaches the interpreter, not
necessarily kingfisher's own files. Stdlib only, for the same reason.
"""

import ctypes
import os
import signal
import sys
from contextlib import suppress
from pathlib import Path

#: `prctl` option: orphaned descendants are handed to this process rather than to init.
PR_SET_CHILD_SUBREAPER = 36

TASKS = Path("/proc/self/task")


class _Run:
    """The shell this process started, and whether it was asked to stop."""

    child = 0
    asked = False


def _time_up(_signum: int, _frame: object) -> None:
    """SIGTERM: the command's time is up. The shell goes now; the rest in `_clear`."""
    _Run.asked = True
    if _Run.child:
        with suppress(ProcessLookupError):
            os.kill(_Run.child, signal.SIGKILL)


def _subreaper() -> bool:
    """Whether this process now adopts what its descendants orphan."""
    if not TASKS.is_dir():
        return False
    prctl = getattr(ctypes.CDLL(None, use_errno=True), "prctl", None)
    return prctl is not None and prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) == 0


def _adopted() -> set[int]:
    found: set[int] = set()
    for task in TASKS.iterdir():
        found.update(int(pid) for pid in (task / "children").read_text("ascii").split())
    return found


def _clear() -> None:
    """Kill and collect every child until none is left: each one collected hands its own
    orphans to this process, and they are killed on the next pass."""
    while left := _adopted():
        for pid in left:
            with suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        for pid in left:
            with suppress(ChildProcessError):
                os.waitpid(pid, 0)


def _shell(command: str) -> None:
    os.execv("/bin/sh", ["/bin/sh", "-c", command])  # noqa: S606 -- the shell is the point


def main(command: str) -> None:
    if not _subreaper():
        # Nowhere to adopt into: the command runs as it would have without this.
        _shell(command)
    signal.signal(signal.SIGTERM, _time_up)
    child = os.fork()
    if child == 0:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        _shell(command)
    _Run.child = child
    if _Run.asked:
        # Asked between the fork and the line above, before there was a shell to stop.
        _time_up(signal.SIGTERM, None)
    _, status = os.waitpid(child, 0)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    _clear()
    if _Run.asked:
        sys.exit(124)
    code = os.waitstatus_to_exitcode(status)
    if code < 0:
        # Ended by a signal: ended by it again, so the caller reads what the shell did.
        signal.signal(-code, signal.SIG_DFL)
        os.kill(os.getpid(), -code)
    sys.exit(code)


if __name__ == "__main__":
    main(sys.argv[1])
