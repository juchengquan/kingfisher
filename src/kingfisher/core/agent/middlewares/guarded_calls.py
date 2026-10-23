"""An offered tool call's paths made real, and a failed call handed back so the turn goes on."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langchain_core.tools import ToolException

from kingfisher.core.agent.host_paths import HostPathError
from kingfisher.core.agent.references import UnsafeReferenceError
from kingfisher.core.agent.session_paths import (
    SessionPaths,
    as_asked,
    refusal_text,
    where_to_write,
)
from kingfisher.core.definitions.subagents import TASK_TOOL
from kingfisher.core.session.runtime import unavailable

from .results import answer_in, answered, failed


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
    """Translate an offered tool call's paths into the session's real ones, and turn what
    running any call raised into a failed result: a host path refused on any tool, and a
    subagent's model refusing its `task`.

    `GuardedTool` does the same two things where no middleware reaches, on a compiled
    subagent's tools and a call from `eval`'s code, and the two must answer alike.

    The translation is a leak closed as well as a convenience: a tool wanting host paths
    sends the agent looking for them, and from there it can name any session's files. The
    call is rewritten rather than each tool wrapped, since `BaseTool`s and plain functions
    share only the call.

    What running the call raised, not a call another middleware refused before it ran:
    that is the refusing middleware's to answer. A built-in's other failures are reported
    already and the model carries on; without this, upstream re-raises anything but bad
    arguments, and one wrong path ends the run.

    An offered tool's own exception is `OfferedToolFailureMiddleware`'s, which must sit
    innermost.
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

    def _told_to_the_model(self, request: Any, result: Any) -> Any:
        """`result` with the paths a failure quotes spelled as the call spelled them, and
        with where a write may go where a deny rule refused one.

        Only a failure, which is two rules in one and holds for both rewrites. A file read
        whole is content, and a file whose text says `/notes.txt` is not a tool talking
        about a path. And the shell keeps its own spelling: its paths are the host's by
        design, with `KINGFISHER_SESSION_ROOT` exported to it, and a command's own
        spelling is the one that would work again -- measured, a command that fails is a
        tool call that succeeded, `status` included, so no shell result arrives here at
        all. Nor would `as_asked` touch one: a `command` is no argument naming a path,
        though `<python> /scratchpad/missing.py` ends with the path its own error quotes.
        """
        call = request.tool_call
        answer = answer_in(result, call.get("id"))
        if answer is None or answer.status != "error":
            return result
        text = str(answer.text)
        said = where_to_write(as_asked(text, call.get("args") or {}), self.paths)
        if said == text:
            return result
        return answered(result, answer, answer.model_copy(update={"content": said}))

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        # Two catches, the first around the translation alone: a built-in's host-path
        # refusal caught there would read as the translation's rule.
        try:
            translated = self._translated(request)
        except (UnsafeReferenceError, HostPathError) as escaped:
            return self._refusal(request, escaped)
        try:
            return self._told_to_the_model(request, handler(translated))
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
            return self._told_to_the_model(request, await handler(translated))
        except Exception as exc:
            if (result := self._failed(request, exc)) is None:
                raise
            return result


class OfferedToolFailureMiddleware(AgentMiddleware):
    """What an offered tool itself raised, as a failed tool result, so the turn goes on.
    `GuardedTool` answers a compiled subagent's and `eval`'s the same way, and so must this.

    Innermost of every graph, under whatever a deployment's definitions add, because it
    cannot tell an offered tool's exception from one raised above it.
    """

    def __init__(self, names: frozenset[str]) -> None:
        super().__init__()
        self.names = names

    def _failed(self, request: Any, exc: Exception) -> ToolMessage | None:
        call = request.tool_call
        if call.get("name") not in self.names:
            return None
        # The type as well as the message, and ahead of the host-path wording a built-in
        # gets: an offered tool's exceptions were not written for a model, and
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
