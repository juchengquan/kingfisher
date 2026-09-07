"""The two ports that had no tests at all, and now have contracts.

`SessionRoot` and `CommandRunner` were named in no test file in this repository.
Their implementations were exercised -- `LocalSessionRoot` through every turn a
test runs, a runner through `test_confinement` -- but nothing was written against
the *ports*, so the rules a deployment has to keep lived only in two docstrings.
`docs/guides/ports.md` wrote them down; this makes them run.

The two halves are not symmetric, and the reason is worth knowing before reading
on. `SessionRoot` ships an implementation, so its kit is proved the way the store
kits are: against the thing it was extracted from. `CommandRunner` ships none --
`runner=None` means kingfisher runs the command itself, inside the shell backend
-- so there is nothing to point the kit at, and a reference runner is written
here instead.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from kingfisher.domain.ports import CommandResult
from kingfisher.infrastructure.workspace.sessions import LocalSessionRoot
from kingfisher.testing import COMMAND_RUNNER_CONTRACT, SESSION_ROOT_CONTRACT


@pytest.mark.parametrize("check", SESSION_ROOT_CONTRACT, ids=lambda c: c.__name__)
def test_the_local_session_root_keeps_the_port_contract(check, tmp_path):
    """The kit, against the implementation it was written from.

    A fresh workspace per check, because several of them create directories and
    one holds two sessions at once.
    """
    made = 0

    def make():
        nonlocal made
        made += 1
        return LocalSessionRoot(tmp_path / f"ws-{made}")

    check(make)


@dataclass(frozen=True)
class ReferenceRunner:
    """A `CommandRunner` written the way the port asks, for the kit to run on.

    Not shipped, and not a suggestion that it should be: `runner=None` already
    means "run it here", and its own comment explains why a default runner would
    be *"110 lines of upstream's truncation, timeout and exit-code shaping,
    copied to be kept in step"*. What this is for is proving the contract is
    satisfiable, and showing the one thing every implementer gets to decide
    wrongly.

    That one thing is the timeout. `subprocess.run(timeout=...)` raises, so the
    obvious runner propagates `TimeoutExpired` -- and the port wants a result
    with `exit_code` 124 instead, so the model reads a tool result it can retry
    rather than the turn ending. Four lines, and the whole reason the check
    exists.
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
@pytest.mark.parametrize("check", COMMAND_RUNNER_CONTRACT, ids=lambda c: c.__name__)
def test_a_reference_runner_keeps_the_port_contract(check):
    """A kit with nothing to run against is a kit nobody has run.

    Which is the failure the session store's twelve checks were written to
    avoid, and it applies harder here: `CommandRunner` had no implementation in
    this repository at all, so every rule in these checks came from a docstring
    rather than from something that works.
    """
    check(ReferenceRunner)


def test_the_contracts_are_not_quietly_empty():
    """Both are hand-maintained tuples, and a parametrised test over an empty
    one passes by not existing."""
    assert len(SESSION_ROOT_CONTRACT) >= 6
    assert len(COMMAND_RUNNER_CONTRACT) >= 5
    assert all(callable(check) for check in (*SESSION_ROOT_CONTRACT, *COMMAND_RUNNER_CONTRACT))


# -- what the checks refuse -------------------------------------------------
#
# A kit is only worth what it catches, and the two ports it covers are the ones
# whose rules nothing else in this repository enforces. These are the mutations
# from the commit message, kept so they stay caught.


class SharedRoot:
    """A root that ignores the session id -- the isolation failure."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)

    def hold(self, session_id: str):
        from contextlib import contextmanager

        @contextmanager
        def held():
            yield self.workspace / "shared"

        return held()


class SwallowingRoot:
    """A root whose context manager returns true from `__exit__`."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)

    def hold(self, session_id: str):
        root = self.workspace / session_id

        class Held:
            def __enter__(self):
                return root

            def __exit__(self, *_: object) -> bool:
                return True

        return Held()


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


def test_two_sessions_in_one_directory_is_caught(tmp_path):
    """The security-relevant one: every path is legal, and each session reads
    the other's files as its own."""
    with pytest.raises(AssertionError, match="Two sessions in one directory"):
        _run(
            SESSION_ROOT_CONTRACT,
            "two_sessions_are_two_directories",
            lambda: SharedRoot(tmp_path),
        )


def test_a_hold_that_swallows_a_failure_is_caught(tmp_path):
    """A turn that failed reported as one that succeeded, with whatever was
    mounted still mounted."""
    with pytest.raises(AssertionError, match="would be swallowed"):
        _run(
            SESSION_ROOT_CONTRACT,
            "a_failed_turn_still_leaves_the_hold",
            lambda: SwallowingRoot(tmp_path),
        )


@pytest.mark.skipif(sys.platform == "win32", reason="the check runs a POSIX shell command")
def test_a_timeout_that_raises_is_caught():
    """The one every implementer gets to write wrongly, because every timeout
    API in Python raises."""
    with pytest.raises(AssertionError, match="timeout is a result"):
        _run(
            COMMAND_RUNNER_CONTRACT,
            "a_timeout_is_a_result_and_not_an_exception",
            RaisingRunner,
        )
