"""What a run produces, in kingfisher's own vocabulary."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

# Some OpenAI-compatible gateways inline reasoning in the content (MiniMax returns
# "<think>…</think>\n\n42"). Stripped on both API styles, so the answer holds on either.
_THINK = re.compile(r"<think\b[^>]*>.*?</think>", re.DOTALL | re.IGNORECASE)


class ArtifactError(ValueError):
    """A name that is not something a turn produced, or that is not there."""


def normalize_answer(text: str) -> str:
    """Remove inlined reasoning blocks from a final answer."""
    return _THINK.sub("", text or "").strip()


#: The agent was done. Named apart because it is the one reason asked *about*, and
#: `RunResult.completed` is the one place allowed to ask.
END_TURN = "end_turn"

#: Stopped at an approval gate, waiting for an answer. Unlike a bound it is resumable:
#: a `Resume` answering `RunResult.pending` continues the same graph.
AWAITING = "awaiting_decision"

#: Why a turn stopped, and the whole of it.
STOP_REASONS: tuple[str, ...] = (
    END_TURN,
    # `KINGFISHER_TURN_TIMEOUT_S`, checked between stream chunks.
    "max_duration",
    # `KINGFISHER_RECURSION_LIMIT`, enforced inside langgraph's own loop.
    "max_steps",
    # The agent's last reply stopped at its model's `max_tokens`, so the answer is cut off.
    "max_tokens",
    AWAITING,
)

#: What a caller may answer a gated call with. deepagents' `edit` is left out: it lets
#: the caller *author* a call rather than judge one, which admission prevents everywhere
#: else, and adding it later breaks nobody where removing it would.
DECISIONS: tuple[str, ...] = ("approve", "reject", "respond")


@dataclass(frozen=True)
class PendingDecision:
    """One gated call a turn stopped on, and what may be answered about it."""

    #: The tool call's own id, which a `Resume` answers by. Not a position: a turn may
    #: have two gates, and the answer crosses a process and maybe a restart.
    call_id: str
    tool: str
    args: Mapping[str, Any] = field(default_factory=dict)
    #: Which subagent proposed it, or `None` for the caller's agent; the caller answers either way.
    agent: str | None = None
    #: What this call will accept, as the middleware declared it: a caller guessing from
    #: `DECISIONS` would offer a choice the gate may refuse.
    decisions: tuple[str, ...] = ()


#: Who may reach a recorded result, as JSON holds it: names any one of which will
#: do, a nested tuple being names that must all be held, and `("*",)` for everyone.
Listed = tuple[str | tuple[str, ...], ...]

#: A result recorded by the agent the caller asked, and one recorded inside a
#: subagent it consulted.
MAIN = "main"
WORKING = "working"


@dataclass(frozen=True)
class LedgerEntry:
    """One tool result a turn recorded, with the fields of its row in the ledger."""

    #: `A-` and the session's running number, never reused within a session.
    id: str
    turn: str
    #: The tool call's own id.
    call: str
    #: The parent's `task` call id for a `working` entry, and `None` for a `main` one.
    run: str | None
    #: `main` or `working`.
    role: str
    tool: str
    #: Each argument as the model wrote it, `name=value`, a long value cut.
    args: str
    #: The audience the result was produced under, or `None` with no `source_ids.yaml`
    #: in force. Kingfisher acts on none of it: who may read a session is the caller's to decide.
    access: Listed | None
    #: The result's length in bytes, as the tool returned it.
    size: int
    #: Where the result is kept in the session, or `None` where the backend refused
    #: the file; the row is kept either way.
    path: str | None
    #: The tool, its arguments and the first line of its result. No model writes it.
    summary: str


@dataclass(frozen=True)
class RunResult:
    session_id: str
    #: A caller's own id where it supplied one, so a service can tie the run to its
    #: request; otherwise one made here. Not a sequence: nothing compares two.
    turn_id: str
    answer: str
    #: Names, relative to the session, of everything under `/outputs` and `/memory` at the
    #: end of this turn; `Session.artifact` fetches one. What is *present*, not what
    #: changed: `execute` writes unseen by any file tool, so a caller persisting
    #: incrementally diffs against the previous turn's list, which also shows deletions.
    artifacts: tuple[str, ...] = ()
    #: Why this turn stopped. Past a bound, the answer is what had been reached and
    #: `artifacts` still lists what was written: discarding either would hide work.
    stop_reason: str = END_TURN
    #: Why the session `run(delete_session=True)` was told to delete is still there,
    #: or `None`. A turn stopped at a bound keeps its session on purpose, not as a failure.
    deletion_failure: str | None = None
    #: What this turn stopped to ask: non-empty exactly when `stop_reason` is
    #: `awaiting_decision`, and then the session is kept to resume into, even under
    #: `delete_session=True`.
    pending: tuple[PendingDecision, ...] = ()
    #: The tools an unanswered turn was waiting on when this turn superseded it: a human
    #: decision dropped quietly would leave no other trace.
    discarded: tuple[str, ...] = ()
    #: What this turn's tools returned, one entry each in numbered order, a subagent's
    #: calls included. Not the built-in file tools, whose results are files already, nor
    #: a failed call. Empty for a graph the deployment supplied, which kingfisher cannot hook.
    ledger: tuple[LedgerEntry, ...] = ()

    @property
    def completed(self) -> bool:
        """Whether the agent finished, as against a bound cutting the turn off.

        Here rather than at each surface that asks: they share no code path, and two
        copies of the comparison would come apart over the next stop reason.
        """
        return self.stop_reason == END_TURN


#: How much of one tool argument to show: `write_file`'s is a whole file.
ARG_PREVIEW = 60


def _flatten(value: Any, limit: int) -> str:
    """One value as a single bounded line."""
    flat = " ".join(str(value).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _render_call(name: str, args: Mapping[str, Any]) -> str:
    """One tool call: `write_file(file_path=/inputs/x.csv, content=id,name…)`."""
    if not args:
        return name
    inner = ", ".join(f"{key}={_flatten(value, ARG_PREVIEW)}" for key, value in args.items())
    return f"{name}({inner})"


#: Every kind a run can emit, checked against what the package emits. A consumer
#: switching on `kind` should ignore one it does not know rather than fail: this grows.
KINDS: tuple[str, ...] = (
    # Warnings, before the model is reached and only when they apply: a path not
    # hardened, a grant meaning less than the workspace holds, a grant this agent cannot
    # hold, a subagent that meant to run elsewhere and did not, a caller's input overwritten.
    "protect_failed",
    "withheld",
    "subagent_only",
    "indistinct",
    "inputs_placed",
    # The run itself.
    "run_start",
    "model_call",
    "tool_result",
    "token",
    # An approval gate. `decision_needed` precedes `finished`; `decision_discarded` says
    # an *earlier* turn's gate was superseded, so it arrives at the start of this turn.
    "decision_needed",
    "decision_discarded",
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
    #: Index-aligned with `tools`, kept apart because `tools` is already a published shape.
    args: tuple[Mapping[str, Any], ...] = ()
    usage: Mapping[str, int] = field(default_factory=dict)
    #: Which stream a `token` belongs to: `answer`, or `reasoning` for a provider that
    #: separates them. Nothing emits `reasoning` yet; this keeps it from being a new kind.
    channel: str = "answer"
    #: Which subagent produced this, or `None` for the agent the caller asked: a
    #: subagent's prose arrives on the same stream as the caller's, as the same type.
    agent: str | None = None
    result: RunResult | None = None
    #: On a `model_call`: the reply stopped at the model's output cap. The turn goes on,
    #: the call it was cut inside unrun, so this is the only report of one it got past.
    cut_off: bool = False

    def __post_init__(self) -> None:
        # The one place parallel arrays can be kept from being built out of step.
        if self.args and len(self.args) != len(self.tools):
            msg = f"args ({len(self.args)}) must be parallel to tools ({len(self.tools)})"
            raise ValueError(msg)

    def _tag(self, kind: str) -> str:
        """The bracketed tag, naming the subagent when one produced this."""
        return f"{kind}:{self.agent}" if self.agent else kind

    def _line(self, limit: int = 800) -> str:
        """One-line rendering. `text` keeps full fidelity for consumers."""
        flat = " ".join(self.text.split())
        return flat if len(flat) <= limit else flat[: limit - 1] + "…"

    # One branch per kind: fewer returns would mean more nesting, not less branching.
    def __str__(self) -> str:
        if self.kind == "token":
            # A fragment split mid-word, so untagged even for a subagent, and not passed
            # through `_line`, which would flatten the markdown being written.
            return self.text
        if self.kind == "model_call":
            pairs = zip(self.tools, self.args or ({},) * len(self.tools), strict=True)
            rendered = ", ".join(_render_call(name, args) for name, args in pairs)
            calls = f"→ {rendered}" if rendered else ""
            cached = self.usage.get("cache_read", 0)
            cut = "  (cut off at the output limit)" if self.cut_off else ""
            return (
                f"[{self._tag('model')}] {calls}  "
                f"(in={self.usage.get('input_tokens', 0)} cached={cached}){cut}"
            )
        if self.kind == "tool_result":
            return f"[{self._tag('tool ')}] {self.tool}: {self._line()}"
        if self.kind == "run_start":
            return f"[start] {self.text}"
        if self.kind == "finished":
            return "[done ]"
        return f"[{self.kind}] {self.text}"
