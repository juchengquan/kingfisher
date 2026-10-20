"""What a caller's tool is handed for the turn it runs in."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kingfisher.core.interfaces import ScratchRun

#: Why a tool's run was refused when nothing was bound for its call.
NOT_GRANTED = (
    "refused: the agent that called this tool may not run commands itself, so neither may "
    "its tools"
)


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
