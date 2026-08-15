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

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langgraph.types import StreamMode

from kingfisher.domain.result import RunEvent

#: How much of a tool result or message to keep on an event.
PREVIEW = 300

#: A `messages` chunk is a (message, metadata) pair.
TOKEN_CHUNK_PARTS = 2

#: The stream modes we ask for. `updates` drives progress events; `values`
#: carries the full state, whose last emission holds the final answer;
#: `messages` carries the model's output as it is generated.
#:
#: `messages` is not a display choice. LangGraph installs a streaming callback
#: handler to serve it, which makes `_should_stream` true and turns every model
#: call into SSE. That is the point of asking for it: a non-streaming request
#: has to complete the whole generation inside `timeout_s`, while SSE resets
#: the read clock on every chunk. Streaming is how a long turn survives, not
#: only how it is watched -- so it is not a flag, and `run()` gets it too.
STREAM_MODES: list[StreamMode] = ["updates", "values", "messages"]


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


def tool_calls(message: Any) -> tuple[tuple[str, ...], tuple[Mapping[str, Any], ...]]:
    """Names and arguments of the tools a message asks for, index-aligned.

    Both tuples are built from one list in one expression, so they cannot be
    made to disagree about which arguments belong to which call.
    """
    calls = getattr(message, "tool_calls", None) or []
    return (
        tuple(call["name"] for call in calls),
        tuple(call.get("args") or {} for call in calls),
    )


def tool_names(message: Any) -> tuple[str, ...]:
    """Names of the tools a message asks for, or an empty tuple."""
    return tool_calls(message)[0]


def _event_for(message: Any) -> RunEvent | None:
    if isinstance(message, ToolMessage):
        return RunEvent(
            kind="tool_result",
            tool=getattr(message, "name", None),
            text=message.text[:PREVIEW],
        )
    if isinstance(message, AIMessage):
        # One event per completed turn, whether or not it called a tool. The
        # prose is not carried: it has already arrived as tokens, and a
        # truncated second copy here would be the same text twice.
        #
        # Arguments come along because the run log has been recording them all
        # along while the terminal showed only the tool's name -- so you could
        # see that `write_file` ran and not what it wrote where.
        names, args = tool_calls(message)
        # `.text` rather than `str(content)`: the Responses API returns a list
        # of content blocks, and `str()` would render their repr. A message
        # with no text, no tools and no usage is not a turn anyone made -- it
        # is a state shuffle, and announcing it would be noise.
        if not (names or message.text.strip() or getattr(message, "usage_metadata", None)):
            return None
        return RunEvent(kind="model_call", tools=names, args=args, usage=usage_of(message))
    return None


def messages_in(update: Any) -> list[Any]:
    """Messages carried by a node update, tolerating shapes that carry none."""
    if not isinstance(update, Mapping):
        return []
    messages = update.get("messages")
    if messages is None:
        return []
    return list(messages) if isinstance(messages, (list, tuple)) else [messages]


def _token_event(chunk: Any) -> RunEvent | None:
    """One `messages` chunk into a token event, or nothing.

    Tool results travel this stream too, and untruncated -- a `read_file` of a
    large CSV arrives here in full. They are already carried, bounded to
    `PREVIEW`, as `tool_result`, so letting them through would both duplicate
    them and undo the bound. Chunks holding only usage, or only the fragments
    of a tool call's arguments, carry no text and are likewise nothing to show.
    """
    if not isinstance(chunk, tuple) or len(chunk) != TOKEN_CHUNK_PARTS:
        return None
    message, _metadata = chunk
    # `AIMessageChunk` and not `AIMessage`: the former is a subclass, and only
    # it appears on this stream. Testing for the base class would admit the
    # tool results this exists to exclude.
    if not isinstance(message, AIMessageChunk):
        return None
    text = message.text
    return RunEvent(kind="token", text=text) if text else None


def events_in(mode: str, chunk: Any) -> Iterator[RunEvent]:
    """Translate one stream chunk into domain events.

    Which modes exist, and what each carries, is decided here rather than by
    the orchestration above. `updates`, `values` and `messages` are LangGraph's
    vocabulary, and `app/` should no more compare against them than it should
    reach for `input_token_details` -- which is the duplication this module was
    written to end.
    """
    if mode == "messages":
        if (event := _token_event(chunk)) is not None:
            yield event
        return
    if mode != "updates":
        return
    for update in (chunk or {}).values():
        for message in messages_in(update):
            if (event := _event_for(message)) is not None:
                yield event


def answer_in(mode: str, chunk: Any) -> str | None:
    """The assistant's last message from a `values` chunk, if it has one.

    `None` for any other mode, so a caller can offer every chunk and keep the
    last answer it is given: the final emission is the authoritative one.
    """
    if mode != "values":
        return None
    messages = messages_in(chunk)
    if not messages:
        return None
    return getattr(messages[-1], "text", None) or ""
