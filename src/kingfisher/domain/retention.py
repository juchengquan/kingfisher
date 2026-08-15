"""Which sessions to drop, and the order in which a session comes apart.

`plan` is a pure decision: given what exists and how many to keep, it names the
victims and touches nothing. `apply` carries out the ordering rule that has to
stay in the domain, because it is a rule and not a mechanism.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from kingfisher.domain.ports import SessionDirs, ThreadStore
from kingfisher.domain.session import Session


@dataclass(frozen=True)
class SweepResult:
    removed: tuple[str, ...]
    kept: int
    #: Sessions that could not be fully removed, with why. Surfaced rather
    #: than swallowed: a checkpointer that cannot delete at all should be
    #: visible, not silently tolerated on every run.
    failures: tuple[str, ...] = ()


@dataclass(frozen=True)
class SweepPlan:
    """Sessions to remove, newest-first survivors already excluded."""

    doomed: tuple[str, ...]
    kept: int


def expired(
    entries: Sequence[tuple[str, float]], older_than_seconds: float, now: float
) -> SweepPlan:
    """Name every session untouched for longer than `older_than_seconds`.

    Age rather than count. Keeping the newest N counts every caller's sessions
    together, so a busy caller evicts a quiet one -- fine when one person owned
    the workspace, a tenancy bug once many callers share it. Age asks only how
    long a session has been idle, which is a property of that session alone.
    """
    doomed = tuple(name for name, modified in entries if now - modified > older_than_seconds)
    return SweepPlan(doomed=doomed, kept=len(entries) - len(doomed))


def apply(
    sweep_plan: SweepPlan,
    runs: Path,
    dirs: SessionDirs,
    threads: ThreadStore | None = None,
) -> SweepResult:
    """Carry out a plan, one session at a time.

    The per-session ordering lives in `Session.discard`, which is where the
    reasoning about benign failure belongs. This only walks the list and
    collects what went wrong.
    """
    removed: list[str] = []
    failures: list[str] = []
    for name in sweep_plan.doomed:
        failure = Session(id=name, directory=runs / name).discard(dirs, threads)
        if failure:
            failures.append(failure)
        else:
            removed.append(name)

    return SweepResult(
        removed=tuple(removed),
        kept=sweep_plan.kept,
        failures=tuple(failures),
    )
