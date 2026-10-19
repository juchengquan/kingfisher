"""Session I/O written once, as the port calls it makes, and driven sync or async.

A sequence is a generator that yields each call it needs and is sent back the answer.
`drive` makes the call and `adrive` awaits its `a`-prefixed twin, so every check between
the calls is written once and the sync and async ways in cannot come apart.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Generator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .threads import finished, off_loop, thread_pool

_NO_KEYWORDS: Mapping[str, Any] = MappingProxyType({})


@dataclass(frozen=True)
class Call:
    """`method` on `target`, or its `a`-prefixed twin when driven async."""

    target: Any
    method: str
    args: tuple[Any, ...] = ()
    kwargs: Mapping[str, Any] = _NO_KEYWORDS
    #: Whether it changes anything. A cancel waits for a call that does, so its effect
    #: reaches the sequence first; a read is abandoned.
    changes: bool = False


@dataclass(frozen=True)
class OnHost:
    """Work on this host's disk or CPU: inline when driven sync, on kingfisher's pool
    when async. Always waited for, since a thread runs to the end regardless.
    """

    fn: Callable[..., Any]
    args: tuple[Any, ...] = ()
    kwargs: Mapping[str, Any] = _NO_KEYWORDS


type Step = Call | OnHost
type Steps[T] = Generator[Step, Any, T]


def reading(target: Any, method: str, /, *args: Any, **kwargs: Any) -> Call:
    """A call that changes nothing."""
    return Call(target, method, args, MappingProxyType(kwargs))


def changing(target: Any, method: str, /, *args: Any, **kwargs: Any) -> Call:
    """A call that changes something."""
    return Call(target, method, args, MappingProxyType(kwargs), changes=True)


def on_host(fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> OnHost:
    """Work on this host's disk or CPU."""
    return OnHost(fn, args, MappingProxyType(kwargs))


def drive[T](steps: Steps[T]) -> T:
    """Run `steps`, making each call as it is asked for."""
    answer: Any = None
    failed: BaseException | None = None
    while True:
        try:
            step = steps.send(answer) if failed is None else steps.throw(failed)
        except StopIteration as done:
            return done.value
        answer, failed = None, None
        try:
            answer = _made(step)
        # Thrown into the sequence, not raised past it, so a sequence that took a claim
        # gives it back.
        except BaseException as exc:  # noqa: BLE001 -- thrown into the sequence
            failed = exc


async def adrive[T](steps: Steps[T]) -> T:
    """Run `steps` on the event loop, awaiting each call's async twin."""
    answer, held = await adrive_finished(steps)
    if held is not None:
        raise held from None
    return answer


async def adrive_finished[T](steps: Steps[T]) -> tuple[T, asyncio.CancelledError | None]:
    """`adrive`, handing back with the answer a cancel that arrived during the last call.

    A cancel during an earlier call is thrown into the sequence, which gives back what
    it took. One during the last has no later step to go in at, and raised here would
    drop an answer holding a claim or a saver only the caller can now let go of.
    """
    answer: Any = None
    failed: BaseException | None = None
    held: asyncio.CancelledError | None = None
    while True:
        try:
            step = steps.send(answer) if failed is None else steps.throw(failed)
        except StopIteration as done:
            return done.value, held
        if held is not None:
            # A cancel held over a changing call: the sequence has its result now, so the
            # cancel goes in instead of the next step.
            answer, failed, held = None, held, None
            continue
        answer, failed = None, None
        try:
            if isinstance(step, OnHost) or step.changes:
                answer, held = await finished(_awaited(step))
            else:
                answer = await _awaited(step)
        except BaseException as exc:  # noqa: BLE001 -- thrown into the sequence, as in `drive`
            failed = exc


def _made(step: Any) -> Any:
    if isinstance(step, OnHost):
        return step.fn(*step.args, **step.kwargs)
    if isinstance(step, Call):
        return getattr(step.target, step.method)(*step.args, **step.kwargs)
    raise _not_a_step(step)


def _awaited(step: Any) -> Awaitable[Any]:
    if isinstance(step, OnHost):
        return off_loop(thread_pool(), step.fn, *step.args, **step.kwargs)
    if isinstance(step, Call):
        return getattr(step.target, f"a{step.method}")(*step.args, **step.kwargs)
    raise _not_a_step(step)


def _not_a_step(step: Any) -> TypeError:
    return TypeError(
        f"a sequence yielded {step!r}, which is not a step: a sequence one calls goes "
        "under `yield from`, not `yield`"
    )
