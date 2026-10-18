"""A workspace tool call's paths made real, and a failed call handed back so the turn goes on."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langchain_core.tools import ToolException

from kingfisher.core.agent.host_paths import HostPathError
from kingfisher.core.agent.middlewares.results import failed
from kingfisher.core.agent.references import UnsafeReferenceError
from kingfisher.core.agent.session_paths import SessionPaths, refusal_text
from kingfisher.core.definitions.subagents import TASK_TOOL
from kingfisher.core.session.runtime import unavailable


def _handed_back(call: Any, exc: Exception) -> ToolMessage | None:
    """A subagent's refused model call, as what its parent is told.

    Raw provider errors as well as kingfisher's own: a compiled subagent runs with no
    middleware of kingfisher's in front of its model.
    """
    if call.get("name") != TASK_TOOL or (why := unavailable(exc)) is None:
        return None
    whose = (call.get("args") or {}).get("subagent_type") or "the subagent"
    return failed(
        call,
        f"Error: {whose!r} could not reach its model ({why}). Go on without it, or "
        "say in your answer that this part could not be done.",
    )


# The other half of `reject_host_path`.
class GuardedCallsMiddleware(AgentMiddleware):
    """Translate a workspace tool call's paths into the session's real ones, and turn what
    running any call raised into a failed result: anything a workspace tool raises, a host
    path refused on any tool, and a subagent's model refusing its `task`.

    `GuardedTool` does the same two things on a compiled subagent's tools, which no
    middleware reaches, and the two must answer alike.

    The translation is a leak closed as well as a convenience: a tool wanting host paths
    sends the agent looking for them, and from there it can name any session's files. The
    call is rewritten rather than each tool wrapped, since `BaseTool`s and plain functions
    share only the call.

    What running the call raised, not a call another middleware refused before it ran:
    that is the refusing middleware's to answer. A built-in's other failures are reported
    already and the model carries on; without this, upstream re-raises anything but bad
    arguments, and one wrong path ends the run. It cannot tell a workspace tool's
    exception from one raised below it, so a deployment middleware, appended last and
    nearest the tool, that raises on a workspace tool's call becomes a failed result too.
    """

    def __init__(
        self, names: frozenset[str] = frozenset(), paths: SessionPaths | None = None
    ) -> None:
        super().__init__()
        self.names = names
        #: `None` where a build has no session to translate against; failures still convert.
        self.paths = paths

    def _translated(self, request: Any) -> Any:
        """The same call with its path arguments made real, or the request unchanged when
        it names no tool of ours or there is no session to translate against."""
        call = request.tool_call
        paths = self.paths
        if paths is None or call.get("name") not in self.names:
            return request
        args = call.get("args") or {}
        return replace(request, tool_call={**call, "args": paths.translated(args)})

    def _refusal(self, request: Any, escaped: ValueError) -> ToolMessage:
        """A path that climbs out, reported the way `reject_host_path` reports one."""
        return failed(request.tool_call, refusal_text(escaped, self.paths))

    def _failed(self, request: Any, exc: Exception) -> ToolMessage | None:
        """The result to hand back, or `None` when the exception is not this one's."""
        call = request.tool_call
        # Ahead of the host-path case: a workspace tool's exceptions were not written for
        # a model, and `FileNotFoundError: /inputs/x.csv` reads better than the path.
        if call.get("name") in self.names:
            # The one exception a tool writes for the model.
            if isinstance(exc, ToolException):
                return failed(call, str(exc))
            return failed(call, f"Error: {type(exc).__name__}: {exc}")
        if isinstance(exc, HostPathError):
            return failed(call, f"Error: {exc}")
        return _handed_back(call, exc)

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        # Two catches, the first around the translation alone: a built-in's host-path
        # refusal caught there would read as the translation's rule.
        try:
            translated = self._translated(request)
        except (UnsafeReferenceError, HostPathError) as escaped:
            return self._refusal(request, escaped)
        try:
            return handler(translated)
        except Exception as exc:
            if (result := self._failed(request, exc)) is None:
                raise
            return result

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        try:
            translated = self._translated(request)
        except (UnsafeReferenceError, HostPathError) as escaped:
            return self._refusal(request, escaped)
        try:
            return await handler(translated)
        except Exception as exc:
            if (result := self._failed(request, exc)) is None:
                raise
            return result
