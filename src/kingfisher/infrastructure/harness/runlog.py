"""What a turn did, as events handed to the deployment's `RunEvents`."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from kingfisher.infrastructure.harness import runtime

MODEL_CALL = "model_call"

#: Where `LoggedRunEvents` writes, and so where a deployment that wired nothing finds
#: its run events: one JSON line per event, at INFO.
RUN_LOGGER = "kingfisher.run"

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Usage:
    """What a run's model calls cost, totalled from its events."""

    calls: int
    input_tokens: int
    output_tokens: int
    cache_read: int

    @property
    def cached_share(self) -> float | None:
        """Fraction of input tokens served from cache, or None if none were sent."""
        return self.cache_read / self.input_tokens if self.input_tokens else None


def usage_of(events: Iterable[Mapping[str, Any]]) -> Usage:
    """Total the model calls among these events."""
    calls = [e for e in events if e.get("event") == MODEL_CALL]
    return Usage(
        calls=len(calls),
        input_tokens=sum(e.get("input_tokens", 0) for e in calls),
        output_tokens=sum(e.get("output_tokens", 0) for e in calls),
        cache_read=sum(e.get("cache_read", 0) for e in calls),
    )


class LoggedRunEvents:
    """`RunEvents` into the `kingfisher.run` logger, which is where a deployment that
    wired nothing finds them.

    The event travels as `extra={"run_event": ...}` as well as the message, so a
    handler that ships structured records has the mapping rather than a string to
    parse back. Serialised only when the logger would emit it: the default Python
    configuration drops INFO, and a turn should not pay to format lines nobody keeps.
    """

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger(RUN_LOGGER)

    def record(self, event: Mapping[str, object]) -> None:
        if self._logger.isEnabledFor(logging.INFO):
            self._logger.info(
                "%s",
                json.dumps(event, ensure_ascii=False, default=str),
                extra={"run_event": dict(event)},
            )


class RunLogger(BaseCallbackHandler):
    """Turns one turn's model and tool callbacks into events for a `RunEvents`."""

    def __init__(
        self, sink: Any, *, model: str, endpoint: str, session_id: str, turn_id: str
    ) -> None:
        self._sink = sink
        self._stamp = {
            "session_id": session_id,
            "turn_id": turn_id,
            "model": model,
            "endpoint": endpoint,
        }

    def _write(self, event: str, **fields: Any) -> None:
        try:
            self._sink.record({"ts": time.time(), "event": event, **self._stamp, **fields})
        except Exception:  # noqa: BLE001 -- the sink is the deployment's code, and
            # any failure of it is one a turn must outlive: `run_start` and `run_end`
            # are called from kingfisher's own turn, where langchain's own guard
            # around callbacks does not reach.
            _log.warning("a RunEvents sink failed to record %r", event, exc_info=True)

    # -- lifecycle ---------------------------------------------------------

    def run_start(self, task: str) -> None:
        self._write("run_start", task=task)

    def run_end(self, *, ok: bool, answer_chars: int) -> None:
        self._write("run_end", ok=ok, answer_chars=answer_chars)

    # -- callbacks ---------------------------------------------------------

    def on_llm_end(self, response: Any, **_: Any) -> None:
        message = _first_message(response)
        usage = runtime.usage_of(message)
        self._write(
            MODEL_CALL,
            **usage,
            usage_present=bool(getattr(message, "usage_metadata", None)),
            tool_calls=list(runtime.tool_names(message)),
        )

    def on_tool_start(self, serialized: dict[str, Any] | None, input_str: str, **_: Any) -> None:
        name = (serialized or {}).get("name", "?")
        self._write("tool_start", tool=name, input_preview=str(input_str)[:400])

    def on_tool_end(self, output: Any, **_: Any) -> None:
        self._write("tool_end", output_preview=str(output)[:400])

    def on_tool_error(self, error: BaseException, **_: Any) -> None:
        self._write("tool_error", error=f"{type(error).__name__}: {error}")

    def on_llm_error(self, error: BaseException, **_: Any) -> None:
        self._write("model_error", error=f"{type(error).__name__}: {error}")


def _first_message(response: Any) -> Any:
    """Pull the AIMessage out of an LLMResult, tolerating shape changes."""
    try:
        return response.generations[0][0].message
    except (AttributeError, IndexError, TypeError):
        return None
