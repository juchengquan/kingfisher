"""The JavaScript sandbox a turn may run, and getting rid of it afterwards."""

from __future__ import annotations

import logging
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from kingfisher.core.definitions.subagents import INTERPRETER_TOOL, TASK_TOOL

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from kingfisher.core.config import Config


#: The one warning kingfisher causes on purpose. Matched on the message, not silenced
#: by level: the same logger carries warnings that matter.
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


def interpreter(
    cfg: Config,
    permitted: tuple[str, ...] | None,
    *,
    gated: Collection[str] = (),
    offered: Sequence[Any] = (),
) -> Any:
    """The JavaScript sandbox, if this deployment wired one.

    `offered` is the offered tools granted, each already wrapped by
    `guarded_tools`, and `gated` the names this agent stops on.

    `max_snapshot_bytes=1` drops the VM image rather than writing the whole QuickJS heap
    into the checkpoint every turn, `eval` called or not, which is what makes the
    sandbox affordable to leave on. The cost is that a value computed in one `eval` is
    gone by the next. The image is a constant size, so no cap keeps part of it.
    """
    # Deferred, so a run that never enables the sandbox does not import it.
    from langchain_quickjs import CodeInterpreterMiddleware  # noqa: PLC0415

    # Beside the cap below, which is what causes the warning.
    quieten_expected_snapshot_drop()

    # A call from code meets no `wrap_tool_call` and no gate, so this list must be safe
    # with neither. An offered tool goes in as its `GuardedTool`, never by name, which
    # would reach the bare object; a gated tool is left out, since from here it would
    # run with nobody asked. `None` turns `tools.*` off, not on for everything.
    wrapped = {one.name for one in offered}
    ptc: list[Any] | None = (
        None
        if permitted is None
        else [
            *(one for one in offered if one.name not in gated),
            *(
                name
                for name in permitted
                if name != TASK_TOOL and name not in wrapped and name not in gated
            ),
        ]
    )
    return CodeInterpreterMiddleware(
        tool_name=INTERPRETER_TOOL,
        # Never `task`, which the library refuses here: it is the `task()` global,
        # governed by `subagents=`.
        ptc=ptc,
        # The tool-call grant: left at its default, a request that withheld `task`, or
        # gated it, could still delegate from inside the sandbox.
        subagents=(permitted is None or TASK_TOOL in permitted) and TASK_TOOL not in gated,
        mode="thread",
        # Below any real snapshot, so every one is dropped.
        max_snapshot_bytes=1,
        timeout=float(cfg.execution_timeout_s),
    )


def release_interpreter(cfg: Config, graph: Any) -> None:
    """Close the QuickJS runtime a turn started, before anything else can.

    `quickjs_rs` pins its Runtime and Context to one worker thread, being `!Send`, and
    only a `gc.collect()` on that thread closes them. Left to `Py_FinalizeEx` on the
    main thread at shutdown, the process deadlocks: an unattended run stops, not fails.
    """
    if not cfg.interpreter_enabled:
        return
    from langchain_quickjs import CodeInterpreterMiddleware  # noqa: PLC0415

    for node in getattr(getattr(graph, "nodes", None), "values", tuple)():
        owner = getattr(getattr(getattr(node, "bound", None), "func", None), "__self__", None)
        if isinstance(owner, CodeInterpreterMiddleware):
            with suppress(Exception):
                # Private, and the only handle there is.
                owner._registry.close()
            return
