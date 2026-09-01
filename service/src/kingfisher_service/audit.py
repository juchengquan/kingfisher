"""What happened, for whoever has to answer that later.

A log, not a store, and the distinction is the whole design. A keyed store of
results would be a fourth kind of residue -- after sessions, threads and claims,
two of which leaked until they were fixed -- and nothing in the server sweeps
anything. A log stream has no such problem: rotation, retention and destination
are the operator's, configured on a handler like every other log they run.

It records what `JsonlRunLogger` cannot. That logger is built inside a turn, so
everything refused *before* a turn exists -- an unknown session, a busy one, a
quota, a reference that does not resolve -- leaves no trace anywhere. Measured:
a refused request writes nothing at all, in a surface where a caller probing
session ids is exactly the thing an operator would want to see afterwards.

Session ids are here on purpose, and that is the difference from `access`. The
access log goes to stdout and omits them because a session id is a bearer
credential. This is the record that exists to say *which* session did what, so
it is a separate logger with no handler by default: an operator wiring one is
choosing where those ids may be written, which is a decision worth making
explicitly rather than by default.

Content -- the task and the answer -- is off unless asked for. What may be kept,
and for how long, is a question about the deployment's obligations rather than
about kingfisher, so it is a switch rather than a judgement made here.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from time import perf_counter
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from kingfisher import RunEvent
    from kingfisher_service.config import ServiceConfig

#: Its own logger, unconfigured. Nothing is written until a deployment attaches
#: a handler, which is how "may session ids be written here" stays a decision
#: somebody makes rather than a default they inherit.
logger = logging.getLogger("kingfisher.audit")

#: No timestamp field: the handler's formatter owns that, and a second clock in
#: the payload is a second thing that can disagree with the first.
TURN = "turn"
REFUSED = "refused"


@dataclass(frozen=True)
class Attempt:
    """What was asked for, and when the asking started.

    One object rather than four parameters threaded through both paths, because
    a refusal and a turn are the same attempt seen at different depths -- and
    the fields they share are exactly the ones a reader correlates on.
    """

    session_id: str | None
    task: str
    started: float
    settings: ServiceConfig
    #: The groups this request was resolved as, or `None` where the deployment
    #: controls nothing by group.
    #:
    #: Here rather than in `access`, which is the other log and deliberately
    #: carries no session id because that is a bearer credential. A group name
    #: is not one -- it cannot be replayed -- so the reason that log omits
    #: identity does not transfer. What does transfer is the caution: this
    #: logger has no handler until a deployment attaches one, so a deployment
    #: uneasy about group names in logs attaches none.
    #:
    #: Written on refusals as well as turns, and the refusals are the half that
    #: earns it: it is how somebody tells a gateway that has drifted from its
    #: vocabulary apart from a caller who genuinely may not.
    groups: tuple[str, ...] | None = None

    @property
    def elapsed_ms(self) -> float:
        return round((perf_counter() - self.started) * 1000, 1)


def _write(**fields: Any) -> None:
    if logger.isEnabledFor(logging.INFO):
        logger.info(json.dumps({k: v for k, v in fields.items() if v is not None}))


def refused(attempt: Attempt, error: BaseException, *, status: int, code: str) -> None:
    """One line for a request that never became a turn.

    The half nothing else sees. `reason` is the machine-readable code the caller
    was given, so a line here and the response that caller got say the same
    thing -- which is what makes the two correlatable at all without logging a
    request id neither side keeps.
    """
    _write(
        event=REFUSED,
        session_id=attempt.session_id,
        reason=code,
        status=status,
        detail=type(error).__name__,
        duration_ms=attempt.elapsed_ms,
        groups=list(attempt.groups) if attempt.groups else None,
    )


async def watching(
    events: AsyncIterator[RunEvent], first: RunEvent | None, attempt: Attempt
) -> AsyncIterator[RunEvent]:
    """Pass every event through, and write one line when the turn ends.

    A wrapper rather than a hook inside `streaming`, so the streaming code stays
    about SSE and knows nothing about auditing.

    The `finally` is what makes a hangup auditable: closing this generator lands
    there whether the turn answered, was cut short, or had its client walk away
    -- and "walked away" is the outcome an operator is least able to reconstruct
    from anywhere else.
    """
    totals = {"input_tokens": 0, "output_tokens": 0}
    outcome = "stopped"
    turn_id: str | None = None
    answer: str | None = None
    # A one-shot `POST /turns` names no session, so the attempt has none to
    # record -- but the turn is given one, and a line an operator cannot tie to
    # a session is most of the value gone. Taken from the result, which is where
    # the caller learns it too.
    session_id = attempt.session_id

    def account(event: RunEvent) -> None:
        nonlocal outcome, turn_id, answer, session_id
        for name in totals:
            totals[name] += int(event.usage.get(name, 0) or 0)
        if event.kind == "cut_short":
            outcome = "cut_short"
        elif event.kind == "finished" and event.result is not None:
            outcome = "cut_short" if event.result.cut_short else "ok"
            turn_id = event.result.turn_id
            answer = event.result.answer
            session_id = event.result.session_id

    if first is not None:
        account(first)
    try:
        async for event in events:
            account(event)
            yield event
    finally:
        keep = attempt.settings.audit_content
        _write(
            event=TURN,
            session_id=session_id,
            turn_id=turn_id,
            outcome=outcome,
            duration_ms=attempt.elapsed_ms,
            input_tokens=totals["input_tokens"] or None,
            output_tokens=totals["output_tokens"] or None,
            task=attempt.task if keep else None,
            answer=answer if keep else None,
            groups=list(attempt.groups) if attempt.groups else None,
        )
