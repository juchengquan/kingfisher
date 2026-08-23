"""What was said in a session, as records this package owns.

A conversation is the most durable thing a session has and the most sensitive.
It was a sqlite database written by langgraph's checkpointer — which preserves
resumable *graph* state: pending writes, channel versions, a position in the
graph. Kingfisher never resumes a graph. `service.py` passes
`{"thread_id": session_id}` and no `checkpoint_id`, and there is no `interrupt()`
anywhere, so a turn runs to completion or fails and the next one continues a
*conversation*. The machinery was paid for and unused.

So this is what a session's history actually is here, in kingfisher's own
vocabulary rather than a framework's. Two things follow, and the second is the
one worth defending.

**A harness is a choice, and this outlives one.** Storing LangChain's classes
would make every stored conversation a bet that the next harness reads them.
Roles and tool calls are what every provider's wire format already carries, so a
record of those travels; `infrastructure/harness/` translates, and if the
framework changes that is the file that changes.

**Tool calls are kept, not only the human and assistant text.** "Flattened" has
a cheaper reading that keeps the question and the final answer, and it makes the
agent forget its own work: the next turn would see *"summarise /data/x.csv"* →
*"Done, 40 rows"* with no record that `csv_profile` ran or what it returned, so
it re-does things and cannot refer to what it did. Portability does not require
that loss — it requires not storing a framework's objects.
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
    """One tool an assistant asked for, and the name it asked by.

    `id` is what pairs a call with its result, and it comes from the model
    rather than from here. Kept verbatim: a provider that sees its own id come
    back is a provider that can match them, and re-minting would break exactly
    the pairing this exists to record.
    """

    name: str
    args: dict[str, Any] = field(default_factory=dict)
    id: str = ""


@dataclass(frozen=True)
class Message:
    """One thing said, by one party.

    Flat on purpose. `content` is text because that is what every role has;
    an assistant additionally has `tool_calls`, and a tool result additionally
    answers a `call_id`. Nothing here holds a provider's raw payload — a record
    that carried one would be a record only that provider could read, which is
    the thing this file exists to avoid.
    """

    role: Role
    content: str = ""
    #: Set only on an assistant message that asked for tools.
    tool_calls: tuple[ToolCall, ...] = ()
    #: Set only on a tool message, naming the call it answers.
    call_id: str = ""
    #: Which tool answered, for a reader who has only this line.
    name: str = ""


def as_json(messages: tuple[Message, ...]) -> str:
    """The transcript as one JSON document, newline-delimited.

    One object per line rather than one array, because a transcript is appended
    to and a line-oriented file can be appended to without rewriting it. Nothing
    here appends yet -- the turn writes the whole thing -- and the format is
    chosen so that the day something measures the cost, the fix does not need a
    migration.

    Readable on purpose. This is the one thing in a session a person may need to
    inspect after the fact, and a binary format would mean writing a tool before
    anyone could answer "what did it actually say".
    """
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
    """Read back what `as_json` wrote.

    A blank line is skipped rather than refused: a file written and re-read
    across a crash may end in one, and losing a whole conversation over trailing
    whitespace is a worse answer than ignoring it.

    A malformed line is *not* skipped. That is data loss with no error, which is
    the failure this whole design exists to prevent -- if a transcript cannot be
    read, the caller should be told rather than handed a shorter conversation
    that looks complete.
    """
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
