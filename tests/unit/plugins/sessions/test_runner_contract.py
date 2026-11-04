"""The runner port, which had no tests at all, and now has a contract."""

from __future__ import annotations

import inspect
import os
import signal
import subprocess
import sys
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass

import pytest

from kingfisher.core.interfaces import CommandResult, CommandRunner
from tests.checks import runner as runner_kit
from tests.checks.runner import RUNNER_CHECKS


@dataclass(frozen=True)
class ReferenceRunner(CommandRunner):
    """A `CommandRunner` written the way the port asks, for the kit to run on.

    Not shipped: `runner=None` already means "run it here", through kingfisher's own
    launcher. What this is for is proving the contract is satisfiable, and showing the
    two things every implementer gets to decide wrongly: a timeout that raises, and a
    kill that reaches the shell and nothing it started.
    """

    #: Declared, though `True` is what kingfisher assumes of a runner that says
    #: nothing. Saying it is the habit the port asks for.
    local: bool = True

    def run(self, command: str, *, timeout: int | None = None) -> CommandResult:
        process = subprocess.Popen(  # noqa: S602 -- a shell command is the whole job
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            # A group of its own, so the kill below reaches what the command started.
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_group(process)
            process.communicate()
            return CommandResult(output=f"timed out after {timeout}s", exit_code=124)
        _kill_group(process)
        return CommandResult(output=stdout + stderr, exit_code=process.returncode)


def _kill_group(process: subprocess.Popen[str]) -> None:
    """Everything the command started, not only the shell."""
    with suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGKILL)


@pytest.mark.skipif(sys.platform == "win32", reason="the checks run POSIX shell commands")
def test_a_reference_runner_keeps_the_port_contract():
    """A kit with nothing to run against is a kit nobody has run.

    Nothing to keep apart: each check is handed the class and builds its own runner.

    This was the last fan-out here to stay parametrised, and what kept it was a defect
    rather than a hazard. Walked in a loop, `check` takes its type from the tuple, and
    `ty` then refused the argument -- `CommandRunner` declared `local` as a settable
    attribute and the reference is a frozen dataclass, so the reference implementation
    of the port did not satisfy it. The port asks for a read-only `local` now, which is
    what it always read, and `test_the_reference_runner_satisfies_the_port_it_was_written_from`
    asks the question directly rather than leaving it to this loop.
    """
    for check in RUNNER_CHECKS:
        check(ReferenceRunner)


def _built_by(make: Callable[[], CommandRunner]) -> CommandRunner:
    """The argument position the kit uses, which is where conformance is decided."""
    return make()


def test_the_reference_runner_satisfies_the_port_it_was_written_from() -> None:
    """Asked of the type checker, because the suite cannot ask it.

    `RUNNER_CHECKS` takes a factory, and every check here is handed
    `ReferenceRunner` -- but through `pytest.mark.parametrize` the argument is
    effectively `Any`, so nothing has ever asked whether the reference implementation
    of this port implements it. It did not: `local` was declared `local: bool` on the
    protocol, which demands a *settable* attribute, and the reference is a frozen
    dataclass.

    Written as a call rather than an annotation because the annotation does not fail:
    `ty` accepts `x: Callable[[], CommandRunner] = ReferenceRunner` and refuses the
    same class passed as an argument, which is the shape the kit actually uses.
    """
    assert _built_by(ReferenceRunner).local is True


def test_the_kit_runs_every_check_it_defines():
    """A check the kit defines and `RUNNER_CHECKS` leaves out is one no deployment running
    the kit ever runs."""
    # Public and defined in the kit itself: its helpers are private, and what it imports
    # is not a check.
    defined = {
        name
        for name, value in vars(runner_kit).items()
        if inspect.isfunction(value)
        and value.__module__ == runner_kit.__name__
        and not name.startswith("_")
    }

    assert defined
    assert sorted(check.__name__ for check in RUNNER_CHECKS) == sorted(defined)


# -- what the checks refuse -------------------------------------------------
#
# A kit is only worth what it catches, and the port it covers is one
# whose rules nothing else in this repository enforces. These are the mutations
# from the commit message, kept so they stay caught.


@dataclass(frozen=True)
class RaisingRunner(CommandRunner):
    """A runner that lets `TimeoutExpired` out -- the obvious implementation."""

    local: bool = True

    def run(self, command: str, *, timeout: int | None = None) -> CommandResult:
        done = subprocess.run(  # noqa: S602
            command, shell=True, capture_output=True, text=True, timeout=timeout, check=False
        )
        return CommandResult(output=done.stdout, exit_code=done.returncode)


@dataclass(frozen=True)
class ShellOnlyRunner(CommandRunner):
    """A runner whose timeout kills the shell and leaves what it started -- the obvious
    implementation, and what every runner here did until the kit asked."""

    local: bool = True

    def run(self, command: str, *, timeout: int | None = None) -> CommandResult:
        try:
            done = subprocess.run(  # noqa: S602
                command, shell=True, capture_output=True, text=True, timeout=timeout, check=False
            )
        except subprocess.TimeoutExpired:
            return CommandResult(output="timed out", exit_code=124)
        return CommandResult(output=done.stdout + done.stderr, exit_code=done.returncode)


@dataclass(frozen=True)
class BlindRunner(CommandRunner):
    """A runner whose shell cannot leave a mark, so no leftover could ever be seen."""

    local: bool = True

    def run(self, command: str, *, timeout: int | None = None) -> CommandResult:
        return CommandResult(output="absent", exit_code=0)


@dataclass(frozen=True)
class RefusedRedirectRunner(CommandRunner):
    """A runner whose shell refuses the job's redirection, as Landlock refused
    `>/dev/null`: the job never starts, so no mark could ever appear."""

    local: bool = True

    def run(self, command: str, *, timeout: int | None = None) -> CommandResult:
        refused = command.replace('.log"', '.log/refused"')
        return ReferenceRunner().run(refused, timeout=timeout)


def _run(contract, name, subject):
    """One named check from a contract, against one subject."""
    (check,) = [c for c in contract if c.__name__ == name]
    check(subject)


class ShapedRunner:
    """Every method a runner has, subclassing nothing."""

    local = True

    def run(self, command: str, *, timeout: int | None = None) -> CommandResult:
        return CommandResult(output="", exit_code=0)


def test_a_runner_not_subclassing_the_interface_is_caught():
    """Driven with a runner that has `local` and `run` both: kingfisher refuses it the
    first time a session opens, so a kit passing it would pass a runner that never runs."""
    with pytest.raises(AssertionError, match="subclasses CommandRunner"):
        _run(RUNNER_CHECKS, "a_runner_says_where_it_runs", ShapedRunner)


@pytest.mark.skipif(sys.platform == "win32", reason="the check runs a POSIX shell command")
def test_a_timeout_that_raises_is_caught():
    """The one every implementer gets to write wrongly, because every timeout API in
    Python raises.
    """
    with pytest.raises(AssertionError, match="timeout is a result"):
        _run(
            RUNNER_CHECKS,
            "a_timeout_is_a_result_and_not_an_exception",
            RaisingRunner,
        )


@pytest.mark.skipif(sys.platform == "win32", reason="the check runs POSIX shell commands")
def test_a_runner_that_kills_only_the_shell_is_caught():
    """Every runner kingfisher had did this: deepagents' and both Linux fences killed the
    shell on a timeout and left whatever it had started running."""
    with pytest.raises(AssertionError, match="process group"):
        _run(RUNNER_CHECKS, "a_command_leaves_nothing_running", ShellOnlyRunner)


def test_a_runner_that_cannot_leave_a_mark_is_not_passed():
    """Without the control the check passes against a shell that could not show a
    leftover, which is what Landlock's working directory would have been."""
    with pytest.raises(AssertionError, match="cannot see a process left behind"):
        _run(RUNNER_CHECKS, "a_command_leaves_nothing_running", BlindRunner)


@pytest.mark.skipif(sys.platform == "win32", reason="the check runs POSIX shell commands")
def test_a_job_that_never_began_is_not_passed():
    """The check passed under Landlock with its job refused `/dev/null`: nothing ran, so
    nothing was left behind to find."""
    with pytest.raises(AssertionError, match="never began"):
        _run(RUNNER_CHECKS, "a_command_leaves_nothing_running", RefusedRedirectRunner)
