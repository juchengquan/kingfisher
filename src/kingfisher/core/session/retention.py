"""Which sessions to drop, and dropping them one at a time."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class SweepResult:
    removed: tuple[str, ...]
    kept: int
    #: How many of `kept` a running turn spared.
    busy: int = 0
    #: Sessions that could not be fully removed, with why: surfaced, not swallowed.
    failures: tuple[str, ...] = ()


@dataclass(frozen=True)
class SweepPlan:
    """Sessions to remove, those a running turn holds already excluded."""

    doomed: tuple[str, ...]
    kept: int
    #: How many of `kept` a running turn spared rather than their age, which `kept`
    #: cannot tell apart: blaming age for a busy session contradicts `kingfisher sessions`.
    busy: int = 0


def expired(
    entries: Sequence[tuple[str, float]],
    older_than_seconds: float,
    now: float,
    *,
    busy: Sequence[str] = (),
) -> SweepPlan:
    """Name every session untouched for longer than `older_than_seconds`."""
    running = set(busy)
    doomed = tuple(
        name
        for name, modified in entries
        if name not in running and now - modified > older_than_seconds
    )
    spared = tuple(name for name, _ in entries if name in running)
    return SweepPlan(
        doomed=doomed, kept=len(entries) - len(doomed), busy=len(spared)
    )


def apply(
    sweep_plan: SweepPlan,
    discard: Callable[[str], str | None],
) -> SweepResult:
    """Carry out a plan, one session at a time. `discard` answers a failure, or `None`."""
    removed: list[str] = []
    failures: list[str] = []
    for name in sweep_plan.doomed:
        failure = discard(name)
        if failure:
            failures.append(failure)
        else:
            removed.append(name)

    return SweepResult(
        removed=tuple(removed),
        kept=sweep_plan.kept,
        busy=sweep_plan.busy,
        failures=tuple(failures),
    )
