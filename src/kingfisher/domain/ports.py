"""What the domain needs the world to do for it."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class CommandResult:
    """What running one command produced."""

    output: str
    exit_code: int
    #: Set when `output` is not all of it. A runner that cuts long output says so,
    #: because a caller cannot tell a truncated result from a short one.
    truncated: bool = False


class CommandRunner(Protocol):
    """How a shell command is run, for a deployment that runs them elsewhere.

    `command` arrives already confined **when the runner is local**, which is the
    default. Applying the confinement stays on kingfisher's side so a runner cannot
    forget to -- but the confinement names paths on *this* host, so a runner shipping
    the command elsewhere must set `local` to False, and then receives the command as
    the model wrote it.
    """

    @property
    def local(self) -> bool:
        """Whether the command runs on this machine. `True` unless a runner says not.

        A read-only property rather than a plain attribute, and the difference is not
        cosmetic: a protocol declaring `local: bool` demands a *settable* one, so an
        adapter written as a frozen dataclass -- the obvious way to write one, and how
        `ReferenceRunner` in the suite is written -- does not satisfy this port. Nothing
        in kingfisher ever assigns to it, so demanding that was asking implementers for
        a guarantee no caller uses.
        """
        return True

    def run(self, command: str, *, timeout: int | None = None) -> CommandResult:
        """Run `command`, giving up after `timeout` seconds if one is given.

        A timeout is a result, not an exception: `exit_code` 124, the shell's own,
        with output saying so. Raising would make every runner's failure the
        model's problem rather than a tool result it can read and retry.
        """
        ...



@runtime_checkable
class RunEvents(Protocol):
    """Where a record of what each turn did goes: its start and end, every model call,
    every tool call.

    Not session state. It is the one record written as the turn runs rather than at
    its end, and it is most wanted for the turn that crashed, so it goes wherever a
    deployment keeps its logs and outlives the session it describes. Every event
    carries `session_id` and `turn_id`, which is how one session's are found again.
    """

    def record(self, event: Mapping[str, object]) -> None:
        """Keep one event: a flat mapping whose `event` key names what happened.

        Called once per event, and not always from the thread running the turn:
        tool calls a model makes together run at once, on an executor's threads, and
        on the async path langchain hands each event to a worker thread. So the events
        of tool calls made together can interleave, though each call's start comes
        before its end, and a sink that keeps state between events wants a lock. An
        exception here is logged and the turn goes on, because a turn is not failed
        for want of a log line -- which also means a sink that fails is heard from
        only in the log it was failing to write.
        """
        ...
