"""Thread persistence: the conversation behind a session."""

from __future__ import annotations

from contextlib import suppress
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver


def build_session_checkpointer() -> BaseCheckpointSaver:
    """The saver this turn runs on, which holds nothing after it."""
    return InMemorySaver()


#: msgpack, never pickle: the shell can reach `.harness` wherever a deployment turns the
#: sandbox off, and a tampered pickle is arbitrary code at resume. Hence not
#: `InMemorySaver(factory=PersistentDict)`, which always pickles.
_SERDE = JsonPlusSerializer()


#: What a checkpoint's shape depends on: all three build the node names it refers to,
#: so a move in any one can leave it naming nodes the rebuilt graph does not have.
HARNESS_LIBRARIES: tuple[str, ...] = ("deepagents", "langchain", "langgraph")


def harness_mark() -> dict[str, str]:
    """What a checkpoint written now was built against, for a resume to check.

    Checked rather than discovered on load: a pause outliving a deploy is ordinary,
    and earns a sentence rather than a deserialiser's traceback.
    """
    marked: dict[str, str] = {}
    for name in HARNESS_LIBRARIES:
        with suppress(PackageNotFoundError):
            marked[name] = version(name)
    return marked


def paused_state(saver: Any) -> bytes:
    """What a paused turn's saver holds, as bytes a later turn can resume from.

    The three mappings rather than the saver: what they hold is already
    `(type, bytes)`, so writing them out needs no second format.
    """
    document = {
        "storage": [
            [thread, namespace, checkpoint_id, entry]
            for thread, spaces in saver.storage.items()
            for namespace, entries in spaces.items()
            for checkpoint_id, entry in entries.items()
        ],
        "writes": [
            [list(outer), list(inner), entry]
            for outer, pending in saver.writes.items()
            for inner, entry in pending.items()
        ],
        "blobs": [[list(key), entry] for key, entry in saver.blobs.items()],
    }
    kind, payload = _SERDE.dumps_typed(document)
    return kind.encode("utf-8") + b"\n" + payload


def resumed_saver(state: bytes) -> Any:
    """The saver `paused_state` was taken from, rebuilt from its bytes."""
    kind, _, payload = state.partition(b"\n")
    document = _SERDE.loads_typed((kind.decode("utf-8"), payload))
    saver = InMemorySaver()
    # Assigned *through* the mappings, so the saver's defaultdicts survive: `get_tuple`
    # indexes a thread and namespace that may not exist yet. msgpack reads every tuple
    # back as a list, and only the keys are hashed, so only they are converted.
    for thread, namespace, checkpoint_id, entry in document["storage"]:
        saver.storage[thread][namespace][checkpoint_id] = entry
    for outer, inner, entry in document["writes"]:
        saver.writes[tuple(outer)][tuple(inner)] = entry
    for key, entry in document["blobs"]:
        saver.blobs[tuple(key)] = entry
    return saver
