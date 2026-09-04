"""The JavaScript sandbox a turn may run, and getting rid of it afterwards.

Its own module because it is a *lifecycle*, not part of wiring a graph. Nothing
in agent assembly calls into it except to build one and to close one, and the
reason it needs closing at all belongs beside the closing rather than in a file
about tools and delegates: of the three things a turn opens, this is the one
that hangs the process rather than leaking a handle.

`quieten_expected_snapshot_drop` is here for the same reason. It exists because
`max_snapshot_bytes=1` makes a warning inevitable, which is a fact about running
QuickJS this way and about nothing else.
"""

from __future__ import annotations

import logging
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from kingfisher.subagents.harness import TASK_TOOL

if TYPE_CHECKING:
    from kingfisher.config import Config


#: The one warning kingfisher asks for and does not want to hear. Matched on
#: the message rather than silenced by level, because the same logger carries
#: warnings that matter -- a snapshot that failed to restore, and a workspace
#: tool skipped for having a name JavaScript cannot spell.
_EXPECTED_DROP = "Dropping QuickJS snapshot"


class _ExpectedSnapshotDrop(logging.Filter):
    """Drop the warning `max_snapshot_bytes=1` makes inevitable.

    The cap is deliberate -- it is what makes the sandbox affordable to leave
    on -- and the library warns every time it drops an image, which is every
    turn. So the warning reports a setting we chose, on a schedule nothing can
    reduce, into the middle of the agent's streamed prose. It reaches the
    terminal at all only because a library that configures no logging leaves
    Python's last-resort handler to print WARNING and above to stderr.

    A filter on that one logger, not a level and not a handler: kingfisher is a
    library, and a library that calls `basicConfig` decides for a program it
    does not own. This suppresses one record and leaves every other decision to
    whoever is hosting us.

    If upstream rewords the message the filter stops matching and the noise
    comes back. That is the safe direction: the failure is visible rather than
    a real warning silently swallowed.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return not str(record.msg).startswith(_EXPECTED_DROP)


def quieten_expected_snapshot_drop() -> None:
    """Install the filter once, on the logger that emits it.

    Idempotent because `_interpreter` runs per request: an agent is built for
    every turn, and a filter added each time would be a list that grows for the
    life of the process.
    """
    logger = logging.getLogger("langchain_quickjs.middleware")
    if not any(isinstance(f, _ExpectedSnapshotDrop) for f in logger.filters):
        logger.addFilter(_ExpectedSnapshotDrop())


def _interpreter(cfg: Config, permitted: tuple[str, ...] | None) -> Any:
    """The JavaScript sandbox, if this deployment wired one.

    `ptc` is the request's own tool grant, unchanged. A caller that withheld
    `execute` cannot reach it from inside the sandbox either; a caller that
    granted it is not escalating by using it from code. That is the same rule
    the parent and its delegates follow -- restrictions attach to narrowing,
    and a caller who narrowed nothing has nothing to escape from.

    `None` rather than an empty tuple when the request is unrestricted: the
    library reads `None` as "no allowlist", and an empty tuple would mean the
    opposite of what an unrestricted request asked for.

    `mode="thread"` because a thread here is a kingfisher session -- the
    checkpointer already keys on the same id, so REPL state and conversation
    state expire together rather than one outliving the other. Largely moot
    while the snapshot is capped below, and kept so that raising the cap gives
    the aligned lifetime rather than a second decision to remember.

    `max_snapshot_bytes=1` drops the VM image instead of storing it, and that
    is the whole reason the sandbox is affordable to leave on. The library
    otherwise serialises the entire QuickJS heap into the checkpoint at the end
    of every turn: measured at exactly 1,280KB each time, a floor rather than a
    cost that scales with the work, and written whether or not `eval` was
    called at all. In one observed run it was called zero times out of
    forty-five tool calls and still cost that. Capping it took a workspace's
    thread database from 2.94MB to 0.31MB across the same two turns.

    What that buys the deployment is the sandbox forgetting between calls: a
    value computed in one `eval` is gone by the next. Everything measured here
    did its whole calculation in a single call -- including the fan-out spike,
    whose loop runs inside one `eval` -- so nothing observed paid for the
    memory it was storing. A deployment that genuinely builds state across
    calls should raise this, and pay the 1,280KB a turn knowingly.

    There is no useful middle value. The image is a constant 1,280KB, so any
    cap under it drops everything and any cap over it keeps everything.

    Dispatching subagents from code needs the *async* path. `task()` inside the
    REPL awaits, so a sync `SqliteSaver` raises `does not support async
    methods` partway through a workflow that has already run. Use `arun` or
    `astream`; everything else here works on either. Undocumented upstream, and
    found by running it.

    Nothing has to be injected for that. This note used to say
    `async_checkpointer(cfg)`, which was true when a sync saver was the default
    and `astream` refused it -- and became advice to reach for the one shared
    database per workspace, which is the contention a database per session
    exists to avoid. `astream` now opens an async saver for the session itself.
    """
    # Deferred so that shipping the sandbox by default costs nothing to the runs
    # that never enable it. Measured, because the saving is smaller than it
    # looks: importing this standalone takes ~0.85s, but nearly all of that is
    # deepagents and langchain, which are loaded already. On top of kingfisher it
    # is ~15ms and ~6MB of resident memory -- worth deferring, not worth
    # restructuring anything else around.
    from langchain_quickjs import CodeInterpreterMiddleware  # noqa: PLC0415

    # Here rather than at import: the cap below is what makes the warning
    # inevitable, so the remedy belongs beside the cause.
    quieten_expected_snapshot_drop()

    # `None` here is the library's "no allowlist", which is what an
    # unrestricted request resolves to. A request that granted no tools gets an
    # empty list, which is the opposite and has to stay distinguishable.
    ptc: list[Any] | None = (
        None if permitted is None else [t for t in permitted if t != TASK_TOOL]
    )
    return CodeInterpreterMiddleware(
        # `task` is refused here by the library: it is always the top-level
        # `task()` global, and routing it through `tools.*` as well would give
        # two dispatch paths, the second losing `responseSchema`. Delegation is
        # governed by `subagents=` below instead.
        ptc=ptc,
        # Dispatch from code follows the same grant as dispatch from a tool
        # call. Left at its default this would let a request that withheld
        # `task` delegate anyway, from inside the sandbox -- a hole of exactly
        # the shape the delegate ceiling exists to close.
        subagents=permitted is None or TASK_TOOL in permitted,
        mode="thread",
        # Below any real snapshot, so every one is dropped. See the docstring:
        # the image is a constant 1,280KB written every turn regardless of use.
        max_snapshot_bytes=1,
        timeout=float(cfg.execution_timeout_s),
    )


def release_interpreter(cfg: Config, graph: Any) -> None:
    """Close the QuickJS runtime a turn started, before anything else can.

    The interpreter's own teardown is `after_agent`, and langgraph does not run
    `after_agent` when the graph raises. So a turn that ended by exception left
    the runtime open, and `CodeInterpreterMiddleware.__del__` was then the only
    thing that would ever close it -- which it attempts, and which is exactly
    the wrong moment.

    What that costs is not a leaked handle. `quickjs_rs` pins its Runtime and
    Context to one worker thread because they are `!Send`, and closing them
    means a `gc.collect()` *on that thread*; its own docstring says a later
    sweep from anywhere else "would hit the `!Send` drop check". At interpreter
    shutdown that sweep is `Py_FinalizeEx`, on the main thread, and the
    finalizer does not panic -- it deadlocks. Measured on a real run: the turn
    hit `recursion_limit`, printed its traceback, and the process then sat
    there for as long as it was left. An unattended run does not fail, it
    stops. That is worse than the exception it followed.

    So the close moves to where the turn ends, beside the session slot and the
    checkpointer connection, and runs however the turn ended.

    Found by walking the compiled graph rather than being handed down, because
    a middleware is not on `build_agent`'s way out and threading one through
    every caller to reach a teardown would put the plumbing in nine signatures.
    Each hook a middleware declares becomes a node whose callable is bound to
    it, which is a shape the same as the one `registered_tools` reads and just
    as unpublished -- so this is best-effort the same way, and
    `test_a_real_build_is_releasable` is what notices a rename upstream.

    `_registry` is private and there is no public equivalent: the class exposes
    `before_agent`/`after_agent` and nothing to call from outside a graph run.
    `after_agent` is not usable here even so -- it resolves its `thread_id` from
    langgraph's context, which is gone by the time a turn is being torn down,
    so it would evict a slot that never existed and leave the real one open.
    """
    if not cfg.interpreter_enabled:
        return
    from langchain_quickjs import CodeInterpreterMiddleware  # noqa: PLC0415

    for node in getattr(getattr(graph, "nodes", None), "values", tuple)():
        owner = getattr(getattr(getattr(node, "bound", None), "func", None), "__self__", None)
        if isinstance(owner, CodeInterpreterMiddleware):
            with suppress(Exception):
                # Private, and the only handle there is -- see the docstring.
                owner._registry.close()
            return
