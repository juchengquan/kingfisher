"""Messages between kingfisher's records and LangChain's, what a reply says of itself, and
the names of the graph's parts the session reads it by."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from .messages import Message, Role, ToolCall

#: The `lc_source` deepagents' summariser tags its model's calls with, and the summary
#: message it writes, by which it knows an earlier summary when it summarises again.
SUMMARISER = "summarization"


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
    if message.summary:
        return HumanMessage(content=message.content, additional_kwargs={"lc_source": SUMMARISER})
    return {"role": message.role, "content": message.content}


def as_messages(messages: Iterable[Any]) -> tuple[Message, ...]:
    """What the graph ended up holding, as records this package owns."""
    read: list[Message] = []
    for raw in messages:
        role = _role(raw)
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
                summary=(getattr(raw, "additional_kwargs", None) or {}).get("lc_source")
                == SUMMARISER,
            )
        )
    return tuple(read)


def reasoning_in(messages: Iterable[Any]) -> tuple[str, ...]:
    """What each message `as_messages` keeps reasoned, in its order: `""` for one whose
    provider handed back none a reader can read."""
    return tuple(_reasoning(raw) for raw in messages if _role(raw) is not None)


def _role(raw: Any) -> Role | None:
    """The role a message is kept under, or `None` for one that is not kept."""
    # Both shapes occur: `_as_langchain` emits plain dicts where one will do, and a graph
    # may hand back what it was given rather than a coerced object.
    if isinstance(raw, dict):
        return _CANONICAL.get(str(raw.get("role", "")))
    return _ROLES.get(getattr(raw, "type", ""))


def _reasoning(raw: Any) -> str:
    """A reply's reasoning as langchain's standard blocks read it, whichever provider wrote
    it, each block apart from the next by a blank line."""
    if not isinstance(raw, AIMessage):
        return ""
    try:
        blocks = raw.content_blocks
    except (AttributeError, KeyError, TypeError, ValueError):
        # A provider block langchain cannot read costs the line its reasoning, not the turn
        # its log.
        return ""
    # Redacted thinking and an encrypted reasoning item carry no text, and are left out.
    return "\n\n".join(
        said
        for block in blocks
        if block.get("type") == "reasoning"
        and isinstance(said := block.get("reasoning"), str)
        and said.strip()
    )


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


def tool_calls(
    message: Any,
) -> tuple[tuple[str, ...], tuple[Mapping[str, Any], ...], tuple[str, ...]]:
    """Names, arguments and ids of the tools a message asks for, index-aligned."""
    calls = getattr(message, "tool_calls", None) or []
    return (
        tuple(call["name"] for call in calls),
        tuple(call.get("args") or {} for call in calls),
        tuple(call.get("id") or "" for call in calls),
    )


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


#: The graph node a subagent's pause arrives on: it stopped inside the tool call that
#: runs it, so the task belongs to the tool node rather than to any gate.
TOOLS_NODE = "tools"

#: The argument of `TASK_TOOL` naming which subagent it starts.
SUBAGENT_TYPE_ARG = "subagent_type"
