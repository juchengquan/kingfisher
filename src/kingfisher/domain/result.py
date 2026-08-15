"""What a run produces, in kingfisher's own vocabulary.

`RunEvent` is deliberately not a LangGraph stream chunk. Those shapes are not
a published protocol the way `BaseCheckpointSaver` is, so passing them through
would make a LangGraph change a kingfisher breaking change — and would put
foreign vocabulary in the domain. Translation happens in the adapter.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

# Some OpenAI-compatible gateways inline reasoning in the response content
# (MiniMax returns "<think>…</think>\n\n42"). Stripping is applied on both API
# styles, not just the one that currently misbehaves — supporting both equally
# means the deliverable contract has to hold identically on both.
_THINK = re.compile(r"<think\b[^>]*>.*?</think>", re.DOTALL | re.IGNORECASE)


def normalize_answer(text: str) -> str:
    """Remove inlined reasoning blocks from a final answer."""
    return _THINK.sub("", text or "").strip()


@dataclass(frozen=True)
class RunResult:
    session_id: str
    #: Sequential within the session — `t001`, `t002`. One turn is one request.
    turn_id: str
    answer: str
    run_dir: Path
    log_path: Path
    swept: tuple[str, ...]
    commit: str | None


@dataclass(frozen=True)
class RunEvent:
    """A normalised step in a run.

    kinds: `run_start`, `swept`, `model_call`, `tool_result`, `message`,
    `finished`.
    """

    kind: str
    text: str = ""
    tool: str | None = None
    tools: tuple[str, ...] = ()
    usage: Mapping[str, int] = field(default_factory=dict)
    result: RunResult | None = None

    def _line(self, limit: int = 150) -> str:
        """One-line rendering. `text` keeps full fidelity for consumers."""
        flat = " ".join(self.text.split())
        return flat if len(flat) <= limit else flat[: limit - 1] + "…"

    # A flat dispatch, one branch per kind: fewer returns would mean more
    # nesting, not less branching.
    def __str__(self) -> str:  # noqa: PLR0911
        if self.kind == "model_call":
            calls = f"→ {', '.join(self.tools)}" if self.tools else ""
            cached = self.usage.get("cache_read", 0)
            return f"[model] {calls}  (in={self.usage.get('input_tokens', 0)} cached={cached})"
        if self.kind == "tool_result":
            return f"[tool ] {self.tool}: {self._line()}"
        if self.kind == "message":
            return f"[say  ] {self._line()}"
        if self.kind == "swept":
            return f"[sweep] removed {self.text}"
        if self.kind == "run_start":
            return f"[start] {self.text}"
        if self.kind == "finished":
            return "[done ]"
        return f"[{self.kind}] {self.text}"
