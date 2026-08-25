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

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import replace
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langgraph.errors import GraphRecursionError
from langgraph.types import StreamMode

from kingfisher.domain.result import RunEvent
from kingfisher.domain.transcript import Message, Role, ToolCall

#: What langgraph raises out of `stream` when a turn uses up `recursion_limit`.
#:
#: Re-exported rather than imported where it is caught, because the layer that
#: catches it is `application` and only `infrastructure/harness` may name
#: langgraph -- the same reason `STREAM_MODES` lives here. It is an exception
#: type rather than a shape, so there is nothing to translate: what the ACL
#: adds is the import, and the guarantee that swapping the runtime changes one
#: line here rather than one in the orchestration.
OutOfSteps = GraphRecursionError

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


def user_payload(text: str, history: tuple[Message, ...] = ()) -> dict[str, Any]:
    """The graph's input: what was said before, then what is being asked now.

    The history used to arrive from a checkpointer keyed by `thread_id`, and
    this sent only the new turn. It is passed in now, because a transcript this
    package owns is what survives a machine that may keep nothing — see
    `domain.transcript`. The graph gets a whole conversation and a fresh
    checkpointer each turn, which is the same conversation by a route that does
    not depend on a framework's storage.
    """
    return {"messages": [*(_as_langchain(message) for message in history),
                         {"role": "user", "content": text}]}


def _as_langchain(message: Message) -> Any:
    """One of kingfisher's records, in the shape LangChain expects.

    Dicts rather than message classes wherever a dict will do: langchain coerces
    them, and a record that never becomes a `HumanMessage` is one less thing to
    keep in step with a library.

    A tool result has to be a `ToolMessage`, because `tool_call_id` has no dict
    spelling that survives coercion -- and losing it would break the pairing
    that makes a tool result mean anything.
    """
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
    """What the graph ended up holding, as records this package owns.

    The other direction, and the only place that knows how LangChain spells a
    conversation. A message whose type is not one of the four roles is dropped
    rather than guessed at: a framework that grows a fifth kind should make this
    lose information visibly rather than invent a role for it.
    """
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
    """A message's text, whatever shape the provider used.

    Anthropic returns a list of blocks where OpenAI returns a string, and a
    transcript that stored one shape would be unreadable by the other.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    return str(content)


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


class Delegates:
    """Which delegate produced a chunk, learned from the stream as it arrives.

    With `subgraphs=True` every chunk carries a namespace: `()` for the agent
    the caller asked, and an opaque `("tools:<uuid>",)` for a delegate running
    inside a `task` call. That distinguishes them but does not name them.

    Only `messages` chunks carry a name, in `lc_agent_name`. `updates` carry no
    metadata at all -- which would leave `model_call` and `tool_result` unnamed
    if the two were read independently. They are not: a delegate's first model
    call is *streamed* before the node update that reports it, so the name is
    always known by the time an event needs it. Measured, not assumed; a test
    drives a real two-level run and asserts every delegate event is named.
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


def events_in(
    namespace: Any, mode: str, chunk: Any, delegates: Delegates | None = None
) -> Iterator[RunEvent]:
    """Translate one stream chunk into domain events.

    Which modes exist, and what each carries, is decided here rather than by
    the orchestration above. `updates`, `values` and `messages` are LangGraph's
    vocabulary, and `application/` should no more compare against them than it should
    reach for `input_token_details` -- which is the duplication this module was
    written to end.

    `namespace` says which agent produced the chunk, and `delegates` turns it
    into a name. Both flow through every event so that a delegate's work is
    distinguishable at the far end: the caller's prose and a delegate's arrive
    as the same type on the same channel, so nothing else could tell them apart.
    """
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

    `None` for any other mode, so a caller can offer every chunk and keep the
    last answer it is given: the final emission is the authoritative one.

    And `None` for any chunk from a delegate, which is not a refinement -- it
    is the whole reason this takes a namespace. Streaming into delegates makes
    their `values` chunks arrive here too, and "the last one wins" then means
    the last *anyone* emitted. A turn that ends normally is unharmed, because
    the caller's agent always speaks last. A turn cut short by
    `turn_timeout_s` is not: it stops between chunks, and if it stopped just
    after a delegate finished, the run reported the delegate's answer as its
    own. Measured on a real two-level run before this line existed.
    """
    if namespace:
        return None
    if mode != "values":
        return None
    messages = messages_in(chunk)
    if not messages:
        return None
    return getattr(messages[-1], "text", None) or ""
