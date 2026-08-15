"""Running a task — everything with a side effect lives here.

`stream()` is the primitive and `run()` drains it, so the orchestration
sequence (commit, sweep, run directory, logging, normalisation) exists exactly
once. Two entrypoints with two copies of that sequence would drift, and the
drift would be silent.

Ordering matters: commit, then sweep, then create this run's directory. The
sweep runs before the new directory exists so it is never a candidate for its
own deletion, and after the commit so the restore point covers the state the
sweep is about to change.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, ToolMessage

from kingfisher import config as config_module
from kingfisher.agent import build_agent
from kingfisher.checkpointing import build_checkpointer
from kingfisher.config import Config
from kingfisher.runlog import JsonlRunLogger, log_path
from kingfisher.workspace import (
    ensure_layout,
    allocate_turn_dir,
    pre_run_commit,
    protect_data,
    sweep,
    virtual_input_dir,
    virtual_run_dir,
)

# Some OpenAI-compatible gateways inline reasoning in the response content
# (MiniMax returns "<think>…</think>\n\n42"). Stripping is applied on both API
# styles, not just the one that currently misbehaves — supporting both equally
# means the deliverable contract has to hold identically on both.
_THINK = re.compile(r"<think\b[^>]*>.*?</think>", re.DOTALL | re.IGNORECASE)

_PREVIEW = 300


def normalize_answer(text: str) -> str:
    """Remove inlined reasoning blocks from a final answer."""
    return _THINK.sub("", text or "").strip()


@dataclass(frozen=True)
class Request:
    """One request: what the caller asks for, and nothing about wiring.

    This is the turn boundary made explicit. A stateless service receives
    exactly these four things and passes them straight through; `cfg`, `agent`
    and `checkpointer` stay keyword arguments because they describe how this
    kingfisher is configured, not what is being asked of it.

    `session_id` continues a conversation; omitted, a new one starts.
    `turn_id` should be the caller's own request id where one exists — it makes
    a retry idempotent rather than forking a second turn.
    `inputs` are files supplied with this request. They are copied into the
    turn's `input/` directory, never into `/data`: they arrive fresh each round
    and leave with the turn.
    """

    task: str
    session_id: str | None = None
    turn_id: str | None = None
    inputs: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        if not self.task or not self.task.strip():
            msg = "task must not be empty"
            raise ValueError(msg)
        # Normalise at the edge so everything downstream sees real paths.
        object.__setattr__(self, "inputs", tuple(Path(p) for p in self.inputs))

    @classmethod
    def coerce(cls, value: str | Request) -> Request:
        """Accept a bare task string so `run("do a thing")` still reads well."""
        return value if isinstance(value, cls) else cls(task=value)


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

    Normalised rather than raw LangGraph chunks: those shapes are not a stable
    published protocol the way `BaseCheckpointSaver` is, so passing them
    through would make a LangGraph change a kingfisher breaking change.

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

    def __str__(self) -> str:
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


def new_session_id() -> str:
    return uuid4().hex[:12]


def _usage_of(message: Any) -> dict[str, int]:
    usage = getattr(message, "usage_metadata", None) or {}
    details = usage.get("input_token_details") or {}
    return {
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cache_read": details.get("cache_read", 0),
        "cache_creation": details.get("cache_creation", 0),
    }


def _event_for(message: Any) -> RunEvent | None:
    if isinstance(message, ToolMessage):
        return RunEvent(
            kind="tool_result",
            tool=getattr(message, "name", None),
            text=str(message.content)[:_PREVIEW],
        )
    if isinstance(message, AIMessage):
        tools = tuple(tc["name"] for tc in (message.tool_calls or []))
        if tools:
            return RunEvent(kind="model_call", tools=tools, usage=_usage_of(message))
        text = str(message.content).strip()
        if text:
            return RunEvent(kind="message", text=text[:_PREVIEW], usage=_usage_of(message))
    return None


def _messages_in(update: Any) -> list[Any]:
    if not isinstance(update, Mapping):
        return []
    messages = update.get("messages")
    if messages is None:
        return []
    return list(messages) if isinstance(messages, (list, tuple)) else [messages]


def stream(
    request: str | Request,
    *,
    cfg: Config | None = None,
    agent: Any | None = None,
    checkpointer: Any | None = None,
) -> Iterator[RunEvent]:
    """Run one task, yielding progress as it happens.

    The terminal event has `kind == "finished"` and carries the `RunResult`.

        for event in stream("profile /data/sales.csv"):
            print(event)

        for event in stream(Request(task, session_id=sid, turn_id=req.id)):
            print(event)

    `session_id` is the LangGraph `thread_id` and the log filename, so one
    string reaches this conversation's state and its trace. `turn_id` scopes a
    single request within it: one directory per turn, so a second turn cannot
    overwrite the first one's answer.

    Pass `turn_id` when the caller has a request id of its own — it makes a
    retry idempotent and removes any need for kingfisher to guess where one
    turn ends. Omitted, the next sequential id is allocated atomically.

    `inputs` are files supplied *with this request*. They are copied into the
    turn's `input/` directory rather than into `/data`, because they are not
    project data — they arrive fresh each round and leave with the turn.
    """
    request = Request.coerce(request)
    task, inputs = request.task, request.inputs

    cfg = cfg or config_module.from_env()
    config_module.enforce_local_only_tracing()

    workspace = ensure_layout(cfg.workspace)
    protect_data(workspace)  # kernel-level guard; the deny rule covers only file tools
    checkpointer = checkpointer if checkpointer is not None else build_checkpointer(cfg)
    session_id = request.session_id or new_session_id()

    commit = pre_run_commit(workspace, f"kingfisher: pre-run {session_id}")
    swept = sweep(workspace, cfg.keep_runs, checkpointer)

    # Allocation is atomic, and a caller-supplied id wins. A service should
    # pass its own request id: only the caller knows the request boundary, and
    # deriving one here cannot be made to match it.
    turn_id, rd = allocate_turn_dir(workspace, session_id, request.turn_id)

    if inputs:
        input_dir = rd / "input"
        input_dir.mkdir(exist_ok=True)
        for source in inputs:
            shutil.copy(source, input_dir / Path(source).name)

    logger = JsonlRunLogger(
        log_path(workspace, session_id),
        model=cfg.model,
        api_style=cfg.api_style,
        session_id=session_id,
    )
    logger.swept(swept.removed, swept.kept)
    logger.run_start(task, virtual_run_dir(session_id, turn_id))

    if swept.removed:
        yield RunEvent(kind="swept", text=", ".join(swept.removed))
    yield RunEvent(kind="run_start", text=virtual_run_dir(session_id, turn_id))

    graph = agent if agent is not None else build_agent(cfg, checkpointer=checkpointer)

    # The run directory is run-scoped, so it reaches the model here rather than
    # in the system prompt — putting it in the prompt would change the cached
    # prefix on every session.
    supplied = (
        f" Files supplied with this request are in "
        f"{virtual_input_dir(session_id, turn_id)}."
        if inputs
        else ""
    )
    message = (
        f"{task}\n\n"
        f"Your run directory for this task is {virtual_run_dir(session_id, turn_id)}. "
        f"Write report.md and result.json there.{supplied}"
    )

    final_messages: list[Any] = []
    answer = ""
    ok = False
    try:
        for mode, chunk in graph.stream(
            {"messages": [{"role": "user", "content": message}]},
            config={
                "configurable": {"thread_id": session_id},
                "callbacks": [logger],
                "recursion_limit": cfg.recursion_limit,
            },
            stream_mode=["updates", "values"],
        ):
            if mode == "values":
                # Full state each step; the last one carries the final messages.
                final_messages = _messages_in(chunk) or final_messages
                continue
            for update in (chunk or {}).values():
                for msg in _messages_in(update):
                    if (event := _event_for(msg)) is not None:
                        yield event

        if final_messages:
            answer = normalize_answer(final_messages[-1].text)
        ok = True
    finally:
        logger.run_end(ok=ok, answer_chars=len(answer))

    yield RunEvent(
        kind="finished",
        text=answer,
        result=RunResult(
            session_id=session_id,
            turn_id=turn_id,
            answer=answer,
            run_dir=rd,
            log_path=log_path(workspace, session_id),
            swept=swept.removed,
            commit=commit,
        ),
    )


def run(
    request: str | Request,
    *,
    cfg: Config | None = None,
    agent: Any | None = None,
    checkpointer: Any | None = None,
) -> RunResult:
    """Run one task to completion and return where its outputs landed.

    A drain of `stream()` — there is no second orchestration path.
    """
    result: RunResult | None = None
    for event in stream(request, cfg=cfg, agent=agent, checkpointer=checkpointer):
        if event.kind == "finished":
            result = event.result

    if result is None:  # pragma: no cover -- stream always terminates with `finished`
        msg = "stream() ended without a finished event"
        raise RuntimeError(msg)
    return result
