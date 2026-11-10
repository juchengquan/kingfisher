"""What a turn did, as events handed to the deployment's `RunEvents`."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langgraph.errors import GraphInterrupt

from kingfisher.core.agent.middlewares.results import answer_in
from kingfisher.core.definitions.subagents import TASK_TOOL
from kingfisher.core.interfaces import RunEvents
from kingfisher.core.logged import INFO, handed, logged, raised

from . import runtime, stream_chunks

MODEL_CALL = "model_call"

#: Where langgraph puts, in a callback's metadata, the node it ran in.
CHECKPOINT_NS = "langgraph_checkpoint_ns"


class LoggedRunEvents(RunEvents):
    """`RunEvents` into Python's logging, where a deployment that wired nothing finds them,
    as `logged` writes them."""

    def record(self, event: Mapping[str, object]) -> None:
        logged(event)


class RunLogger(BaseCallbackHandler):
    """Turns one turn's model and tool callbacks into events for a `RunEvents`.

    An event carries sizes, names, ids and an error's type, never what was asked, what a
    tool was handed or answered, or an error's message: a deployment's logs reach people
    the caller never sent any of it to.
    """

    def __init__(  # noqa: PLR0913 -- the stamp's five fields, and where each model runs
        self,
        sink: Any,
        *,
        model: str,
        endpoint: str,
        session_id: str,
        turn_id: str,
        turn_agent: str | None,
        endpoints: Mapping[str, str] | None = None,
    ) -> None:
        self._sink = sink
        self._stamp = {
            "session_id": session_id,
            "turn_id": turn_id,
            # Not folded into `agent`, which is `None` for the turn's own agent so that a
            # subagent's calls stand out: this names that agent, on every event.
            "turn_agent": turn_agent,
            "model": model,
            "endpoint": endpoint,
        }
        #: Each catalogue model's endpoint, by name.
        self._endpoints = dict(endpoints or {})
        #: What each model call under way is reported with, by its run id: an end
        #: callback carries no metadata.
        self._calls: dict[Any, dict[str, Any]] = {}
        #: The same for each tool call under way.
        self._tools: dict[Any, dict[str, Any]] = {}
        self._delegations = stream_chunks.Delegations()

    def _write(self, event: str, **fields: Any) -> None:
        # Through `handed`, never the sink itself: a turn outlives a failing sink, and
        # `run_start` and `run_end` are called outside langchain's guard around callbacks.
        handed(
            self._sink,
            {
                "ts": time.time(),
                "event": event,
                "level": INFO,
                "message": event,
                **self._stamp,
                **fields,
            },
        )

    def _where(self, namespace: list[str], metadata: Mapping[str, Any]) -> dict[str, Any]:
        """Which agent a callback came from, and inside which delegations, as the stream
        says it."""
        # The stream names no agent for the caller's, whatever name its graph carries.
        named = metadata.get("lc_agent_name") if namespace else None
        return {
            "agent": str(named) if named else None,
            "delegations": [
                *(str(one) for one in metadata.get(stream_chunks.DISPATCH) or ()),
                *self._delegations.within(namespace),
            ],
        }

    def run_start(self, task: str, *, decisions: int | None = None) -> None:
        """`decisions` is how many a resume answers, which asks no task."""
        self._write("run_start", task_chars=len(task), decisions=decisions)

    def run_end(self, *, ok: bool, answer_chars: int, stop_reason: str | None) -> None:
        self._write("run_end", ok=ok, answer_chars=answer_chars, stop_reason=stop_reason)

    def on_chat_model_start(
        self,
        *_: Any,
        run_id: Any = None,
        metadata: Mapping[str, Any] | None = None,
        **__: Any,
    ) -> None:
        found = metadata or {}
        held = self._where(_segments(found)[:-1], found)
        # Read off the call, not the turn: a subagent may run a model of its own. A name
        # the catalogue does not hold keeps the turn's stamp rather than half of one.
        named = found.get("ls_model_name")
        if isinstance(named, str) and named in self._endpoints:
            held = {"model": named, "endpoint": self._endpoints[named], **held}
        self._calls[run_id] = held

    def on_llm_end(self, response: Any, *, run_id: Any = None, **_: Any) -> None:
        message = _first_message(response)
        usage = runtime.usage_of(message)
        names, _args, ids = runtime.tool_calls(message)
        self._write(
            MODEL_CALL,
            **self._calls.pop(run_id, {}),
            **usage,
            usage_present=bool(getattr(message, "usage_metadata", None)),
            tool_calls=list(names),
            calls=list(ids),
            cut_off=runtime.cut_off(message),
        )

    def on_tool_start(
        self,
        serialized: dict[str, Any] | None,
        input_str: str,
        *,
        run_id: Any = None,
        metadata: Mapping[str, Any] | None = None,
        tool_call_id: str | None = None,
        **_: Any,
    ) -> None:
        name = (serialized or {}).get("name", "?")
        found = dict(metadata or {})
        *namespace, node = _segments(found)
        stamp = found.get(stream_chunks.DISPATCH) or ()
        if name == TASK_TOOL and tool_call_id and stamp and stamp[-1] == tool_call_id:
            # A program's dispatch, filed by the stamp on every run inside it; the call
            # itself is the program's. Entered too, a lone one would name itself twice.
            del found[stream_chunks.DISPATCH]
        elif name == TASK_TOOL and tool_call_id:
            # Before anything inside it runs, whose events are filed under it from here.
            self._delegations.entered(node.partition(":")[2], tool_call_id)
        held = {"tool": name, "call": tool_call_id or None, **self._where(namespace, found)}
        self._tools[run_id] = held
        self._write("tool_start", **held, input_chars=len(str(input_str)))

    def on_tool_end(self, output: Any, *, run_id: Any = None, **_: Any) -> None:
        held = self._tools.pop(run_id, {})
        self._write("tool_end", **held, output_chars=_answer_chars(output, held.get("call")))

    def on_tool_error(self, error: BaseException, *, run_id: Any = None, **_: Any) -> None:
        held = self._tools.pop(run_id, {})
        # A subagent's pause reaches its `task` call as an exception, and fails nothing:
        # the call is left started, and the turn that resumes it starts it again.
        if isinstance(error, GraphInterrupt):
            return
        kind, at = raised(error)
        self._write("tool_error", **held, error_type=kind, error_at=at)

    def on_llm_error(self, error: BaseException, *, run_id: Any = None, **_: Any) -> None:
        kind, at = raised(error)
        self._write(
            "model_error",
            **self._calls.pop(run_id, {}),
            error_type=kind,
            error_at=at,
            status_code=_status_code(error),
        )


def _segments(metadata: Mapping[str, Any]) -> list[str]:
    """The node a callback ran in, after each graph it passed through: the segments
    before the last are the namespace the stream gives the same work."""
    return str(metadata.get(CHECKPOINT_NS) or "").split("|")


def _answer_chars(output: Any, call: str | None) -> int | None:
    """How long a tool's answer is as the model reads it, attachments left out as the
    recorder leaves them; `None` where it holds no message for the call.

    Not `str(output)`: a `ToolMessage` stringifies to its repr.
    """
    if isinstance(output, str):
        return len(output)
    answer = answer_in(output, call)
    return None if answer is None else len(answer.text)


def _status_code(error: BaseException) -> int | None:
    """The provider's HTTP status: on the error, as the SDKs' errors carry it, or on its
    response, as `httpx`'s does."""
    for status in (
        getattr(error, "status_code", None),
        getattr(getattr(error, "response", None), "status_code", None),
    ):
        if isinstance(status, int):
            return status
    return None


def _first_message(response: Any) -> Any:
    """Pull the AIMessage out of an LLMResult, tolerating shape changes."""
    try:
        return response.generations[0][0].message
    except (AttributeError, IndexError, TypeError):
        return None
