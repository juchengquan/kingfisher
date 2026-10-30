"""What a caller's tool is handed for the turn it runs in."""

from __future__ import annotations

# `datetime` and `Mapping` are not under TYPE_CHECKING: pydantic resolves `Caller`'s
# annotations when a tool is bound.
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from kingfisher.core.reachable import Reachable

if TYPE_CHECKING:
    from kingfisher.core.interfaces import ScratchRun

#: Why a tool's run was refused when nothing was bound for its call.
NOT_GRANTED = (
    "refused: the agent that called this tool may not run commands itself, so neither may "
    "its tools"
)


#: Not a `ToolContext` field: one context is handed to every graph a turn runs, a
#: subagent's included, so only a value set per call can tell them apart.
GRAPH: ContextVar[str | None] = ContextVar("kingfisher_current_graph", default=None)


def current_graph() -> str | None:
    """The name of the graph whose tool call is running here: a subagent's, or `None` in
    the agent the caller asked and outside any tool call."""
    return GRAPH.get()


@dataclass(frozen=True)
class Caller:
    """Who a turn runs for, as its tools are handed it: on `ToolContext.caller`, never in
    what the model is sent."""

    #: The source ids the caller presented, `None` where nothing narrows: no lock in the
    #: deployment, or a run `UNSCOPED`.
    source_ids: frozenset[str] | None
    datazone: str | None
    #: The request's `context`, as the caller sent it.
    context: Mapping[str, str]
    #: When the turn started, in UTC. Kingfisher's clock, so no request sets the date; a
    #: tool converts it to the caller's timezone where `context` names one.
    started: datetime


@dataclass(frozen=True)
class ToolContext:
    """The run context of every turn kingfisher builds the graph for.

    A tool reads it as `runtime.context`, from a parameter written
    `runtime: ToolRuntime[ToolContext]`. Kept apart from the backend class it carries,
    which subclasses a deepagents one, so importing it for that annotation stays cheap.
    """

    #: The turn's filesystem under its permissions: a deepagents `BackendProtocol` taking
    #: the file tools' virtual paths. `Any`, since naming that type would import it.
    backend: Any
    #: Bound for one call by `ScratchRunsMiddleware`, and only where the calling agent may
    #: use the shell itself; `None` refuses every run.
    scratch: Any = None
    #: Who the turn runs for. `None` only for a build no turn asked for.
    caller: Caller | None = None
    #: What `caller` can use this turn, worked out only when a tool asks.
    reachable: Reachable | None = None

    def run(
        self,
        argv: Sequence[str],
        files: Mapping[str, bytes] | None = None,
        *,
        outputs: Sequence[str] = (),
        timeout: int | None = None,
    ) -> ScratchRun:
        """Run `argv`, with no shell, in a new folder holding `files`, inside the
        session's fenced shell, and bring back the files named in `outputs`.

        Names are flat. `timeout` is clamped to what is left of this call's time.
        """
        if self.scratch is None:
            return _refused()
        return self.scratch.run(argv, files or {}, outputs=outputs, timeout=timeout)

    async def arun(
        self,
        argv: Sequence[str],
        files: Mapping[str, bytes] | None = None,
        *,
        outputs: Sequence[str] = (),
        timeout: int | None = None,
    ) -> ScratchRun:
        """`run`, for an `async` tool."""
        if self.scratch is None:
            return _refused()
        return await self.scratch.arun(argv, files or {}, outputs=outputs, timeout=timeout)


def _refused() -> ScratchRun:
    # Here, not at the top: a tool importing this module for its annotation pays nothing.
    from kingfisher.core.interfaces import ScratchRun  # noqa: PLC0415

    return ScratchRun(error=NOT_GRANTED)
