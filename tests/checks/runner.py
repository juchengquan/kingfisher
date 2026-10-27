"""What a `CommandRunner` promises, as checks.

Not shipped: kingfisher holds its own runners to these, and a deployment writing its own
copies them. Plain functions raising `AssertionError` and no pytest.
"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from kingfisher.plugins import CommandRunner


def _equal(got: object, want: object, *, doing: str) -> None:
    if got != want:
        msg = f"{doing}: expected {want!r}, got {got!r}"
        raise AssertionError(msg)


def _must_be(value: object, kind: type, *, doing: str, why: str) -> None:
    """Require `value` to be a `kind`, saying what the shape is for."""
    if isinstance(value, kind):
        return
    msg = f"{doing}: {why}. Got {type(value).__name__}"
    raise AssertionError(msg)


# -- what a store holds -----------------------------------------------------


#: What the checks run. Kept together so a deployment whose runner needs
#: something else can see exactly what it is being asked for.
SUCCEEDS = "echo kingfisher-contract"
FAILS = "exit 3"
SLEEPS = "sleep 30"


def a_runner_says_where_it_runs(make: Callable[[], CommandRunner]) -> None:
    """`local` decides whether the fence is applied, so it is read before a command is."""
    runner = make()
    declared = runner.local
    _must_be(
        declared,
        bool,
        doing="reading `local` off the runner",
        why="it says whether the command runs on this machine, and a non-boolean is "
        "truthy in ways that quietly keep or lose the fence",
    )


def a_command_that_works_reports_that_it_worked(make: Callable[[], CommandRunner]) -> None:
    """Exit code zero, and the output the command wrote."""
    result = make().run(SUCCEEDS)

    _equal(result.exit_code, 0, doing=f"run({SUCCEEDS!r}).exit_code")
    if "kingfisher-contract" not in result.output:
        msg = (
            f"run({SUCCEEDS!r}).output does not contain what the command printed: "
            f"{result.output!r}. The model reads this as the tool result"
        )
        raise AssertionError(msg)


def a_command_that_fails_is_a_result_and_not_an_exception(
    make: Callable[[], CommandRunner],
) -> None:
    """A non-zero exit is ordinary. The agent runs commands that fail and reads
    the code to decide what to do next, so raising here turns a normal tool
    result into a turn that ended."""
    result = make().run(FAILS)

    _equal(result.exit_code, 3, doing=f"run({FAILS!r}).exit_code")


def a_result_is_shaped_the_way_a_caller_reads_it(make: Callable[[], CommandRunner]) -> None:
    """`exit_code` is not optional, and that is a decision the port records:
    *"the harness's equivalent allows `None`, and a caller deciding whether a
    command worked has nothing to do with that but guess."*"""
    result = make().run(SUCCEEDS)

    _must_be(
        result.exit_code,
        int,
        doing=f"run({SUCCEEDS!r}).exit_code",
        why="it is always a number -- None would leave a caller guessing whether the "
        "command worked",
    )
    _must_be(
        result.output,
        str,
        doing=f"run({SUCCEEDS!r}).output",
        why="output is text, decoded by the runner",
    )
    _must_be(
        getattr(result, "truncated", False),
        bool,
        doing=f"run({SUCCEEDS!r}).truncated",
        why="a caller cannot tell a cut result from a short one unless the runner says",
    )


def a_timeout_is_a_result_and_not_an_exception(make: Callable[[], CommandRunner]) -> None:
    """The one most likely to be got wrong, because every timeout API raises.

    `subprocess.run(timeout=...)` raises `TimeoutExpired`, so a runner written the
    obvious way propagates it -- and the port says otherwise: *"a timeout is a
    result, not an exception: `exit_code` 124, the shell's own, with output saying
    so. Raising would make every runner's failure the model's problem rather than a
    tool result it can read and retry."*

    124 rather than any non-zero code, because it is what `timeout(1)` returns and
    the number a reader of the output will recognise.
    """
    try:
        result = make().run(SLEEPS, timeout=1)
    except Exception as raised:
        msg = (
            f"run({SLEEPS!r}, timeout=1) raised {type(raised).__name__}: {raised}. A "
            f"timeout is a result -- exit_code 124 -- so the model can read it and "
            f"retry rather than the turn ending"
        )
        raise AssertionError(msg) from raised

    _equal(result.exit_code, 124, doing=f"run({SLEEPS!r}, timeout=1).exit_code")


def a_command_leaves_nothing_running(make: Callable[[], CommandRunner]) -> None:
    """What a command starts goes with it, when it ends and when its time runs out.

    A process left behind goes on writing into the session after the turn that started
    it has let go of it, which `claim_stale_after` counts on no command doing. It is seen
    by the mark it would make, and the mark is first made where nothing stops it: a shell
    that cannot write there would pass this having proved nothing. In `TMPDIR`, because
    under Landlock the shell cannot write the directory it starts in.

    **The background job says it began**, and its output goes beside the mark, not to
    `/dev/null`: under Landlock that redirection was refused, the job never started, and
    this passed there with nothing running to kill.
    """
    runner = make()
    base = f"${{TMPDIR:-/tmp}}/kingfisher-contract-{uuid.uuid4().hex}"
    mark, began, log = (f'"{base}{end}"' for end in ("", "-began", ".log"))

    def present(path: str) -> bool:
        return "present" in runner.run(f"test -e {path} && echo present || echo absent").output

    def started(job: str, *, then: str, timeout: int) -> None:
        runner.run(f"rm -f {began}; ({job}) >{log} 2>&1 & {then}", timeout=timeout)
        if not present(began):
            msg = (
                f"the background job never began ({runner.run(f'cat {log}').output!r}), "
                "so this check cannot see whether it would have been left behind"
            )
            raise AssertionError(msg)

    try:
        seen = runner.run(f"touch {mark} && echo present; rm -f {mark}").output
        if "present" not in seen:
            msg = (
                f"the command could not leave a mark in $TMPDIR ({seen!r}), so this check "
                f"cannot see a process left behind; give the shell a writable TMPDIR"
            )
            raise AssertionError(msg)

        # The command waits until its job has begun, so it ends with the job running.
        started(
            f"touch {began}; sleep 1; touch {mark}",
            then=f"while [ ! -e {began} ]; do sleep 0.05; done",
            timeout=5,
        )
        time.sleep(1.5)
        if present(mark):
            msg = (
                "a process the command started in the background was still running after "
                "the command ended; kill the command's process group when it exits"
            )
            raise AssertionError(msg)

        started(f"touch {began}; sleep 1.5; touch {mark}", then="sleep 30", timeout=1)
        time.sleep(1)
        if present(mark):
            msg = (
                "a process the command started was still running after the command timed "
                "out; kill the command's process group, not only the shell"
            )
            raise AssertionError(msg)
    finally:
        runner.run(f"rm -f {mark} {began} {log}")


#: Every check a `CommandRunner` must pass. These run commands, and two wait for a
#: timeout.
RUNNER_CHECKS: tuple[Callable[[Callable[[], CommandRunner]], None], ...] = (
    a_runner_says_where_it_runs,
    a_command_that_works_reports_that_it_worked,
    a_command_that_fails_is_a_result_and_not_an_exception,
    a_result_is_shaped_the_way_a_caller_reads_it,
    a_timeout_is_a_result_and_not_an_exception,
    a_command_leaves_nothing_running,
)
