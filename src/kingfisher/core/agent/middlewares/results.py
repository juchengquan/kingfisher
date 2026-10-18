"""The answer a tool call gets: found in what the tool returned, swapped for another, or
written when kingfisher's middleware stops or fails the call."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from langchain_core.messages import ToolMessage
from langgraph.types import Command


def failed(call: Any, content: str) -> ToolMessage:
    """A failed result for `call`, which the model reads and the turn goes on past."""
    return ToolMessage(
        content=content,
        tool_call_id=call.get("id", ""),
        name=call.get("name"),
        status="error",
    )


def answer_in(result: Any, call_id: Any) -> ToolMessage | None:
    """The message that answers this call, inside the `Command` `task` returns too."""
    if isinstance(result, ToolMessage):
        return result
    if not isinstance(result, Command) or not isinstance(result.update, Mapping):
        return None
    found = [one for one in result.update.get("messages") or () if isinstance(one, ToolMessage)]
    return next((one for one in found if one.tool_call_id == call_id), None)


def answered(result: Any, answer: ToolMessage, new: ToolMessage) -> Any:
    """`result` with `new` where `answer_in` found `answer`.

    A `Command` is copied, not changed in place: a tool may hand back one it still holds.
    """
    if result is answer:
        return new
    messages = [new if one is answer else one for one in result.update["messages"]]
    return replace(result, update={**result.update, "messages": messages})
