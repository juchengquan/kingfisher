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
    #: Threads deleted because no session owned them any more. Reported
    #: separately from `removed` because they are not sessions this sweep
    #: decided to end -- they are residue from ones that ended some other way.
    orphans: tuple[str, ...] = ()


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


def orphaned(threads: Sequence[str], sessions: Sequence[str]) -> tuple[str, ...]:
    """Threads no session owns any more.

    A thread is the conversation; the directory is the session. `discard`
    removes both, so a sweep leaves neither behind -- but a session directory
    that goes any other way leaves its thread forever, because nothing else
    looks. Measured on one real workspace: after reaping all 55 sessions, 132
    threads and 1,894 checkpoints remained, owned by nothing.

    They are unreachable as well as unused. A session id whose directory is
    gone is refused with `UnknownSessionError` -- the directory is what proves
    a session exists -- so the conversation behind an orphaned thread can never
    be resumed by anyone. Deleting it loses nothing that could have been read.

    A set difference, kept pure and here rather than in the janitor, because
    "what is residue" is the same kind of decision as "what has expired".
    """
    live = set(sessions)
    return tuple(sorted(t for t in set(threads) if t not in live))


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
