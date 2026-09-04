"""Thread persistence: the conversation behind a session.

`BaseCheckpointSaver` is already the swappable interface, so this is a factory
rather than a wrapper — wrapping an existing protocol in a bespoke one can only
lose fidelity. A deployment that wants durable graph state passes its own saver
to `Kingfisher(threads=...)`; nothing else changes, including the thread
deletion that `delete_session` and `reap` depend on.

Two builders, and they return the same thing. `build_session_checkpointer` and
`async_session_checkpointer` both hand back an `InMemorySaver`, held for one
turn — the sync and async halves stay separate only because a deployment may
have wired a factory through either.

**Nothing here persists, and that is the design.** A checkpoint holds one turn's
working state; what a later turn reads is the transcript in the session
directory. See *Sessions: what persists and where* in `docs/decisions.md`, which
records why a checkpointer stopped being where a conversation lives: kingfisher
never resumes a graph — no `checkpoint_id`, no `interrupt()` — so what a saver
was preserving was machinery nothing asked for.

This module used to build sqlite savers too, one per workspace, exported for a
deployment that wanted one shared file. They went with the dependencies that
carried them once the server stopped opening one; a deployment that still wants
that arrangement installs `langgraph-checkpoint-sqlite` and passes its own.
"""

from __future__ import annotations

from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langgraph.checkpoint.memory import InMemorySaver

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from langgraph.checkpoint.base import BaseCheckpointSaver


def build_session_checkpointer(session_dir: Path) -> BaseCheckpointSaver:
    """The saver this turn runs on, which holds nothing after it.

    In memory, and that is a change in what a checkpointer is *for* here rather
    than a cheaper way to do the same job. A checkpoint preserves resumable
    graph state — pending writes, channel versions, a position in the graph —
    and kingfisher never resumes a graph: `service.py` passes `{"thread_id":
    session_id}` with no `checkpoint_id`, and there is no `interrupt()`
    anywhere. A turn runs to completion or fails, and the next one continues a
    *conversation*, which is now carried by `domain.transcript` and restored as
    the graph's input.

    So what is left for a saver is one turn's supersteps, and nothing needs to
    outlive the turn that made them.

    **What this gives up, and it was measured rather than assumed.** The default
    was one sqlite database per session, inside the session directory, and that
    bought three things: a conversation deleted with its directory (one real
    workspace held 132 orphaned threads after every session had been reaped); a
    conversation visible to `session_bytes` and so to the quota; and no
    cross-session contention, where at 32 concurrent writers the slowest single
    writer went from 363ms on a shared file to 80ms on its own.

    All three survive, by a different route. The transcript is a file in the
    session, so it is deleted with the directory and counted by the quota. And
    nothing is shared, so there is nothing to contend for. What is genuinely
    gone is the ~20KB of empty database per session, which was the cost rather
    than the benefit.

    `session_dir` is unused and stays in the signature: it is what a deployment
    injecting a *factory* is handed, and a parameter dropped here would change
    that contract for a saver that no longer needs it.
    """
    del session_dir
    return InMemorySaver()


def thread_ids(store: Any) -> tuple[str, ...] | None:
    """Every thread the store holds, or `None` when it cannot say.

    `ThreadStore` is "something that forgets a thread" and deliberately stays
    that narrow; enumerating is a janitor's need, not the domain's, so it is
    asked for here rather than widened into the port. A store that cannot
    enumerate yields `None`, which the caller reads as "cannot reconcile" and
    skips -- the same shape as `registered_tools` returning `()` for "cannot
    check". An injected double is the usual case, and a sweep must not fail
    because the thing it was handed does not answer this question.

    Through the saver's public `list`, not a `SELECT DISTINCT thread_id`.
    Direct SQL measured 411x faster on a real database -- under a millisecond
    against 175ms -- and was still the wrong trade: this runs on a janitor's
    schedule, never on a request, and the public call cannot be broken by an
    upstream schema change. The cost is that `list` deserialises every
    checkpoint, so the 175ms was for 1,894 of them and grows with the
    database. If that ever matters, it is a reason to page, not a reason to
    reach into the schema.
    """
    lister = getattr(store, "list", None)
    if lister is None:
        return None
    return tuple({item.config["configurable"]["thread_id"] for item in lister(None)})


@asynccontextmanager
async def async_session_checkpointer(session_dir: Path) -> AsyncIterator[BaseCheckpointSaver]:
    """The async twin, and there is now nothing asynchronous about it.

    `InMemorySaver` implements both halves of the protocol, so `astream` no
    longer needs a different saver from `stream` — which it did, because
    `SqliteSaver.aget_tuple` raises `NotImplementedError` and an async
    deployment had to inject its own.

    Still a context manager, and still separate. Both are contracts a deployment
    may already depend on: a factory wired for the async path is called through
    this, and collapsing the two would change how an injected one is reached.
    There is simply nothing to close now, which is the point.
    """
    del session_dir
    yield InMemorySaver()


def release_checkpointer(saver: Any) -> None:
    """Close a saver this service opened. Safe to call on anything.

    A per-session database is a file descriptor per session, so a process
    serving many of them has to give them back. Best-effort by design: the turn
    is already over by the time this runs, and failing to close a connection is
    not worth turning a completed turn into an error.

    Nothing is closed that we did not open -- callers pass `None` for an
    injected store, which belongs to the deployment that made it.
    """
    conn = getattr(saver, "conn", None)
    if conn is None:
        return
    with suppress(Exception):
        conn.close()
