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
    arguments, and one wrong path ends the run.

    A workspace tool's own exception is converted from here but not *at* here: that is
    `WorkspaceToolFailureMiddleware` below, which runs under whatever a deployment's
    definitions add. Read the two together -- they answer alike and `GuardedTool` answers
    alike again -- and see that class for why one of them is somewhere else.
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


class WorkspaceToolFailureMiddleware(AgentMiddleware):
    """What a workspace tool itself raised, as a failed tool result, so the turn goes on.
    `GuardedTool` answers a compiled subagent's the same way, and so must this.

    Innermost of every graph, under whatever a deployment's definitions add, because it
    cannot tell a workspace tool's exception from one raised above it. Sitting outside
    them, it reported a deployment's middleware falling over as the tool's own failure --
    the tool's name on the failed message -- and let the turn carry on with the
    deployment's rule not applied, while the same middleware falling over on a built-in
    ended the turn. One answer for both now, and it is the loud one.
    """

    def __init__(self, names: frozenset[str]) -> None:
        super().__init__()
        self.names = names

    def _failed(self, request: Any, exc: Exception) -> ToolMessage | None:
        call = request.tool_call
        if call.get("name") not in self.names:
            return None
        # The type as well as the message, and ahead of the host-path wording a built-in
        # gets: a workspace tool's exceptions were not written for a model, and
        # `FileNotFoundError: /inputs/x.csv` reads better than the path alone.
        if isinstance(exc, ToolException):
            # The one exception a tool writes for the model.
            return failed(call, str(exc))
        return failed(call, f"Error: {type(exc).__name__}: {exc}")

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        try:
            return handler(request)
        except Exception as exc:
            if (result := self._failed(request, exc)) is None:
                raise
            return result

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        try:
            return await handler(request)
        except Exception as exc:
            if (result := self._failed(request, exc)) is None:
                raise
            return result
