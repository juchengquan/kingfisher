"""Local-only structured run log.

One append-only JSONL file per session under `<state_dir>/runs/`, which is
`<workspace>/.kingfisher/runs/` unless configured elsewhere. Nothing leaves
the machine (Q13), and per-step token usage is recorded (Q18) because it is the
only way the cost-control driver becomes measurable rather than aspirational.

`cache_read` is the signal worth watching: in an agent that re-sends its whole
prefix on every step, a run of zeros means prompt caching is not working. The
model and API style are logged alongside it, because on a gateway that does not
cache server-side, zero is correct rather than broken — without those fields the
alarm cannot tell the two cases apart.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from kingfisher.adapters import runtime


def log_path(state_dir: Path, session_id: str) -> Path:
    """One log per session, under the configured state directory."""
    return Path(state_dir) / "runs" / f"{session_id}.jsonl"


class JsonlRunLogger(BaseCallbackHandler):
    """Writes one JSON object per line for each model call and tool call."""

    def __init__(self, path: Path, *, model: str, api_style: str, session_id: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._model = model
        self._api_style = api_style
        self._session_id = session_id

    def _write(self, event: str, **fields: Any) -> None:
        record = {
            "ts": time.time(),
            "session_id": self._session_id,
            "event": event,
            "model": self._model,
            "api_style": self._api_style,
            **fields,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    # -- lifecycle ---------------------------------------------------------

    def run_start(self, task: str, run_dir: str) -> None:
        self._write("run_start", run_dir=run_dir, task=task)

    def run_end(self, *, ok: bool, answer_chars: int) -> None:
        self._write("run_end", ok=ok, answer_chars=answer_chars)

    def swept(self, removed: tuple[str, ...], kept: int) -> None:
        if removed:
            self._write("swept", removed=list(removed), kept=kept)

    # -- callbacks ---------------------------------------------------------

    def on_llm_end(self, response: Any, **_: Any) -> None:
        message = _first_message(response)
        usage = runtime.usage_of(message)
        self._write(
            "model_call",
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
