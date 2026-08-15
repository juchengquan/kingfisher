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
from typing import Any

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
    #: Everything under `/derived` and `/memory` at the end of this turn, as
    #: paths relative to the session root. What is *present*, not what changed:
    #: `execute` writes without any file tool seeing it, so the only sound view
    #: is the filesystem's, and a caller persisting incrementally diffs against
    #: the previous turn's manifest -- which also tells it what was deleted.
    artifacts: tuple[str, ...] = ()
    #: The turn hit its wall-clock bound and stopped between steps. The answer
    #: is whatever it had reached, and `artifacts` still lists what it wrote --
    #: discarding either would hide work rather than undo it.
    cut_short: bool = False


#: How much of one tool argument to show. `write_file` takes an entire file as
#: an argument; unbounded, a single call would fill the terminal.
ARG_PREVIEW = 60


def _flatten(value: Any, limit: int) -> str:
    """One value as a single bounded line."""
    flat = " ".join(str(value).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _render_call(name: str, args: Mapping[str, Any]) -> str:
    """One tool call: `write_file(file_path=/data/x.csv, content=id,name…)`."""
    if not args:
        return name
    inner = ", ".join(f"{key}={_flatten(value, ARG_PREVIEW)}" for key, value in args.items())
    return f"{name}({inner})"


@dataclass(frozen=True)
class RunEvent:
    """A normalised step in a run.

    kinds: `run_start`, `swept`, `sweep_failed`, `model_call`, `tool_result`,
    `token`, `finished`.

    There is no `message` kind. A completed assistant turn is a `model_call`
    whatever it produced, and its prose is not carried there: prose arrives as
    `token` events while it is being generated, which is the only place it
    lives. Carrying it twice would mean rendering the same text at two
    granularities and asking every consumer to know that.
    """

    kind: str
    text: str = ""
    tool: str | None = None
    tools: tuple[str, ...] = ()
    #: Index-aligned with `tools`. Two tuples rather than one tuple of pairs
    #: because `tools` is the older, already-published shape.
    args: tuple[Mapping[str, Any], ...] = ()
    usage: Mapping[str, int] = field(default_factory=dict)
    #: Which stream a `token` belongs to: `answer`, or `reasoning` for a
    #: provider that separates the two. Nothing emits `reasoning` yet — the
    #: field exists so that when one does, it is not a new event kind.
    channel: str = "answer"
    result: RunResult | None = None

    def __post_init__(self) -> None:
        # Parallel arrays are only safe if nothing can construct them out of
        # step. This is the one place that can be enforced.
        if self.args and len(self.args) != len(self.tools):
            msg = f"args ({len(self.args)}) must be parallel to tools ({len(self.tools)})"
            raise ValueError(msg)

    def _line(self, limit: int = 800) -> str:
        """One-line rendering. `text` keeps full fidelity for consumers."""
        flat = " ".join(self.text.split())
        return flat if len(flat) <= limit else flat[: limit - 1] + "…"

    # A flat dispatch, one branch per kind: fewer returns would mean more
    # nesting, not less branching.
    def __str__(self) -> str:  # noqa: PLR0911
        if self.kind == "token":
            # A fragment, not a line. A tag would assert a boundary that is not
            # there -- chunks split mid-word -- and `_line` would flatten the
            # markdown the model is in the middle of writing.
            return self.text
        if self.kind == "model_call":
            pairs = zip(self.tools, self.args or ({},) * len(self.tools), strict=True)
            rendered = ", ".join(_render_call(name, args) for name, args in pairs)
            calls = f"→ {rendered}" if rendered else ""
            cached = self.usage.get("cache_read", 0)
            return f"[model] {calls}  (in={self.usage.get('input_tokens', 0)} cached={cached})"
        if self.kind == "tool_result":
            return f"[tool ] {self.tool}: {self._line()}"
        if self.kind == "swept":
            return f"[sweep] removed {self.text}"
        if self.kind == "run_start":
            return f"[start] {self.text}"
        if self.kind == "finished":
            return "[done ]"
        return f"[{self.kind}] {self.text}"
