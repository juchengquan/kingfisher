"""Anticorruption layer between kingfisher's domain and the agent runtime.

Everything that knows LangChain's and LangGraph's shapes lives here: the
message payload, the stream-chunk structure, and where usage and tool calls
hide on a message. The domain and the orchestration above it speak only
`Request`, `RunEvent` and `RunResult`.

This exists because the knowledge was previously duplicated — `run.py` and
`runlog.py` each carried their own copy of

    usage.get("input_token_details")

kept in sync by nobody. One move of that field upstream would have broken two
modules and been caught by one test.

Note what is deliberately *not* wrapped: `BaseChatModel`, `BaseCheckpointSaver`
and `BackendProtocol` are stable published protocols, and swapping sqlite for
postgres is a one-line factory change precisely because they pass through
unwrapped. An ACL earns its place where a foreign *shape* enters our
vocabulary, not wherever a foreign name appears.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from kingfisher.domain.result import RunEvent

#: How much of a tool result or message to keep on an event.
PREVIEW = 300

#: The stream modes we ask for. `updates` drives progress events; `values`
#: carries the full state, whose last emission holds the final answer.
STREAM_MODES = ["updates", "values"]


def user_payload(text: str) -> dict[str, Any]:
    """The graph's input shape for a single user turn."""
    return {"messages": [{"role": "user", "content": text}]}


def usage_of(message: Any) -> dict[str, int]:
    """Token usage from a message, in kingfisher's flat vocabulary.

    The single place that knows where LangChain puts these numbers.
    """
    usage = getattr(message, "usage_metadata", None) or {}
    details = usage.get("input_token_details") or {}
    return {
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cache_read": details.get("cache_read", 0),
        "cache_creation": details.get("cache_creation", 0),
    }


def tool_names(message: Any) -> tuple[str, ...]:
    """Names of the tools a message asks for, or an empty tuple."""
    return tuple(tc["name"] for tc in (getattr(message, "tool_calls", None) or []))


def _event_for(message: Any) -> RunEvent | None:
    if isinstance(message, ToolMessage):
        return RunEvent(
            kind="tool_result",
            tool=getattr(message, "name", None),
            text=str(message.content)[:PREVIEW],
        )
    if isinstance(message, AIMessage):
        tools = tool_names(message)
        if tools:
            return RunEvent(kind="model_call", tools=tools, usage=usage_of(message))
        text = str(message.content).strip()
        if text:
            return RunEvent(kind="message", text=text[:PREVIEW], usage=usage_of(message))
    return None


def messages_in(update: Any) -> list[Any]:
    """Messages carried by a node update, tolerating shapes that carry none."""
    if not isinstance(update, Mapping):
        return []
    messages = update.get("messages")
    if messages is None:
        return []
    return list(messages) if isinstance(messages, (list, tuple)) else [messages]


def events_in(chunk: Any) -> Iterator[RunEvent]:
    """Translate one `updates` chunk into domain events."""
    for update in (chunk or {}).values():
        for message in messages_in(update):
            if (event := _event_for(message)) is not None:
                yield event


def final_text(values_chunk: Any) -> str | None:
    """The assistant's last message from a `values` chunk, if it has one."""
    messages = messages_in(values_chunk)
    if not messages:
        return None
    return getattr(messages[-1], "text", None) or ""
