"""A `SessionStore` over a directory, and the one a deployment names instead.

Two ways to arrive at the port, kept together because they are alternatives to
each other: `LocalSessionStore` is what a deployment gets for naming a
directory, and `store_named` is what it gets for naming a factory. Which one a
deployment reaches is `Config`'s question and `Kingfisher.__init__` asks it;
both answers are built here, because building one is an adapter's job.

The implementation a deployment gets when it wires nothing, and the one that
makes the port's claim checkable: *a local directory is a perfectly good
implementation of this*. What the constraint forbids is kingfisher assuming a
local disk, not a deployment choosing one — so the default has to be a
directory, or the port would be a promise nobody had ever kept.

Deliberately dull. It walks, it writes, it deletes. Everything interesting about
this design is in *when* a caller reaches for it, not in what happens when they
do, and a first implementation that was clever about batching or streaming would
be optimising a cost nobody has measured yet.

Bytes rather than handles, matching `LocalFileStore`. That means a session's
files are held whole in memory during a transfer, which is a real cost on a
large upload and is the shape both other stores already have — one vocabulary is
worth more here than one saved copy, and the port can grow a streaming twin the
day something measures the need for it.
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping, Sequence
from importlib import import_module
from pathlib import Path

from kingfisher.config import ConfigError

# At runtime, not under `TYPE_CHECKING`, because `store_named` is an
# `isinstance` against it rather than only an annotation. That is what
# `runtime_checkable` on the port is for, and importing the protocol costs
# nothing this module was not already paying -- `domain/ports.py` imports the
# standard library and three of kingfisher's own domain modules.
from kingfisher.domain.ports import SessionStore
from kingfisher.domain.references import within
from kingfisher.domain.transcript import Message, as_json, from_json


class LocalSessionStore:
    """Sessions kept as directories under `root`, one per session id.

    `root` is somewhere the *deployment* chose. If that is a mounted volume, a
    network filesystem or a directory on the same disk as everything else, this
    class neither knows nor cares — which is the property the port exists for.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser().resolve()

    def _held(self, session_id: str) -> Path:
        """Where one session's files sit, refusing an id that would escape.

        `within` rather than a plain join. A session id is kingfisher's own
        today and this is still checked, because "the caller cannot reach this
        argument" is a claim about every call site rather than about this one,
        and a store handed an id from a request later would be checked by
        nobody. `FileStore`'s refs are checked for the same reason and that one
        *is* caller-supplied.
        """
        return within(self.root, session_id)

    def fetch(self, session_id: str) -> dict[str, bytes]:
        """Everything kept for this session, keyed by path relative to its root."""
        held = self._held(session_id)
        if not held.is_dir():
            return {}
        return {
            str(path.relative_to(held)): path.read_bytes()
            for path in sorted(held.rglob("*"))
            if path.is_file()
        }

    def save(self, session_id: str, files: Mapping[str, bytes]) -> None:
        """Keep these files, replacing any of the same name.

        Merges rather than mirrors: a file the store holds and this call does
        not mention survives. That is what lets a caller send only what changed,
        and it is why `forget` exists — without it there would be no way to
        remove anything at all.
        """
        held = self._held(session_id)
        for name, content in files.items():
            target = within(held, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)

    def knows(self, session_id: str) -> bool:
        """Whether anything is held for this session, without reading it."""
        return self._held(session_id).is_dir()

    def forget(self, session_id: str) -> None:
        """Drop everything kept for this session. Idempotent."""
        shutil.rmtree(self._held(session_id), ignore_errors=True)


def store_named(spec: str) -> SessionStore:
    """The store a deployment named in `KINGFISHER_SESSION_STORE_FACTORY`.

    `module:name` for something callable with no arguments. The same string
    `Adapter.chat_class` uses, resolved the same way, and for a related reason:
    naming the thing rather than holding it is what lets a deployment supply an
    implementation kingfisher has never imported.

    Zero arguments is the whole convention. Kingfisher does not know whether a
    store wants a bucket, a region, a DSN or a pool, so it asks for none of them
    and the factory reads its own configuration. A class with a no-argument
    `__init__` satisfies this as readily as a function.

    **What is checked here is the name, not the building.** A spec that will not
    parse, a module that will not import, an attribute that is not there, a
    result that is not a `SessionStore` -- those are wiring mistakes, and a
    `ConfigError` naming the variable is what an operator can act on. A factory
    that raises *its own* exception is left alone: that is the deployment's code
    failing at the deployment's job, its type may be something their own
    handling knows, and this function is already on the traceback saying which
    setting reached it. Wrapping it would replace a `NoCredentialsError` with a
    sentence about configuration that is not what went wrong.

    Called once, when the service is constructed, which is the same moment the
    catalogue is read and for the same reason: a store that cannot be built is a
    wiring mistake, and this is the last point at which saying so is cheap.
    """
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name or not attribute:
        msg = (
            f"KINGFISHER_SESSION_STORE_FACTORY is {spec!r}, which does not name "
            "anything. Write it as 'module:name' -- the import path of a module, a "
            "colon, and something in it callable with no arguments"
        )
        raise ConfigError(msg)
    try:
        module = import_module(module_name)
    except ImportError as exc:
        msg = (
            f"KINGFISHER_SESSION_STORE_FACTORY names module {module_name!r}, which "
            f"cannot be imported ({exc}). It has to be importable by this process, so "
            "an installed package or something already on the path"
        )
        raise ConfigError(msg) from exc
    try:
        factory = getattr(module, attribute)
    except AttributeError as exc:
        msg = (
            f"KINGFISHER_SESSION_STORE_FACTORY names {attribute!r} in {module_name!r}, "
            "which does not define it"
        )
        raise ConfigError(msg) from exc

    store = factory()
    # Shallow -- `runtime_checkable` compares method names and not signatures --
    # and shallow is the mistake worth catching. A factory returning the class
    # instead of an instance, or a config object, or `None` from a function that
    # forgot to return, is told so here rather than at the first turn that tried
    # to save anything.
    if not isinstance(store, SessionStore):
        msg = (
            f"KINGFISHER_SESSION_STORE_FACTORY names {spec!r}, which returned "
            f"{type(store).__name__} -- not a SessionStore. It has to answer to "
            "fetch, save, knows and forget"
        )
        raise ConfigError(msg)
    return store


def restore_into(store: SessionStore, session_id: str, directory: Path) -> tuple[str, ...]:
    """Write back what the store kept, for a directory that has lost it.

    Here rather than in `service.py` because the application layer decides what
    happens and an adapter makes it happen —
    `test_the_application_layer_does_not_write_to_disk_itself` enforces that, and
    it was written after two sets of caller-supplied files ended up with
    different guarantees precisely because one path did its own I/O.

    Only files that are *absent*. On a host keeping its own disk, that is every
    turn after the first finding nothing to do, which is the case that has to
    stay cheap; on a fresh container it is the whole session, which is the case
    that has to work at all.

    Absent rather than differing, and the distinction is deliberate: a file
    present locally and different in the store means a turn was interrupted
    between writing and saving. Nothing here can tell that from a file the
    current turn has simply not saved yet, so the local copy wins and the store
    catches up when this turn ends.

    Returns what it wrote, so a caller can say so rather than guess.
    """
    written: list[str] = []
    for name, content in store.fetch(session_id).items():
        target = within(directory, name)
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        written.append(name)
    return tuple(written)


def keep_from(store: SessionStore, session_id: str, directory: Path, names: Sequence[str]) -> None:
    """Hand the named files to the store, reading them from `directory`.

    `names` rather than a walk, because the caller has just done one:
    `collect_artifacts` lists everything under `/derived` and `/memory` at the
    end of a turn, which is exactly what has to outlive the machine. `/data` is
    re-fetched per request through `FileStore`, and `/runs` is scratch the
    prompt itself calls disposable.

    A name that no longer resolves to a file is skipped rather than refused. The
    list was taken a moment ago and the shell can delete between then and now;
    failing a turn's persistence over a file that has gone is a worse answer
    than keeping the rest.
    """
    store.save(
        session_id,
        {
            name: within(directory, name).read_bytes()
            for name in names
            if within(directory, name).is_file()
        },
    )


#: Where a session's conversation is kept. Dotted and not in `SESSION_DIRS`,
#: for the reason `.home` is not: those are the names the agent addresses, and
#: this is plumbing. It sits at the session root so it is deleted with the
#: session, counted by `session_bytes`, and carried by whatever keeps the rest.
TRANSCRIPT = ".transcript.jsonl"


def read_transcript(directory: Path) -> tuple[Message, ...]:
    """What was said in this session before now, or nothing for a first turn."""
    held = Path(directory) / TRANSCRIPT
    if not held.is_file():
        return ()
    return from_json(held.read_text(encoding="utf-8"))


def write_transcript(directory: Path, messages: tuple[Message, ...]) -> None:
    """Replace this session's transcript with what it now holds.

    Whole rather than appended, which is the honest first version and the one
    that cannot go wrong: the graph hands back a full conversation, and writing
    the part that is new means knowing which part that is. It costs the whole
    history per turn, which grows with the session -- the format is
    line-oriented precisely so that appending is a change of one function rather
    than a migration, the day something measures the need.
    """
    Path(directory).mkdir(parents=True, exist_ok=True)
    (Path(directory) / TRANSCRIPT).write_text(as_json(messages), encoding="utf-8")
