"""What was said in a session, as records this package owns.

Tool calls are kept, not only the human and assistant text: without them the next turn
has no record of the agent's own work, so it redoes it and cannot refer to it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

#: Who said it: only the roles a provider's wire format has, so nothing reading these
#: has to translate one.
Role = Literal["system", "user", "assistant", "tool"]

#: Who wrote a piece of a turn's message: the person asking, in the request's `task`; the
#: deployment, in its `note`; or kingfisher, in everything it adds.
Author = Literal["user", "caller", "kingfisher"]


@dataclass(frozen=True)
class Part:
    """One piece of a turn's message and who wrote it, `None` where nothing can say."""

    text: str
    by: Author | None = None


def joined(parts: Sequence[Part]) -> str:
    """The message the pieces make: each carries the separator that opens it, so nothing
    goes between them."""
    return "".join(part.text for part in parts)


@dataclass(frozen=True)
class ToolCall:
    """One tool an assistant asked for, and the name it asked by."""

    name: str
    args: dict[str, Any] = field(default_factory=dict)
    id: str = ""


@dataclass(frozen=True)
class Message:
    """One thing said, by one party."""

    role: Role
    content: str = ""
    #: Set only on an assistant message that asked for tools.
    tool_calls: tuple[ToolCall, ...] = ()
    #: Set only on a tool message, naming the call it answers.
    call_id: str = ""
    #: Which tool answered, for a reader who has only this line.
    name: str = ""
    #: Set only on the summary deepagents wrote in place of the messages before it, which
    #: it must still recognise when it summarises again.
    summary: bool = False


def as_record(message: Message) -> dict[str, Any]:
    """One message as a line of the turn log holds it, every empty field left out."""
    return {
        "role": message.role,
        **({"content": message.content} if message.content else {}),
        **(
            {
                "tool_calls": [
                    {"name": call.name, "args": call.args, "id": call.id}
                    for call in message.tool_calls
                ]
            }
            if message.tool_calls
            else {}
        ),
        **({"call_id": message.call_id} if message.call_id else {}),
        **({"name": message.name} if message.name else {}),
        **({"summary": True} if message.summary else {}),
    }


def from_record(raw: Any) -> Message:
    """What `as_record` wrote, or a turn log's message kept as `parts`, read back.
    `KeyError` or `TypeError` where it is neither."""
    role = raw["role"]
    parts = raw.get("parts")
    return Message(
        role=role,
        content="".join(part["text"] for part in parts)
        if parts is not None
        else raw.get("content", ""),
        tool_calls=tuple(
            ToolCall(name=call["name"], args=call.get("args", {}), id=call.get("id", ""))
            for call in raw.get("tool_calls", ())
        ),
        call_id=raw.get("call_id", ""),
        name=raw.get("name", ""),
        summary=bool(raw.get("summary", False)),
    )
