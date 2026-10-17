"""The answer a tool call gets when kingfisher's middleware stops or fails it."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import ToolMessage


def failed(call: Any, content: str) -> ToolMessage:
    """A failed result for `call`, which the model reads and the turn goes on past."""
    return ToolMessage(
        content=content,
        tool_call_id=call.get("id", ""),
        name=call.get("name"),
        status="error",
    )
