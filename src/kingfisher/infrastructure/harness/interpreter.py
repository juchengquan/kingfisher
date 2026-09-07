"""The JavaScript sandbox a turn may run, and getting rid of it afterwards."""

from __future__ import annotations

import logging
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from kingfisher.subagents import TASK_TOOL

if TYPE_CHECKING:
    from kingfisher.config import Config


#: The one warning kingfisher asks for and does not want to hear. Matched on
#: the message rather than silenced by level, because the same logger carries
#: warnings that matter -- a snapshot that failed to restore, and a workspace
#: tool skipped for having a name JavaScript cannot spell.
_EXPECTED_DROP = "Dropping QuickJS snapshot"


class _ExpectedSnapshotDrop(logging.Filter):
    """Drop the warning `max_snapshot_bytes=1` makes inevitable."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not str(record.msg).startswith(_EXPECTED_DROP)


def quieten_expected_snapshot_drop() -> None:
    """Install the filter once, on the logger that emits it."""
    logger = logging.getLogger("langchain_quickjs.middleware")
    if not any(isinstance(f, _ExpectedSnapshotDrop) for f in logger.filters):
        logger.addFilter(_ExpectedSnapshotDrop())


def _interpreter(cfg: Config, permitted: tuple[str, ...] | None) -> Any:
    """The JavaScript sandbox, if this deployment wired one.

    `max_snapshot_bytes=1` drops the VM image instead of storing it, and that is the
    whole reason the sandbox is affordable to leave on. The library otherwise
    serialises the entire QuickJS heap into the checkpoint at the end of every turn:
    measured at exactly 1,280KB each time, a floor rather than a cost that scales
    with the work, and written whether or not `eval` was called at all. In one
    observed run it was called zero times out of forty-five tool calls and still cost
    that. Capping it took a workspace's thread database from 2.94MB to 0.31MB across
    the same two turns.

    What that buys the deployment is the sandbox forgetting between calls: a value
    computed in one `eval` is gone by the next. Everything measured here did its
    whole calculation in a single call -- including the fan-out spike, whose loop
    runs inside one `eval` -- so nothing observed paid for the memory it was storing.
    A deployment that genuinely builds state across calls should raise this, and pay
    the 1,280KB a turn knowingly.

    There is no useful middle value. The image is a constant 1,280KB, so any cap
    under it drops everything and any cap over it keeps everything.
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

    What that costs is not a leaked handle. `quickjs_rs` pins its Runtime and Context
    to one worker thread because they are `!Send`, and closing them means a
    `gc.collect()` *on that thread*; its own docstring says a later sweep from
    anywhere else "would hit the `!Send` drop check". At interpreter shutdown that
    sweep is `Py_FinalizeEx`, on the main thread, and the finalizer does not panic --
    it deadlocks. Measured on a real run: the turn hit `recursion_limit`, printed its
    traceback, and the process then sat there for as long as it was left. An
    unattended run does not fail, it stops. That is worse than the exception it
    followed.
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
