"""Anticorruption layer between kingfisher's domain and the agent runtime."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import replace
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langgraph.errors import GraphRecursionError
from langgraph.types import Command, StreamMode

from kingfisher.core.definitions.subagents import TASK_TOOL
from kingfisher.core.request import Decision, DecisionError
from kingfisher.core.result import DECISIONS, PendingDecision, RunEvent

from .transcript import Message, Role, ToolCall

#: What langgraph raises out of `stream` when a turn uses up `recursion_limit`.
OutOfSteps = GraphRecursionError

#: A provider declining a request it may serve later: a rate limit or quota, and the
#: 5xx range, where Anthropic's 529 "overloaded" sits. A 401 is a configuration error,
#: and the rest of the 4xx range is a request that will fail the same way again.
TOO_MANY_REQUESTS = 429
SERVER_ERROR = 500

#: Matched by name through the class's ancestry, so neither SDK -- nor httpx, which both
#: send through -- is imported here.
TIMED_OUT = frozenset({"APITimeoutError", "TimeoutException", "ModelTimeoutError"})
UNREACHED = frozenset(
    {"APIConnectionError", "NetworkError", "RemoteProtocolError", "ModelConnectionError"}
)


class ModelUnavailableError(Exception):
    """A model call the provider could not serve now, which ends a turn as a stop."""

    def __init__(self, why: str) -> None:
        super().__init__(why)
        self.why = why


def unavailable(exc: BaseException) -> str | None:
    """Why the provider could not answer, where trying again later can work, else `None`.

    The causes are read too: langchain re-raises an SDK's error as one of its own.
    """
    seen: set[int] = set()
    one: BaseException | None = exc
    while one is not None and id(one) not in seen:
        seen.add(id(one))
        if isinstance(one, ModelUnavailableError):
            return one.why
        status = getattr(one, "status_code", None)
        if isinstance(status, int) and (status == TOO_MANY_REQUESTS or status >= SERVER_ERROR):
            return f"{status}: {_said(one)}"
        names = {cls.__name__ for cls in type(one).__mro__}
        # Before the connection check: both SDKs' timeout is a kind of connection error.
        if names & TIMED_OUT or isinstance(one, TimeoutError):
            return "the request timed out"
        if names & UNREACHED or isinstance(one, ConnectionError):
            return "the connection to the provider failed"
        one = one.__cause__ or one.__context__
    return None


def _said(exc: BaseException) -> str:
    """The provider's own words, from the body where it sent one."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        inner = body.get("error", body)
        if isinstance(inner, dict) and isinstance(inner.get("message"), str):
            return inner["message"][:PREVIEW]
    return str(getattr(exc, "message", None) or exc)[:PREVIEW]

#: How much of a tool result or message to keep on an event.
PREVIEW = 300

#: A `messages` chunk is a (message, metadata) pair.
TOKEN_CHUNK_PARTS = 2

#: `updates` drives progress events; `values` carries the state, whose last emission
#: holds the answer; `messages` the model's output as it comes; `custom` what
#: kingfisher's middleware reports of a reply that never reaches the state.
STREAM_MODES: list[StreamMode] = ["updates", "values", "messages", "custom"]

#: The key a middleware writes to the `custom` stream under, for a cut answer it asked
#: again and so kept out of the state, where a `model_call` event is otherwise read from.
CUT_OFF_MARK = "kingfisher.cut_off"


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
        # Both shapes occur: `_as_langchain` emits plain dicts where one will do, and a
        # graph may hand back what it was given rather than a coerced object.
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


#: A role already kingfisher's, for a message handed back as a plain dict. Spelled
#: out rather than derived, so only a role this vocabulary has gets in.
_CANONICAL: dict[str, Role] = {
    "system": "system",
    "user": "user",
    "assistant": "assistant",
    "tool": "tool",
}


#: LangChain's `.type` for each role kingfisher records. `system` rarely appears: the
#: system prompt is rebuilt per turn rather than stored.
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


def cut_off(message: Any) -> bool:
    """Whether a reply stopped because it reached the model's output cap.

    Read off the stop reason because nothing else shows it: a call cut partway arrives
    with its JSON closed, by langchain's streaming repair or by the provider.
    """
    meta = getattr(message, "response_metadata", None) or {}
    if meta.get("stop_reason") == "max_tokens":
        return True
    # The Responses API's way of saying it, which has no stop reason.
    details = meta.get("incomplete_details") or {}
    return meta.get("status") == "incomplete" and details.get("reason") == "max_output_tokens"


def output_limit(message: Any) -> str:
    """The cap a cut reply reached, as a phrase: its own output count, which is the cap."""
    spent = usage_of(message)["output_tokens"]
    return f"the {spent:,}-token output limit" if spent else "the output limit"


def cut_off_mark(reply: Any) -> dict[str, Any]:
    """What the `custom` stream is handed for a cut reply the state will never hold."""
    return {CUT_OFF_MARK: usage_of(reply)}


#: The content blocks that are calls, by the key each keeps its call's id under: the
#: Anthropic format's, the Responses API's, and langchain's own.
CALL_BLOCK_IDS = {"tool_use": "id", "function_call": "call_id", "tool_call": "id"}


def call_block_id(block: Any) -> str | None:
    """The id of the call a content block carries, or `None` for any other block."""
    key = CALL_BLOCK_IDS.get(block.get("type")) if isinstance(block, dict) else None
    return block.get(key) if key else None


def with_call_args(message: AIMessage, call_id: str, update: Mapping[str, Any]) -> AIMessage:
    """`message` with one call's arguments updated, in both the places it carries them.

    `tool_calls` is what langchain-anthropic sends, and the call's block in the content
    is what langchain counts in its place; langchain-openai sends its `function_call`
    block instead. Changing only one copy sent the old arguments, or counted them, all
    the same.
    """
    calls = [
        {**call, "args": {**call["args"], **update}} if call["id"] == call_id else call
        for call in message.tool_calls
    ]
    args = next(call["args"] for call in calls if call["id"] == call_id)
    content = message.content
    if isinstance(content, list):
        content = [_block_with(block, call_id, args) for block in content]
    return message.model_copy(update={"tool_calls": calls, "content": content})


def _block_with(block: Any, call_id: str, args: Mapping[str, Any]) -> Any:
    if call_block_id(block) != call_id:
        return block
    if block["type"] == "tool_use":
        return {**{k: v for k, v in block.items() if k != "partial_json"}, "input": dict(args)}
    if block["type"] == "function_call":
        return {**block, "arguments": json.dumps(args)}
    return {**block, "args": dict(args)}


def ended_cut_off(state: Any) -> Any | None:
    """The agent's last reply where it was cut off at the output cap, else `None`."""
    messages = (getattr(state, "values", None) or {}).get("messages") or ()
    last = messages[-1] if messages else None
    return last if isinstance(last, AIMessage) and cut_off(last) else None


def _event_for(message: Any) -> RunEvent | None:
    if isinstance(message, ToolMessage):
        return RunEvent(
            kind="tool_result",
            tool=getattr(message, "name", None),
            text=message.text[:PREVIEW],
        )
    if isinstance(message, AIMessage):
        # One event per model turn, with its tools' arguments but not its prose, which
        # already arrived as tokens.
        names, args = tool_calls(message)
        # `.text`, not `str()`, which renders the Responses API's content blocks as a repr.
        # A message with no text, tools or usage is a state shuffle, not a turn.
        if not (names or message.text.strip() or getattr(message, "usage_metadata", None)):
            return None
        return RunEvent(
            kind="model_call",
            tools=names,
            args=args,
            usage=usage_of(message),
            cut_off=cut_off(message),
        )
    return None


def messages_in(update: Any) -> list[Any]:
    """Messages carried by a node update, tolerating shapes that carry none."""
    # A node writing twice in one step streams a list of writes, as the model node does
    # when clearing runs inside the call; read as one mapping, the reply would be lost.
    if isinstance(update, (list, tuple)):
        return [message for write in update for message in messages_in(write)]
    if not isinstance(update, Mapping):
        return []
    messages = update.get("messages")
    if messages is None:
        return []
    return list(messages) if isinstance(messages, (list, tuple)) else [messages]


class SubagentNames:
    """Which subagent produced a chunk, learned from the stream as it arrives.

    Only `messages` chunks carry a name, in `lc_agent_name`; `updates` carry none. A
    subagent's first model call is streamed before the update reporting it, so the name
    is known in time -- driven, not assumed, by a test on a real two-level run.
    """

    def __init__(self) -> None:
        self._names: dict[tuple[str, ...], str] = {}

    def name(self, namespace: Any, metadata: Any = None) -> str | None:
        """The subagent a chunk belongs to, or `None` for the main agent."""
        key = tuple(namespace or ())
        if not key:
            return None
        if isinstance(metadata, Mapping) and (found := metadata.get("lc_agent_name")):
            self._names[key] = str(found)
        return self._names.get(key)


class SeenMessages:
    """Which messages each graph's conversation already held, read off `values` chunks.

    A node can write back a message it did not make, under the same id -- clearing, or
    the summariser clipping a result -- and its update reads like a new one. The id
    tells them apart: `values` arrives after a step's writes and `updates` before, so
    what is held here is the conversation as the step found it.
    """

    def __init__(self) -> None:
        self._ids: dict[tuple[str, ...], frozenset[str]] = {}

    def held(self, namespace: Any, state: Any) -> None:
        """Note what one graph's conversation holds, from a `values` chunk."""
        if not isinstance(state, Mapping):
            return
        self._ids[tuple(namespace or ())] = frozenset(
            one.id for one in messages_in(state) if getattr(one, "id", None) is not None
        )

    def rewritten(self, namespace: Any, message: Any) -> bool:
        """Whether an update's message is one this graph's conversation already held."""
        known = self._ids.get(tuple(namespace or ()), frozenset())
        return getattr(message, "id", None) in known


def _token_event(chunk: Any) -> RunEvent | None:
    """One `messages` chunk into a token event, or nothing."""
    if not isinstance(chunk, tuple) or len(chunk) != TOKEN_CHUNK_PARTS:
        return None
    message, metadata = chunk
    # The summariser calls its model inside the model node, so only the tag it sets
    # tells its tokens from the answer's.
    if isinstance(metadata, Mapping) and metadata.get("lc_source") == "summarization":
        return None
    # Not the base `AIMessage`, which would admit the tool results this exists to exclude.
    if not isinstance(message, AIMessageChunk):
        return None
    text = message.text
    return RunEvent(kind="token", text=text) if text else None


def events_in(
    namespace: Any,
    mode: str,
    chunk: Any,
    subagent_names: SubagentNames | None = None,
    seen: SeenMessages | None = None,
) -> Iterator[RunEvent]:
    """Translate one stream chunk into domain events."""
    named = subagent_names.name(namespace) if subagent_names is not None else None
    if mode == "values":
        if seen is not None:
            seen.held(namespace, chunk)
        return
    if mode == "custom":
        if isinstance(chunk, Mapping) and isinstance(usage := chunk.get(CUT_OFF_MARK), Mapping):
            yield RunEvent(kind="model_call", usage=dict(usage), cut_off=True, agent=named)
        return
    if mode == "messages":
        if (
            subagent_names is not None
            and isinstance(chunk, tuple)
            and len(chunk) == TOKEN_CHUNK_PARTS
        ):
            # Only a `messages` chunk carries `lc_agent_name`, so the map learns here.
            named = subagent_names.name(namespace, chunk[1])
        if (event := _token_event(chunk)) is not None:
            yield replace(event, agent=named)
        return
    if mode != "updates":
        return
    for update in (chunk or {}).values():
        for message in messages_in(update):
            if seen is not None and seen.rewritten(namespace, message):
                continue
            if (event := _event_for(message)) is not None:
                yield replace(event, agent=named)


def answer_in(namespace: Any, mode: str, chunk: Any) -> str | None:
    """The assistant's last message from a `values` chunk, if it has one.

    `None` for a subagent's chunk, which is why this takes a namespace: their `values`
    arrive here too, and a turn cut short just after a subagent finished would report
    that subagent's answer as its own.
    """
    if namespace:
        return None
    if mode != "values":
        return None
    messages = messages_in(chunk)
    if not messages:
        return None
    return getattr(messages[-1], "text", None) or ""


#: Where deepagents' summariser keeps the summary it made. Private state, so what
#: `invoke` returns leaves it out and only the graph's own state has it.
SUMMARY_EVENT = "_summarization_event"


def summarised_in(snapshot: Any) -> bool:
    """Whether a turn ended on a conversation deepagents had summarised, even before
    the turn began, as a resume's restored state can be."""
    values = getattr(snapshot, "values", None)
    return isinstance(values, Mapping) and values.get(SUMMARY_EVENT) is not None


#: The graph node a subagent's pause arrives on: it stopped inside the tool call that
#: runs it, so the task belongs to the tool node rather than to any gate.
TOOLS_NODE = "tools"

#: The argument of `TASK_TOOL` naming which subagent it starts.
SUBAGENT_TYPE_ARG = "subagent_type"

#: Separates the interrupt a gated call belongs to from its place within it. Neither
#: half is an address alone: one `Interrupt` covers every gated call in a message, and
#: the tool call's own id is not in the pause.
CALL_SEPARATOR = "#"


def pending_in(snapshot: Any) -> tuple[PendingDecision, ...]:
    """Every gated call a paused turn is waiting on; nothing for a turn that ran on."""
    found: list[PendingDecision] = []
    for task in getattr(snapshot, "tasks", ()) or ():
        subagent = _subagent_behind(task, snapshot)
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
                        agent=subagent,
                        # Narrowed to what kingfisher accepts: a bare `True` gate also
                        # offers `edit`, which the resume path would refuse.
                        decisions=tuple(d for d in allowed if d in DECISIONS),
                    )
                )
    return tuple(found)


def _subagent_behind(task: Any, snapshot: Any) -> str | None:
    """Which subagent proposed this, where the pause can say so without guessing.

    The name is in the parent's last message, as the `subagent_type` of the `task` call
    that started it, and is unambiguous only while one subagent is in flight. With two
    this is `None`: the wrong name beside a call about to be approved is worse than none.
    """
    # The pause's task first: one message may gate the agent's own call and start one
    # subagent, and the search would hand the agent's call that subagent's name.
    if getattr(task, "name", "") != TOOLS_NODE:
        return None
    for message in reversed(list(getattr(snapshot, "values", {}).get("messages", ()))):
        calls = getattr(message, "tool_calls", None)
        if not calls:
            continue
        started = [
            str(call["args"][SUBAGENT_TYPE_ARG])
            for call in calls
            if call.get("name") == TASK_TOOL and SUBAGENT_TYPE_ARG in (call.get("args") or {})
        ]
        return started[0] if len(started) == 1 else None
    return None


def resume_payload(answers: Iterable[Decision], pending: Iterable[PendingDecision]) -> Any:
    """The caller's answers as a graph is driven with them: per interrupt, in order.

    Returns the `Command`, not the mapping inside it, so this stays the one module
    naming langgraph. Keyed by interrupt id, so a turn paused in two places is answered
    in one resume; a wrong or missing id is named here rather than counted by langgraph.
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
    # Back into the gate's order: langgraph matches `decisions` to `action_requests` by
    # position, so the caller's order could carry an approval onto a rejected call.
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
