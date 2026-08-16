"""What crosses the wire, written once and by hand.

Deliberately not pydantic mirrors of the dataclasses. `RunResult` keeps
`run_dir` and `log_path` as `Path` precisely so `json.dumps` raises on them --
there is a test asserting they refuse to serialise -- and a mirrored model is a
second home for that rule. It is the kind of second home that gets it wrong
helpfully: adding a `Path` serialiser makes the error go away and ships exactly
the leak the original refuses.

So the rule lives in one function per type, here, and fastapi is used for
validating what comes *in* rather than for describing what goes out.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kingfisher import SessionInfo


def session_payload(info: SessionInfo) -> dict[str, object]:
    """One session, as something outside is told about it.

    Two fields, and the absence of a third is the point: no directory. A
    caller handed one would start reading files out of it, and the layout would
    become a contract nobody wrote down. `SessionInfo` has no such field, so
    this is a rename rather than a filter -- and `id` becomes `session_id`
    because on the wire it is the only id there is a name for.
    """
    return {"session_id": info.id, "last_used": info.last_used}
