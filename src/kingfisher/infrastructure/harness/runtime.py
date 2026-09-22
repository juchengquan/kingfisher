"""Anticorruption layer between kingfisher's domain and the agent runtime."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import replace
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langgraph.errors import GraphRecursionError
from langgraph.types import Command, StreamMode

from kingfisher.domain.request import Decision, DecisionError
from kingfisher.domain.result import DECISIONS, PendingDecision, RunEvent
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


#: The graph node a delegate's pause arrives on: it stopped inside the tool call that
#: runs it, so the task belongs to the tool node rather than to any gate.
TOOLS_NODE = "tools"

#: The tool a parent calls to start a delegate, and the argument naming which one.
DELEGATE_TOOL = "task"
DELEGATE_ARG = "subagent_type"

#: Separates the interrupt a gated call belongs to from its place within it.
#:
#: The pair is the address because neither half is one alone. langgraph gives an
#: `Interrupt` a stable id and nothing smaller -- one interrupt covers every gated
#: call in a message -- and the tool call's own id is not in the pause at all: the
#: payload carries names and arguments, and for a delegate's pause the tool calls
#: belong to a nested state this snapshot cannot reach.
CALL_SEPARATOR = "#"


def pending_in(snapshot: Any) -> tuple[PendingDecision, ...]:
    """Every gated call a paused turn is waiting on; nothing for a turn that ran on."""
    found: list[PendingDecision] = []
    for task in getattr(snapshot, "tasks", ()) or ():
        delegate = _delegate_behind(task, snapshot)
        for pause in getattr(task, "interrupts", ()) or ():
            value = pause.value
            if not isinstance(value, dict):
                continue
            requests = list(value.get("action_requests") or ())
            configs = list(value.get("review_configs") or ())
            for index, request in enumerate(requests):
                review = configs[index] if index < len(configs) else {}
                allowed = review.get("allowed_decisions", ())
                found.append(
                    PendingDecision(
                        call_id=f"{pause.id}{CALL_SEPARATOR}{index}",
                        tool=str(request.get("name") or ""),
                        args=dict(request.get("args") or {}),
                        agent=delegate,
                        # Narrowed to what kingfisher accepts rather than passed on
                        # as the middleware declared it: a bare `True` gate says all
                        # four, `edit` included, and offering a caller a decision the
                        # resume path will refuse is worse than not offering it.
                        decisions=tuple(d for d in allowed if d in DECISIONS),
                    )
                )
    return tuple(found)


def _delegate_behind(task: Any, snapshot: Any) -> str | None:
    """Which delegate proposed this, where the pause can say so without guessing.

    A delegate's pause arrives on the tool node and its nested state is unreachable
    from here, so the name is not in the pause. It is in the parent's own last
    message -- the `subagent_type` of the `task` call that started it -- and that is
    unambiguous only while one delegate is in flight. With two, this is `None` rather
    than a guess: the tool and arguments are right either way, and naming the wrong
    delegate beside a call somebody is about to approve is worse than naming none.
    """
    # Which task the pause came from, before reading any name off the parent. One
    # message may both gate a call of the agent's own and start a delegate, and the
    # search below would then find that `task` call and hand the agent's own call
    # its delegate's name. Exactly one delegate is in flight there, so the refusal
    # to guess further down does not catch it.
    if getattr(task, "name", "") != TOOLS_NODE:
        return None
    for message in reversed(list(getattr(snapshot, "values", {}).get("messages", ()))):
        calls = getattr(message, "tool_calls", None)
        if not calls:
            continue
        started = [
            str(call["args"][DELEGATE_ARG])
            for call in calls
            if call.get("name") == DELEGATE_TOOL and DELEGATE_ARG in (call.get("args") or {})
        ]
        return started[0] if len(started) == 1 else None
    return None


def resume_payload(answers: Iterable[Decision], pending: Iterable[PendingDecision]) -> Any:
    """The caller's answers as a graph is driven with them: per interrupt, in order.

    Returns the `Command` rather than the mapping inside it, so that the one place
    naming langgraph stays this one -- `application/` reaches the runtime through
    this module and the dependency table is what holds that.

    Keyed by interrupt id rather than handed over as one list, which is what lets a
    turn paused in two places be answered in one resume. Within an interrupt the order
    is the order the gate asked in, and the caller never sees it -- they answer the
    ids they were given, and a wrong or missing one is named here rather than counted
    by langgraph two frames later.
    """
    waiting = {item.call_id: item for item in pending}
    answered: dict[str, Decision] = {}
    for answer in answers:
        if answer.call_id not in waiting:
            msg = f"no pending decision with id {answer.call_id!r}"
            raise DecisionError(msg)
        allowed = waiting[answer.call_id].decisions
        if allowed and answer.action not in allowed:
            msg = (
                f"{answer.call_id} ({waiting[answer.call_id].tool}) takes "
                f"{', '.join(allowed)} -- not {answer.action!r}"
            )
            raise DecisionError(msg)
        if answer.action == "respond" and not answer.message:
            msg = f"{answer.call_id} was answered 'respond' with nothing to respond"
            raise DecisionError(msg)
        answered[answer.call_id] = answer
    if unanswered := sorted(set(waiting) - set(answered)):
        msg = f"still waiting on {', '.join(unanswered)}"
        raise DecisionError(msg)

    grouped: dict[str, list[Any]] = {}
    for call_id, answer in sorted(
        answered.items(), key=lambda pair: int(pair[0].rsplit(CALL_SEPARATOR, 1)[1])
    ):
        where = call_id.rsplit(CALL_SEPARATOR, 1)[0]
        grouped.setdefault(where, []).append(_as_langgraph_decision(answer))
    return Command(
        resume={where: {"decisions": decisions} for where, decisions in grouped.items()}
    )


def _as_langgraph_decision(answer: Decision) -> dict[str, Any]:
    """One answer in the shape `HumanInTheLoopMiddleware` reads."""
    if answer.action == "approve":
        return {"type": "approve"}
    # `reject` may carry nothing and the middleware writes its own refusal; `respond`
    # may not, and is refused above rather than sent on as an empty tool result.
    if answer.message:
        return {"type": answer.action, "message": answer.message}
    return {"type": answer.action}
