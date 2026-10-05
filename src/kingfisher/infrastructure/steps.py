"""Session I/O written once, as the port calls it makes, and driven sync or async.

A sequence is a generator that yields each call it needs and is sent back the
answer. `drive` makes the call, `adrive` awaits its `a`-prefixed twin, and
everything between the calls -- every check, every refusal and their order -- is
written once, so the sync and async ways into a session cannot come apart.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Generator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from kingfisher.infrastructure.threads import finished, off_loop, thread_pool

_NO_KEYWORDS: Mapping[str, Any] = MappingProxyType({})


@dataclass(frozen=True)
class Call:
    """`method` on `target`, or its `a`-prefixed twin when driven async."""

    target: Any
    method: str
    args: tuple[Any, ...] = ()
    kwargs: Mapping[str, Any] = _NO_KEYWORDS
    #: Whether it changes anything. A cancel waits for a call that does, so what the
    #: call did reaches the sequence before the cancel does; a read is abandoned.
    changes: bool = False


@dataclass(frozen=True)
class OnHost:
    """Work on this host, its disk or its CPU: made inline when driven sync, and on
    kingfisher's pool when async. Always waited for -- a thread runs to the end
    whether or not anyone is still waiting, so abandoning one decides nothing.
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
        # Handed to the sequence rather than raised past it, so its own `except` and
        # `finally` run: a sequence that took a claim is the one that gives it back.
        except BaseException as exc:  # noqa: BLE001 -- thrown into the sequence
            failed = exc


async def adrive[T](steps: Steps[T]) -> T:
    """Run `steps` on the event loop, awaiting each call's async twin."""
    answer: Any = None
    failed: BaseException | None = None
    held: asyncio.CancelledError | None = None
    while True:
        try:
            step = steps.send(answer) if failed is None else steps.throw(failed)
        except StopIteration as done:
            if held is not None:
                raise held from None
            return done.value
        if held is not None:
            # A cancel that arrived during a call that changes something. The sequence
            # has what that call did now, so the cancel goes in here instead of the
            # next step being made.
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
