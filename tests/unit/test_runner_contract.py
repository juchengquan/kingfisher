"""The runner port, which had no tests at all, and now has a contract."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass

import pytest

from kingfisher.domain.ports import CommandResult, CommandRunner
from kingfisher.testing import COMMAND_RUNNER_CONTRACT


@dataclass(frozen=True)
class ReferenceRunner:
    """A `CommandRunner` written the way the port asks, for the kit to run on.

    Not shipped, and not a suggestion that it should be: `runner=None` already means
    "run it here", and its own comment explains why a default runner would be *"110
    lines of upstream's truncation, timeout and exit-code shaping, copied to be kept
    in step"*. What this is for is proving the contract is satisfiable, and showing
    the one thing every implementer gets to decide wrongly.
    """

    #: Declared, though `True` is what kingfisher assumes of a runner that says
    #: nothing. Saying it is the habit the port asks for.
    local: bool = True

    def run(self, command: str, *, timeout: int | None = None) -> CommandResult:
        try:
            done = subprocess.run(  # noqa: S602 -- a shell command is the whole job
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return CommandResult(
                output=f"timed out after {timeout}s",
                exit_code=124,
            )
        return CommandResult(
            output=done.stdout + done.stderr,
            exit_code=done.returncode,
        )


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
    for check in COMMAND_RUNNER_CONTRACT:
        check(ReferenceRunner)


def _built_by(make: Callable[[], CommandRunner]) -> CommandRunner:
    """The argument position the kit uses, which is where conformance is decided."""
    return make()


def test_the_reference_runner_satisfies_the_port_it_was_written_from() -> None:
    """Asked of the type checker, because the suite cannot ask it.

    `COMMAND_RUNNER_CONTRACT` takes a factory, and every check here is handed
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


def test_the_contracts_are_not_quietly_empty():
    """A hand-maintained tuple, and a rule walking an empty one passes having checked
    nothing.
    """
    assert len(COMMAND_RUNNER_CONTRACT) >= 5
    assert all(callable(check) for check in COMMAND_RUNNER_CONTRACT)


# -- what the checks refuse -------------------------------------------------
#
# A kit is only worth what it catches, and the port it covers is one
# whose rules nothing else in this repository enforces. These are the mutations
# from the commit message, kept so they stay caught.


@dataclass(frozen=True)
class RaisingRunner:
    """A runner that lets `TimeoutExpired` out -- the obvious implementation."""

    local: bool = True

    def run(self, command: str, *, timeout: int | None = None) -> CommandResult:
        done = subprocess.run(  # noqa: S602
            command, shell=True, capture_output=True, text=True, timeout=timeout, check=False
        )
        return CommandResult(output=done.stdout, exit_code=done.returncode)


def _run(contract, name, subject):
    """One named check from a contract, against one subject."""
    (check,) = [c for c in contract if c.__name__ == name]
    check(subject)


@pytest.mark.skipif(sys.platform == "win32", reason="the check runs a POSIX shell command")
def test_a_timeout_that_raises_is_caught():
    """The one every implementer gets to write wrongly, because every timeout API in
    Python raises.
    """
    with pytest.raises(AssertionError, match="timeout is a result"):
        _run(
            COMMAND_RUNNER_CONTRACT,
            "a_timeout_is_a_result_and_not_an_exception",
            RaisingRunner,
        )
