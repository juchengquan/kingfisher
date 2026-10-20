"""A measurement, not a guard: which `setsid` escapes outlive a command under Landlock,
and whether a launcher that stays alive as a subreaper would catch them. Reported as a
warning in the job's log; nothing here fails on what it finds. Not for merging."""

from __future__ import annotations

import shlex
import statistics
import sys
import time
import warnings
from pathlib import Path

import pytest

from kingfisher.core.layout import DEFAULT_LAYOUT
from kingfisher.plugins.sessions.sandbox.confinement import landlock_ready, toolchain_roots

needs_landlock = pytest.mark.skipif(not landlock_ready(), reason="no Landlock here")

#: A launcher that adopts what the command orphans, and when the command ends kills and
#: collects everything that came back to it, until nothing does.
REAPER = r"""
import ctypes, os, signal, sys
PR_SET_CHILD_SUBREAPER = 36
ctypes.CDLL(None, use_errno=True).prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0)
child = os.fork()
if child == 0:
    os.execv("/bin/sh", ["/bin/sh", "-c", sys.argv[1]])
_, status = os.waitpid(child, 0)

def adopted():
    found = set()
    for task in os.listdir("/proc/self/task"):
        with open(f"/proc/self/task/{task}/children") as listed:
            found |= {int(pid) for pid in listed.read().split()}
    return found

while left := adopted():
    for pid in left:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    while True:
        try:
            if os.waitpid(-1, os.WNOHANG)[0] == 0:
                break
        except ChildProcessError:
            break
sys.exit(os.waitstatus_to_exitcode(status))
"""

#: Each starts something that writes the mark two seconds later; the first stays in the
#: command's group, so the group kill must stop it, which is what makes the rest mean
#: anything.
ESCAPES = {
    "control: background, same group": "sh -c 'sleep 2; touch \"$M\"' >/dev/null 2>&1 &",
    "setsid(1)": "setsid sh -c 'sleep 2; touch \"$M\"' >/dev/null 2>&1 &",
    "python os.setsid": (
        "python3 -c 'import os, time, pathlib; os.setsid(); time.sleep(2); "
        "pathlib.Path(os.environ[\"M\"]).touch()' >/dev/null 2>&1 &"
    ),
    "fork, setsid, fork": (
        "python3 -c 'import os, time, pathlib\n"
        "if os.fork() == 0:\n"
        "    os.setsid()\n"
        "    if os.fork() == 0:\n"
        "        time.sleep(2); pathlib.Path(os.environ[\"M\"]).touch()\n"
        "    os._exit(0)\n"
        "os.wait()' >/dev/null 2>&1"
    ),
    "perl setsid": (
        "perl -MPOSIX -e 'POSIX::setsid(); sleep 2; open(F, \">\", $ENV{M})' "
        ">/dev/null 2>&1 &"
    ),
}


def _runner(session: Path):
    from kingfisher.plugins.sessions.sandbox.fence import LandlockRunner, policy_for

    return LandlockRunner(
        policy_for(session, DEFAULT_LAYOUT, readable=toolchain_roots()),
        cwd=session,
        env={
            "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin:/usr/local/bin",
            "TMPDIR": str(session / DEFAULT_LAYOUT.scratchpad),
        },
    )


def _reaped(command: str) -> str:
    return f"{shlex.quote(sys.executable)} -I -c {shlex.quote(REAPER)} {shlex.quote(command)}"


def _survives(runner, session: Path, command: str, *, reaped: bool, timeout: int = 20) -> bool:
    """Whether what `command` started wrote its mark after the run had returned."""
    mark = session / DEFAULT_LAYOUT.scratchpad / f"mark-{time.monotonic_ns()}"
    inner = f"export M={shlex.quote(str(mark))}\n{command}"
    runner.run(_reaped(inner) if reaped else inner, timeout=timeout)
    time.sleep(3)
    return mark.exists()


def _said(survived: bool) -> str:
    return "SURVIVED" if survived else "killed"


@needs_landlock
def test_measure_setsid_escapes_under_landlock(tmp_path):
    from kingfisher.plugins.sessions.folders import ensure_session_layout

    session = ensure_session_layout(tmp_path / "sessions" / "s", DEFAULT_LAYOUT)
    runner = _runner(session)
    lines = []
    for name, command in ESCAPES.items():
        plain = _survives(runner, session, command, reaped=False)
        at_exit = _survives(runner, session, command, reaped=True)
        # The launcher is the group's leader, so a timeout's SIGKILL reaches it too.
        at_timeout = _survives(runner, session, f"{command}\nsleep 30", reaped=True, timeout=1)
        lines.append(
            f"{name}: plain {_said(plain)}; reaper, command ends {_said(at_exit)}; "
            f"reaper, command times out {_said(at_timeout)}"
        )
    lines.append(_cost(runner))
    warnings.warn("setsid measurement:\n" + "\n".join(lines), stacklevel=1)


def _cost(runner) -> str:
    def timed(command: str) -> float:
        samples = []
        for _ in range(15):
            started = time.perf_counter()
            runner.run(command, timeout=20)
            samples.append((time.perf_counter() - started) * 1000)
        return statistics.median(samples)

    plain, reaped = timed("true"), timed(_reaped("true"))
    return f"cost: plain {plain:.1f}ms, through the reaper {reaped:.1f}ms (median of 15)"
