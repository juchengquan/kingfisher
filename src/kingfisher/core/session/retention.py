"""Which sessions to drop, and dropping them one at a time."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class SweepResult:
    removed: tuple[str, ...]
    #: Sessions a running turn held, and so spared.
    kept: int
    #: Sessions that could not be fully removed, with why: surfaced, not swallowed.
    failures: tuple[str, ...] = ()


@dataclass(frozen=True)
class SweepPlan:
    """Sessions to remove, those a running turn holds already excluded."""

    doomed: tuple[str, ...]
    kept: int


class Spared(Enum):
    """What `discard` answers for a session a turn claimed after the plan named it."""

    BUSY = "busy"


def unheld(names: Sequence[str], *, busy: Sequence[str] = ()) -> SweepPlan:
    """Name every session a running turn does not hold."""
    running = set(busy)
    doomed = tuple(name for name in names if name not in running)
    return SweepPlan(doomed=doomed, kept=len(names) - len(doomed))


def apply(
    sweep_plan: SweepPlan,
    discard: Callable[[str], str | Spared | None],
) -> SweepResult:
    """Carry out a plan, one session at a time. `discard` answers a failure, `BUSY`, or
    `None`."""
    removed: list[str] = []
    failures: list[str] = []
    late = 0
    for name in sweep_plan.doomed:
        failure = discard(name)
        if failure is Spared.BUSY:
            late += 1
        elif failure:
            failures.append(failure)
        else:
            removed.append(name)

    return SweepResult(
        removed=tuple(removed), kept=sweep_plan.kept + late, failures=tuple(failures)
    )
