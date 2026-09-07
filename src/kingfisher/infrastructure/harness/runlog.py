"""Local-only structured run log."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from kingfisher.infrastructure.harness import runtime
from kingfisher.layout import HARNESS, RUNLOG

MODEL_CALL = "model_call"


@dataclass(frozen=True)
class Usage:
    """What a session's model calls cost, read back from its log."""

    calls: int
    input_tokens: int
    output_tokens: int
    cache_read: int

    @property
    def cached_share(self) -> float | None:
        """Fraction of input tokens served from cache, or None if none were sent."""
        return self.cache_read / self.input_tokens if self.input_tokens else None


def read_usage(path: Path) -> Usage:
    """Total the model calls in one session's log. Absent or unreadable, zero."""
    if not path.is_file():
        return Usage(calls=0, input_tokens=0, output_tokens=0, cache_read=0)

    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:  # a torn final line; the rest still counts
            continue
        if record.get("event") == MODEL_CALL:
            records.append(record)

    return Usage(
        calls=len(records),
        input_tokens=sum(r.get("input_tokens", 0) for r in records),
        output_tokens=sum(r.get("output_tokens", 0) for r in records),
        cache_read=sum(r.get("cache_read", 0) for r in records),
    )


def log_path(session_dir: Path) -> Path:
    """One log per session, inside the session.

    It was `<state_dir>/runs/<id>.jsonl`, where nothing deleted it when the
    session went: a workspace kept one per session that had ever existed. Inside,
    `reap` takes it with the rest and `session_bytes` counts what it costs.

    Under `.harness` rather than beside `/runs`, which is per-turn scratch the
    agent addresses -- and what a turn spent is not a turn's to edit.
    """
    return Path(session_dir) / HARNESS / RUNLOG


class JsonlRunLogger(BaseCallbackHandler):
    """Writes one JSON object per line for each model call and tool call."""

    def __init__(self, path: Path, *, model: str, endpoint: str, session_id: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._model = model
        self._endpoint = endpoint
        self._session_id = session_id

    def _write(self, event: str, **fields: Any) -> None:
        record = {
            "ts": time.time(),
            "session_id": self._session_id,
            "event": event,
            "model": self._model,
            "endpoint": self._endpoint,
            **fields,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    # -- lifecycle ---------------------------------------------------------

    def run_start(self, task: str, run_dir: str) -> None:
        self._write("run_start", run_dir=run_dir, task=task)

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
