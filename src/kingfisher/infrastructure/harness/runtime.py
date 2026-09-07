"""Anticorruption layer between kingfisher's domain and the agent runtime.

Everything that knows LangChain's and LangGraph's shapes lives here: the message
payload, the stream-chunk structure, and where usage and tool calls hide on a
message. The domain and the orchestration above it speak only `Request`, `RunEvent`
and `RunResult`.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import replace
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langgraph.errors import GraphRecursionError
from langgraph.types import StreamMode

from kingfisher.domain.result import RunEvent
from kingfisher.domain.transcript import Message, Role, ToolCall

#: What langgraph raises out of `stream` when a turn uses up `recursion_limit`.
OutOfSteps = GraphRecursionError

#: How much of a tool result or message to keep on an event.
PREVIEW = 300

#: A `messages` chunk is a (message, metadata) pair.
TOKEN_CHUNK_PARTS = 2

#: The stream modes we ask for. `updates` drives progress events; `values` carries the
#: full state, whose last emission holds the final answer; `messages` carries the
#: model's output as it is generated.
STREAM_MODES: list[StreamMode] = ["updates", "values", "messages"]


def user_payload(text: str, history: tuple[Message, ...] = ()) -> dict[str, Any]:
    """The graph's input: what was said before, then what is being asked now."""
    return {"messages": [*(_as_langchain(message) for message in history),
                         {"role": "user", "content": text}]}


def _as_langchain(message: Message) -> Any:
    """One of kingfisher's records, in the shape LangChain expects."""
    if message.role == "tool":
        return ToolMessage(
            content=message.content, tool_call_id=message.call_id, name=message.name or None
        )
    if message.role == "assistant" and message.tool_calls:
        return AIMessage(
            content=message.content,
            tool_calls=[
                {"name": call.name, "args": call.args, "id": call.id}
                for call in message.tool_calls
            ],
        )
    return {"role": message.role, "content": message.content}


def as_transcript(messages: Iterable[Any]) -> tuple[Message, ...]:
    """What the graph ended up holding, as records this package owns."""
    read: list[Message] = []
    for raw in messages:
        # Both shapes, because both occur. `_as_langchain` emits plain dicts
        # wherever one will do, and a graph may hand back what it was given
        # rather than a coerced object -- reading only one shape meant a
        # conversation that lost every message this module had just written.
        role = (
            _CANONICAL.get(str(raw.get("role", "")))
            if isinstance(raw, dict)
            else _ROLES.get(getattr(raw, "type", ""))
        )
        if role is None:
            continue
        if isinstance(raw, dict):
            read.append(Message(role=role, content=_text(raw.get("content", ""))))
            continue
        read.append(
            Message(
                role=role,
                content=_text(getattr(raw, "content", "")),
                tool_calls=tuple(
                    ToolCall(name=call["name"], args=dict(call.get("args") or {}),
                             id=str(call.get("id") or ""))
                    for call in (getattr(raw, "tool_calls", None) or ())
                ),
                call_id=str(getattr(raw, "tool_call_id", "") or ""),
                name=str(getattr(raw, "name", "") or ""),
            )
        )
    return tuple(read)


#: A role that is already kingfisher's, for a message handed back as a plain
#: dict rather than a coerced object. Spelled out rather than derived, so the
#: only way in is a role this vocabulary actually has.
_CANONICAL: dict[str, Role] = {
    "system": "system",
    "user": "user",
    "assistant": "assistant",
    "tool": "tool",
}


#: LangChain's `.type` for each role kingfisher records. `system` is here for
#: completeness and does not normally appear: the system prompt is the cached
#: prefix and is rebuilt per turn rather than stored.
_ROLES: dict[str, Role] = {
    "human": "user",
    "ai": "assistant",
    "tool": "tool",
    "system": "system",
}


def _text(content: Any) -> str:
    """A message's text, whatever shape the provider used."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    return str(content)


def usage_of(message: Any) -> dict[str, int]:
    """Token usage from a message, in kingfisher's flat vocabulary."""
    usage = getattr(message, "usage_metadata", None) or {}
    details = usage.get("input_token_details") or {}
    return {
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cache_read": details.get("cache_read", 0),
        "cache_creation": details.get("cache_creation", 0),
    }


def tool_calls(message: Any) -> tuple[tuple[str, ...], tuple[Mapping[str, Any], ...]]:
    """Names and arguments of the tools a message asks for, index-aligned."""
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


class Delegates:
    """Which delegate produced a chunk, learned from the stream as it arrives.

    Only `messages` chunks carry a name, in `lc_agent_name`. `updates` carry no
    metadata at all -- which would leave `model_call` and `tool_result` unnamed if
    the two were read independently. They are not: a delegate's first model call is
    *streamed* before the node update that reports it, so the name is always known by
    the time an event needs it. Measured, not assumed; a test drives a real two-level
    run and asserts every delegate event is named.
    """

    def __init__(self) -> None:
        self._names: dict[tuple[str, ...], str] = {}

    def name(self, namespace: Any, metadata: Any = None) -> str | None:
        """The delegate a chunk belongs to, or `None` for the main agent."""
        key = tuple(namespace or ())
        if not key:
            return None
        if isinstance(metadata, Mapping) and (found := metadata.get("lc_agent_name")):
            self._names[key] = str(found)
        return self._names.get(key)


def _token_event(chunk: Any) -> RunEvent | None:
    """One `messages` chunk into a token event, or nothing."""
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


def events_in(
    namespace: Any, mode: str, chunk: Any, delegates: Delegates | None = None
) -> Iterator[RunEvent]:
    """Translate one stream chunk into domain events."""
    named = delegates.name(namespace) if delegates is not None else None
    if mode == "messages":
        if delegates is not None and isinstance(chunk, tuple) and len(chunk) == TOKEN_CHUNK_PARTS:
            # A `messages` chunk is the only one carrying `lc_agent_name`, so
            # this is where the map learns; ask again once it has.
            named = delegates.name(namespace, chunk[1])
        if (event := _token_event(chunk)) is not None:
            yield replace(event, agent=named)
        return
    if mode != "updates":
        return
    for update in (chunk or {}).values():
        for message in messages_in(update):
            if (event := _event_for(message)) is not None:
                yield replace(event, agent=named)


def answer_in(namespace: Any, mode: str, chunk: Any) -> str | None:
    """The assistant's last message from a `values` chunk, if it has one.

    And `None` for any chunk from a delegate, which is not a refinement -- it is the
    whole reason this takes a namespace. Streaming into delegates makes their
    `values` chunks arrive here too, and "the last one wins" then means the last
    *anyone* emitted. A turn that ends normally is unharmed, because the caller's
    agent always speaks last. A turn cut short by `turn_timeout_s` is not: it stops
    between chunks, and if it stopped just after a delegate finished, the run
    reported the delegate's answer as its own. Measured on a real two-level run
    before this line existed.
    """
    if namespace:
        return None
    if mode != "values":
        return None
    messages = messages_in(chunk)
    if not messages:
        return None
    return getattr(messages[-1], "text", None) or ""
