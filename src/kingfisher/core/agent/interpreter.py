"""The JavaScript sandbox a turn may run, and getting rid of it afterwards."""

from __future__ import annotations

import logging
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from kingfisher.core.definitions.subagents import INTERPRETER_TOOL, TASK_TOOL

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

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


def _interpreter(
    cfg: Config,
    permitted: tuple[str, ...] | None,
    *,
    gated: Collection[str] = (),
    workspace: Sequence[Any] = (),
) -> Any:
    """The JavaScript sandbox, if this deployment wired one.

    `workspace` is the granted workspace tools, each already wrapped by
    `guarded_tools`, and `gated` the names this agent stops on.

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

    # A call from code never passes `ToolNode`, so no `wrap_tool_call` sees it, and
    # it was never a tool call on the model's message, which is what a gate reads.
    # Whatever this list holds has to be safe with neither.
    #
    # A workspace tool goes in as its `GuardedTool`, never by name: a name is
    # matched to the bare object the agent registered, which from here reads
    # another session's files and ends the turn when it raises.
    #
    # A gated tool is left out, since from here it would run with nobody asked. It
    # stays callable directly, behind its gate.
    #
    # `None` turns `tools.*` off -- the library's default, not an allowlist of
    # everything -- so an unrestricted request reaches no tool from code at all.
    wrapped = {one.name for one in workspace}
    ptc: list[Any] | None = (
        None
        if permitted is None
        else [
            *(one for one in workspace if one.name not in gated),
            *(
                name
                for name in permitted
                if name != TASK_TOOL and name not in wrapped and name not in gated
            ),
        ]
    )
    return CodeInterpreterMiddleware(
        tool_name=INTERPRETER_TOOL,
        # `task` is refused here by the library: it is always the top-level
        # `task()` global, and routing it through `tools.*` as well would give
        # two dispatch paths, the second losing `responseSchema`. Delegation is
        # governed by `subagents=` below instead.
        ptc=ptc,
        # Dispatch from code follows the same grant as dispatch from a tool
        # call. Left at its default this would let a request that withheld
        # `task` subagent anyway, from inside the sandbox -- a hole of exactly
        # the shape the subagent ceiling exists to close. A gate on `task` is
        # the same hole with a person in it: `task()` from code would run the
        # subagent with nobody asked.
        subagents=(permitted is None or TASK_TOOL in permitted) and TASK_TOOL not in gated,
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
