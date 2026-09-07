"""What a run produces, in kingfisher's own vocabulary.

`RunEvent` is deliberately not a LangGraph stream chunk. Those shapes are not a
published protocol the way `BaseCheckpointSaver` is, so passing them through would
make a LangGraph change a kingfisher breaking change -- and would put foreign
vocabulary in the domain. Translation happens in the adapter.
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


#: Why a turn stopped, and the whole of it.
#:
#: Named after the Messages API's `stop_reason` rather than invented, so a caller
#: who has used one knows what to do with the other. The values are kingfisher's
#: own because the bounds are: a turn here runs out of *seconds* or *steps*, never
#: tokens, so `max_tokens` would be a familiar word for a thing that cannot
#: happen.
#:
#: A tuple rather than a `Literal`: this grows, and a consumer meeting a reason it
#: does not know should treat the turn as ended rather than fail.
STOP_REASONS: tuple[str, ...] = (
    # The turn finished because the agent was done.
    "end_turn",
    # `KINGFISHER_TURN_TIMEOUT_S`, checked between stream chunks.
    "max_duration",
    # `KINGFISHER_RECURSION_LIMIT`, enforced inside langgraph's own loop.
    "max_steps",
)


@dataclass(frozen=True)
class RunResult:
    session_id: str
    #: Sequential within the session — `t001`, `t002`. One turn is one request.
    turn_id: str
    answer: str
    #: Where the turn's files are, as the agent addresses them -- `/runs/t001`.
    #: Machine-independent, so this is the one a caller somewhere else can use, and
    #: it pairs with `artifacts`, which is relative to the same root.
    virtual_dir: str = ""
    #: Host paths, and the two fields here that must not leave the machine. They
    #: name a directory on the server's disk, which a remote caller cannot read and
    #: should not be told about. They are here because a *local* caller is on the
    #: host: the driver prints `run_dir` to say where your files landed.
    #:
    #: Worth knowing before reaching for `json.dumps`: it raises on these rather
    #: than serialising them, which is deliberate. The fix is to send `virtual_dir`
    #: and `artifacts`, not to stringify these.
    run_dir: Path = Path()
    log_path: Path = Path()
    #: Everything under `/derived` and `/memory` at the end of this turn, as paths
    #: relative to the session root. What is *present*, not what changed: `execute`
    #: writes without any file tool seeing it, so the only sound view is the
    #: filesystem's, and a caller persisting incrementally diffs against the
    #: previous turn's manifest -- which also tells it what was deleted.
    artifacts: tuple[str, ...] = ()
    #: Why this turn stopped. `end_turn` is the ordinary case; anything else means
    #: the answer is what had been reached when a bound was hit, and `artifacts`
    #: still lists what was written -- discarding either would hide work rather
    #: than undo it.
    #:
    #: An enumerated field rather than a flag, which is the shape the Messages API
    #: and every provider like it settled on: a flag cannot say *which* bound was
    #: hit, and it grows without another field being added.
    stop_reason: str = "end_turn"


#: How much of one tool argument to show. `write_file` takes an entire file as an
#: argument; unbounded, a single call would fill the terminal.
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


#: Every kind a run can emit, and the whole of it.
#:
#: A tuple rather than prose, because this is the closest thing to a wire contract
#: the package has, and it is checked against what the package actually emits.
#:
#: A consumer switching on `kind` should ignore one it does not recognise rather
#: than fail: this tuple grows.
KINDS: tuple[str, ...] = (
    # Warnings, emitted before the model is reached and only when they apply. Each
    # says something that would otherwise be discovered too late: a path that could
    # not be hardened, a grant that means less than the workspace holds, a grant
    # this agent cannot hold itself, a delegate that meant to run elsewhere and did
    # not, and durable data that was overwritten.
    "protect_failed",
    "withheld",
    "delegate_only",
    "indistinct",
    "data_placed",
    # The run itself.
    "run_start",
    "model_call",
    "tool_result",
    "token",
    # Terminal. `cut_short` precedes `finished` rather than replacing it.
    "cut_short",
    "finished",
)


@dataclass(frozen=True)
class RunEvent:
    """A normalised step in a run."""

    kind: str
    text: str = ""
    tool: str | None = None
    tools: tuple[str, ...] = ()
    #: Index-aligned with `tools`. Two tuples rather than one tuple of pairs
    #: because `tools` is the older, already-published shape.
    args: tuple[Mapping[str, Any], ...] = ()
    usage: Mapping[str, int] = field(default_factory=dict)
    #: Which stream a `token` belongs to: `answer`, or `reasoning` for a provider
    #: that separates the two. Nothing emits `reasoning` yet — the field exists so
    #: that when one does, it is not a new event kind.
    channel: str = "answer"
    #: Which delegate produced this, or `None` for the agent the caller asked.
    #:
    #: The stream reports the graph it was started on, and a delegate is a separate
    #: graph run inside a tool call -- so the caller's prose and a delegate's
    #: arrive on the same channel and the *type* cannot tell them apart. Both are
    #: `AIMessageChunk`. This is what tells them apart.
    agent: str | None = None
    result: RunResult | None = None

    def __post_init__(self) -> None:
        # Parallel arrays are only safe if nothing can construct them out of step.
        # This is the one place that can be enforced.
        if self.args and len(self.args) != len(self.tools):
            msg = f"args ({len(self.args)}) must be parallel to tools ({len(self.tools)})"
            raise ValueError(msg)

    def _tag(self, kind: str) -> str:
        """The bracketed tag, naming the delegate when one produced this."""
        return f"{kind}:{self.agent}" if self.agent else kind

    def _line(self, limit: int = 800) -> str:
        """One-line rendering. `text` keeps full fidelity for consumers."""
        flat = " ".join(self.text.split())
        return flat if len(flat) <= limit else flat[: limit - 1] + "…"

    # A flat dispatch, one branch per kind: fewer returns would mean more nesting,
    # not less branching.
    def __str__(self) -> str:
        if self.kind == "token":
            # A fragment, not a line. A tag would assert a boundary that is not
            # there -- chunks split mid-word -- and `_line` would flatten the
            # markdown the model is in the middle of writing. Which is also why a
            # delegate's tokens are not tagged: there is nowhere in the middle of a
            # word to put the tag.
            return self.text
        if self.kind == "model_call":
            pairs = zip(self.tools, self.args or ({},) * len(self.tools), strict=True)
            rendered = ", ".join(_render_call(name, args) for name, args in pairs)
            calls = f"→ {rendered}" if rendered else ""
            cached = self.usage.get("cache_read", 0)
            return (
                f"[{self._tag('model')}] {calls}  "
                f"(in={self.usage.get('input_tokens', 0)} cached={cached})"
            )
        if self.kind == "tool_result":
            return f"[{self._tag('tool ')}] {self.tool}: {self._line()}"
        if self.kind == "run_start":
            return f"[start] {self.text}"
        if self.kind == "finished":
            return "[done ]"
        return f"[{self.kind}] {self.text}"
