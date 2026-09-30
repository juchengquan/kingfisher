"""What a workspace tool call is wrapped in: its paths translated, its failures reported."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from kingfisher.domain.references import UnsafeReferenceError
from kingfisher.infrastructure.harness.host_paths import HostPathError
from kingfisher.infrastructure.harness.session_paths import SessionPaths, refusal_text


class WorkspaceToolPaths(AgentMiddleware):
    """Translate the agent's own paths into real ones, per session.

    **It closes a leak and a usability bug with one change, and the second is how the
    first was found.** `system.md` teaches virtual paths and says the two views do
    not mix; the tools wanted host paths and the agent is never told one. Measured in
    a real run: the model passed `/data/config.ini` and the tool raised
    `FileNotFoundError`. The only way it could succeed was to go looking -- `pwd` in
    the shell, learn the layout -- and from there it can name *any* session:
    `line_count('/workspace/sessions/<other>/secret.txt')` returned an answer.

    Rewriting the call rather than wrapping each tool, because a graph this is
    attached to holds tools that are not alike: some are `BaseTool`s from `@tool` and
    some are plain functions. The call is the one shape they share, and langgraph
    documents the rewrite -- `{**request.tool_call, "args": {...}}`. Where there is
    no middleware to attach, `GuardedTool` pays the cost of wrapping instead.
    """

    def __init__(self, names: frozenset[str], session_dir: Path) -> None:
        self.names = names
        self.paths = SessionPaths(session_dir)
        super().__init__()

    def _translated(self, request: Any) -> Any:
        """The same call with its path arguments made real, or the request
        unchanged when it names no tool of ours."""
        call = request.tool_call
        if call.get("name") not in self.names:
            return request
        args = call.get("args") or {}
        return replace(request, tool_call={**call, "args": self.paths.translated(args)})

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        try:
            return handler(self._translated(request))
        except (UnsafeReferenceError, HostPathError) as escaped:
            return self._refusal(request, escaped)

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        try:
            return await handler(self._translated(request))
        except (UnsafeReferenceError, HostPathError) as escaped:
            return self._refusal(request, escaped)

    def _refusal(self, request: Any, escaped: ValueError) -> ToolMessage:
        """A path that climbs out, reported the way `reject_host_path` reports one."""
        call = request.tool_call
        return ToolMessage(
            content=refusal_text(escaped),
            tool_call_id=call.get("id", ""),
            name=call.get("name"),
            status="error",
        )


class WorkspaceToolErrors(AgentMiddleware):
    """Turn a workspace tool's exception into a failed tool result.

    A built-in reports its failures through `_tool_error` and the model carries on;
    `HostPathGuard` gives a rejected host path the same treatment. A workspace
    tool had neither, so upstream's default applied -- bad *arguments* are converted,
    everything else is re-raised -- and one wrong path killed a sixteen-call run.
    Measured, on one deployment: the same mistake through `read_file` cost nothing,
    and through `csv_profile` cost the run. Which of the two happened depended on the
    tool the model reached for, which the deployment cannot predict.
    """

    def __init__(self, names: frozenset[str]) -> None:
        super().__init__()
        self.names = names

    def _mine(self, request: Any) -> str | None:
        """The tool's name if this middleware speaks for it, else `None`."""
        name = request.tool_call.get("name")
        return name if name in self.names else None

    def _as_tool_error(self, request: Any, exc: Exception) -> ToolMessage:
        call = request.tool_call
        # The type as well as the message. A workspace tool is somebody else's
        # code and its exceptions were not written to be read by a model, so
        # `FileNotFoundError: /data/x.csv` reads far better than the path alone.
        return ToolMessage(
            content=f"Error: {type(exc).__name__}: {exc}",
            tool_call_id=call.get("id", ""),
            name=call.get("name"),
            status="error",
        )

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        try:
            return handler(request)
        except Exception as exc:
            if self._mine(request) is None:
                raise
            return self._as_tool_error(request, exc)

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        try:
            return await handler(request)
        except Exception as exc:
            if self._mine(request) is None:
                raise
            return self._as_tool_error(request, exc)
