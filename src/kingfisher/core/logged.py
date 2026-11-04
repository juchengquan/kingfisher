"""What kingfisher's logs may say, and where they go: the deployment's `RunEvents`, or
Python's logging where there is none to hand them to."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import TYPE_CHECKING

from .agent.tool_context import current_graph

if TYPE_CHECKING:
    from .interfaces import RunEvents

#: An event's `level`, in the names Python's logging and loguru share.
INFO = "INFO"
WARNING = "WARNING"

#: Where the default sink writes each kind of event.
RUN_LOGGER = "kingfisher.run"
WARNINGS_LOGGER = "kingfisher.warnings"
#: Not `kingfisher`, every other logger's parent: raised to INFO for this line, it would
#: turn on the run events a deployment had left off.
ORIGINS_LOGGER = "kingfisher.origins"

#: What a `Kingfisher` says once built: where it reads from.
ORIGINS = "origins"
#: What a sink that raised is reported as, to Python's logging and never to itself.
RUN_EVENTS_FAILED = "run_events_failed"

#: The ids a sink's failure keeps from the event it dropped.
IDS = ("session_id", "turn_id", "turn_agent", "agent", "call")


def raised(exc: BaseException) -> tuple[str, str | None]:
    """`exc` as its class's name and where it was raised, `folder/file.py:LINE in FUNC`,
    or `None` for one never raised.

    Never its message, which is content: a tool's `ValidationError` quotes the argument
    the model sent, and a `FileNotFoundError` the path.
    """
    trace = exc.__traceback__
    if trace is None:
        return type(exc).__name__, None
    while trace.tb_next is not None:
        trace = trace.tb_next
    code = trace.tb_frame.f_code
    where = "/".join(PurePath(code.co_filename).parts[-2:])
    return type(exc).__name__, f"{where}:{trace.tb_lineno} in {code.co_name}"


@dataclass(frozen=True)
class TurnWarnings:
    """Where one turn's warnings go, and the ids they carry once the turn has them."""

    sink: RunEvents
    ids: dict[str, object] = field(default_factory=dict)


#: Set only around the turn's own work, never across a yield to its caller: a caller
#: reading two streams at once would hear one turn's warnings as the other's.
_TURN: ContextVar[TurnWarnings | None] = ContextVar("kingfisher_turn_warnings", default=None)


@contextmanager
def warning_into(turn: TurnWarnings) -> Iterator[None]:
    """Have `warn` hand its events to `turn` until the block ends."""
    token = _TURN.set(turn)
    try:
        yield
    finally:
        _TURN.reset(token)


def stamp_warnings(*, session_id: str, turn_id: str, turn_agent: str | None) -> None:
    """Name the turn on every warning it raises from here on, where one is being heard.

    Changed in place, not set: one holder is set around every slice of the turn's work,
    those still to come included.
    """
    if (turn := _TURN.get()) is not None:
        turn.ids.update(session_id=session_id, turn_id=turn_id, turn_agent=turn_agent)


def warning(event: str, message: str, /, **details: object) -> dict[str, object]:
    """A warning as an event: once its turn is named, with the turn's ids and the agent
    whose tool call raised it; then `details`, which replace none of the keys before them."""
    turn = _TURN.get()
    shaped: dict[str, object] = {
        "ts": time.time(),
        "event": event,
        "level": WARNING,
        "message": message,
    }
    if turn is not None and turn.ids:
        shaped |= {**turn.ids, "agent": current_graph()}
    for name, value in details.items():
        shaped.setdefault(name, value)
    return shaped


def warn(event: str, message: str, /, **details: object) -> None:
    """Hand a warning to the running turn's `RunEvents`, or outside a turn to Python's
    logging.

    `event` names it, `message` says it to a person, and `details` are its fields. None of
    them may hold what a caller, a tool or a model said: logs reach people the caller never
    sent it to.
    """
    turn = _TURN.get()
    handed(None if turn is None else turn.sink, warning(event, message, **details))


def handed(sink: RunEvents | None, event: Mapping[str, object]) -> None:
    """Hand `event` to `sink`, or with none to Python's logging.

    A sink that raises is not told so: its failure goes to Python's logging, since through
    the sink it would fail again, each time telling of the last.
    """
    if sink is None:
        logged(event)
        return
    try:
        sink.record(event)
    except Exception as exc:  # noqa: BLE001 -- a turn outlives a failing sink
        kind, at = raised(exc)
        logged({
            "ts": time.time(),
            "event": RUN_EVENTS_FAILED,
            "level": WARNING,
            "message": (
                f"a RunEvents sink failed to record {event.get('event')!r}: it raised "
                f"{kind} at {at}"
            ),
            **{name: event[name] for name in IDS if name in event},
            "dropped": event.get("event"),
            "error_type": kind,
            "error_at": at,
        })


def logged(event: Mapping[str, object]) -> None:
    """`event` into Python's logging, as the default `RunEvents` writes each one.

    A warning to `kingfisher.warnings` and the origins line to `kingfisher.origins`, each as
    its sentence, and a run event to `kingfisher.run` as one JSON line. The whole event
    rides on the record as `kingfisher`, for a handler that ships structured logs.
    """
    if event.get("level") == WARNING:
        logger, level = logging.getLogger(WARNINGS_LOGGER), logging.WARNING
    elif event.get("event") == ORIGINS:
        logger, level = logging.getLogger(ORIGINS_LOGGER), logging.INFO
    else:
        logger, level = logging.getLogger(RUN_LOGGER), logging.INFO
    # Before serialising: `json.dumps` runs whether or not the record is kept, and
    # Python's default drops INFO.
    if not logger.isEnabledFor(level):
        return
    said = (
        json.dumps(event, ensure_ascii=False, default=str)
        if logger.name == RUN_LOGGER
        else event.get("message")
    )
    logger.log(level, "%s", said, extra={"kingfisher": dict(event)})
