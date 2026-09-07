"""What was said in a session, as records this package owns.

**Tool calls are kept, not only the human and assistant text.** Keeping just the
question and the final answer makes the agent forget its own work: the next turn would
see *"summarise /data/x.csv"* -> *"Done, 40 rows"* with no record that `csv_profile`
ran, so it re-does things and cannot refer to what it did.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

#: Who said it. Deliberately the four a provider's wire format has, and not one
#: more: a role this vocabulary invented would be a translation problem for
#: whatever reads these next.
Role = Literal["system", "user", "assistant", "tool"]


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


def as_json(messages: tuple[Message, ...]) -> str:
    """The transcript as one JSON document, newline-delimited."""
    return "".join(
        json.dumps(
            {
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
            },
            sort_keys=True,
        )
        + "\n"
        for message in messages
    )


def from_json(document: str) -> tuple[Message, ...]:
    """Read back what `as_json` wrote."""
    read: list[Message] = []
    for line in document.splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        read.append(
            Message(
                role=raw["role"],
                content=raw.get("content", ""),
                tool_calls=tuple(
                    ToolCall(name=call["name"], args=call.get("args", {}), id=call.get("id", ""))
                    for call in raw.get("tool_calls", ())
                ),
                call_id=raw.get("call_id", ""),
                name=raw.get("name", ""),
            )
        )
    return tuple(read)
