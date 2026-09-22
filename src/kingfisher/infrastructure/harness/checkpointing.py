"""Thread persistence: the conversation behind a session."""

from __future__ import annotations

from contextlib import suppress
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver


def build_session_checkpointer(session_dir: Path) -> BaseCheckpointSaver:
    """The saver this turn runs on, which holds nothing after it."""
    del session_dir
    return InMemorySaver()


#: How a paused turn's state is encoded: msgpack, and a pickle neither written nor
#: read, because this serialiser is not constructed to do either.
#:
#: Which is why this is not `InMemorySaver(factory=PersistentDict)`, the persistence
#: seam langgraph ships and the obvious thing to reach for -- it is unconditionally
#: pickle. `.harness` is denied to the file tools, but `execute` bypasses those
#: entirely, and what refuses the shell is `confinement._harness_denial`: a macOS
#: profile, on one platform, that a deployment can switch off. A tampered msgpack
#: checkpoint is bad graph state. A tampered pickle is arbitrary code at resume.
_SERDE = JsonPlusSerializer()


#: What a checkpoint's shape depends on. Not a guess at which of them matters: the
#: node names a checkpoint refers to are built by all three -- langchain compiles a
#: middleware into `f"{name}.after_model"`, deepagents decides which middleware there
#: are, langgraph keys `versions_seen` by node -- so a move in any one can leave a
#: checkpoint referring to nodes the rebuilt graph does not have.
HARNESS_LIBRARIES: tuple[str, ...] = ("deepagents", "langchain", "langgraph")


def harness_mark() -> dict[str, str]:
    """What a checkpoint written now was built against, for a resume to check.

    Checked rather than discovered on load, because a paused session can outlive a
    deploy and that is ordinary rather than exceptional. The difference is one
    sentence saying the pause did not survive an upgrade, against a deserialiser's
    traceback about a node nobody has heard of.
    """
    marked: dict[str, str] = {}
    for name in HARNESS_LIBRARIES:
        with suppress(PackageNotFoundError):
            marked[name] = version(name)
    return marked


def write_paused_state(saver: Any, path: Path) -> None:
    """Keep what a paused turn's saver holds, so a later turn can resume into it.

    The three mappings rather than the saver, because everything they hold is already
    `(type, bytes)` by the time it lands there -- the saver serialised it on the way
    in, and that is what makes writing them out possible without a second format.
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
    path.parent.mkdir(parents=True, exist_ok=True)
    # Staged and renamed rather than written where it is read from. A write cut off
    # halfway leaves the previous checkpoint whole, and a resume that finds nothing
    # is a turn that has to be asked again -- where one that finds half a checkpoint
    # is a resume failing on state that looks present.
    staged = path.with_name(f"{path.name}.partial")
    staged.write_bytes(kind.encode("utf-8") + b"\n" + payload)
    staged.replace(path)


def read_paused_state(path: Path) -> Any | None:
    """The saver a paused turn left behind, or `None` where no turn left one."""
    if not path.is_file():
        return None
    kind, _, payload = path.read_bytes().partition(b"\n")
    document = _SERDE.loads_typed((kind.decode("utf-8"), payload))
    saver = InMemorySaver()
    # Assigned *through* the mappings rather than over them, so the defaultdicts a
    # saver builds for itself survive: `get_tuple` indexes a thread and a namespace
    # that may not be there yet and expects a mapping back rather than a `KeyError`.
    # msgpack has one sequence type, so every tuple in here reads back as a list.
    # Only the keys are converted, because only they need it -- a list is unhashable
    # and these are dict lookups. The values are unpacked and indexed, never hashed
    # or compared, so a recursive conversion over them would be machinery guarding
    # nothing: mutation-tested, and removing it changed no result.
    for thread, namespace, checkpoint_id, entry in document["storage"]:
        saver.storage[thread][namespace][checkpoint_id] = entry
    for outer, inner, entry in document["writes"]:
        saver.writes[tuple(outer)][tuple(inner)] = entry
    for key, entry in document["blobs"]:
        saver.blobs[tuple(key)] = entry
    return saver


def thread_ids(store: Any) -> tuple[str, ...] | None:
    """Every thread the store holds, or `None` when it cannot say.

    Through the saver's public `list`, not a `SELECT DISTINCT thread_id`. Direct SQL
    measured 411x faster on a real database -- under a millisecond against 175ms --
    and was still the wrong trade: this runs on a janitor's schedule, never on a
    request, and the public call cannot be broken by an upstream schema change. The
    cost is that `list` deserialises every checkpoint, so that 175ms was for 1,894 of
    them and grows with the database. If it ever matters, that is a reason to page
    rather than to reach into the schema.
    """
    lister = getattr(store, "list", None)
    if lister is None:
        return None
    return tuple({item.config["configurable"]["thread_id"] for item in lister(None)})


def release_checkpointer(saver: Any) -> None:
    """Close a saver this service opened. Safe to call on anything."""
    conn = getattr(saver, "conn", None)
    if conn is None:
        return
    with suppress(Exception):
        conn.close()
