"""What a turn did, as events handed to the deployment's `RunEvents`."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from kingfisher.core.interfaces import RunEvents

from . import runtime

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


class LoggedRunEvents(RunEvents):
    """`RunEvents` into the `kingfisher.run` logger.

    `extra` carries the mapping for a handler that ships structured records. Checked
    before serialising: `json.dumps` runs whether or not the line is kept, and the
    default configuration drops INFO.
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

    def __init__(  # noqa: PLR0913 -- the stamp's four fields, and where each model runs
        self,
        sink: Any,
        *,
        model: str,
        endpoint: str,
        session_id: str,
        turn_id: str,
        endpoints: Mapping[str, str] | None = None,
    ) -> None:
        self._sink = sink
        self._stamp = {
            "session_id": session_id,
            "turn_id": turn_id,
            "model": model,
            "endpoint": endpoint,
        }
        #: Each catalogue model's endpoint, by name.
        self._endpoints = dict(endpoints or {})
        #: The model and endpoint of each call under way, by its run id.
        self._calls: dict[Any, dict[str, str]] = {}

    def _write(self, event: str, *, call: Mapping[str, str] | None = None, **fields: Any) -> None:
        stamp = {**self._stamp, **(call or {})}
        try:
            self._sink.record({"ts": time.time(), "event": event, **stamp, **fields})
        except Exception:  # noqa: BLE001 -- a turn outlives a failing sink: `run_start`
            # and `run_end` are called outside langchain's guard around callbacks.
            _log.warning("a RunEvents sink failed to record %r", event, exc_info=True)

    def run_start(self, task: str) -> None:
        self._write("run_start", task=task)

    def run_end(self, *, ok: bool, answer_chars: int) -> None:
        self._write("run_end", ok=ok, answer_chars=answer_chars)

    def on_chat_model_start(
        self,
        *_: Any,
        run_id: Any = None,
        metadata: Mapping[str, Any] | None = None,
        **__: Any,
    ) -> None:
        # Read off the call, not the turn: a subagent may run a model of its own. A name
        # the catalogue does not hold keeps the turn's stamp rather than half of one.
        named = (metadata or {}).get("ls_model_name")
        if isinstance(named, str) and named in self._endpoints:
            self._calls[run_id] = {"model": named, "endpoint": self._endpoints[named]}

    def on_llm_end(self, response: Any, *, run_id: Any = None, **_: Any) -> None:
        message = _first_message(response)
        usage = runtime.usage_of(message)
        self._write(
            MODEL_CALL,
            call=self._calls.pop(run_id, None),
            **usage,
            usage_present=bool(getattr(message, "usage_metadata", None)),
            tool_calls=list(runtime.tool_names(message)),
            cut_off=runtime.cut_off(message),
        )

    def on_tool_start(self, serialized: dict[str, Any] | None, input_str: str, **_: Any) -> None:
        name = (serialized or {}).get("name", "?")
        self._write("tool_start", tool=name, input_preview=str(input_str)[:400])

    def on_tool_end(self, output: Any, **_: Any) -> None:
        self._write("tool_end", output_preview=str(output)[:400])

    def on_tool_error(self, error: BaseException, **_: Any) -> None:
        self._write("tool_error", error=f"{type(error).__name__}: {error}")

    def on_llm_error(self, error: BaseException, *, run_id: Any = None, **_: Any) -> None:
        self._write(
            "model_error",
            call=self._calls.pop(run_id, None),
            error=f"{type(error).__name__}: {error}",
        )


def _first_message(response: Any) -> Any:
    """Pull the AIMessage out of an LLMResult, tolerating shape changes."""
    try:
        return response.generations[0][0].message
    except (AttributeError, IndexError, TypeError):
        return None
