"""Applying a request's capabilities to the agent that runs it.

Named `scoping`, not `capabilities`: `domain/capabilities.py` is the value
object a caller passes, and this is the machinery that enforces it. Two files
with one name across two layers made every import a small act of guessing.

Two middleware, because the two restrictions bite in different places:

`ToolAllowlist` works at two layers, and it needs both. Filtering
`ModelRequest.tools` stops the tool being *offered*; on its own that is not a
boundary, which a live run against MiniMax-M3 demonstrated — the model called
`execute` anyway, from memory, because the system prompt still describes the
shell, and `ToolNode` ran it because the tool was still registered there.
Refusing the call in `wrap_tool_call` is what actually holds. The filter is kept
alongside it so the model is not tempted in the first place, and so its context
is not spent on tool schemas it may not use.

`ScopedSkills` filters what the skills index advertises. Note what that is and
is not: removing a skill from the listing means the agent is not *told* about
it. The file is still on disk, so this is guidance, not a boundary — the
boundary comes from the deny rules `build_agent` adds alongside it, and even
those are bypassable by `execute`. Stated plainly rather than implied, because
a guarantee that quietly is not one is worse than none.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from deepagents.middleware.skills import SkillsMiddleware
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from kingfisher.infrastructure.backend import HostPathError


class CapabilityError(ValueError):
    """A request named a tool, skill or subagent the workspace does not offer.

    Here rather than in `agent`, where it was, because `agent` and `delegation`
    both raise it and neither may import the other. This module is the machinery
    that enforces a request's capabilities, so the refusal belongs with it.
    """


def _tool_name(tool: Any) -> str | None:
    name = getattr(tool, "name", None)
    if isinstance(name, str):
        return name
    if isinstance(tool, dict):  # server-side tool definitions
        value = tool.get("name")
        return value if isinstance(value, str) else None
    return None


class ToolAllowlist(AgentMiddleware):
    """Restrict the tools the model is offered, per call.

    An unnamed tool is kept: the allowlist governs kingfisher's named tool
    surface, and silently dropping something it cannot identify would be a
    worse failure than passing it through.
    """

    def __init__(self, allowed: tuple[str, ...]) -> None:
        self._allowed = set(allowed)
        super().__init__()

    def _filter(self, request: Any) -> Any:
        kept = [
            tool
            for tool in request.tools
            if (name := _tool_name(tool)) is None or name in self._allowed
        ]
        # `override` rather than assigning `request.tools`: langchain deprecated
        # mutating a ModelRequest in place.
        return request.override(tools=kept)

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return handler(self._filter(request))

    async def awrap_model_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        return await handler(self._filter(request))

    def _refuse(self, request: Any) -> ToolMessage | None:
        """A `ToolMessage` when the call must not run, `None` when it may.

        Refusing as an error message rather than raising: the agent gets to see
        that it reached for something it does not have and pick another route,
        where an exception would end the run.
        """
        call = request.tool_call
        name = call.get("name")
        if name in self._allowed:
            return None
        return ToolMessage(
            content=(
                f"Error: {name} is not available for this request. "
                f"Available tools: {', '.join(sorted(self._allowed))}."
            ),
            tool_call_id=call.get("id", ""),
            name=name,
            status="error",
        )

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return self._refuse(request) or handler(request)

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        refusal = self._refuse(request)
        return refusal if refusal is not None else await handler(request)


class ScopedSkills(SkillsMiddleware):
    """A skills index restricted to the skills a request activated.

    Overrides the formatting seam rather than rewriting the assembled prompt:
    string surgery on a system message would break the moment deepagents
    changes its wording.
    """

    def __init__(self, *, allowed: tuple[str, ...], **kwargs: Any) -> None:
        self._allowed = set(allowed)
        super().__init__(**kwargs)

    def _format_skills_list(self, skills: list[Any]) -> str:
        return super()._format_skills_list(
            [s for s in skills if s.get("name") in self._allowed]
        )


class HostPathGuard(AgentMiddleware):
    """Turn a rejected host path back into something the agent can act on.

    `reject_host_path` exists to correct the model mid-turn -- its message
    names the virtual path to use instead. But it raises from inside the
    backend, and deepagents' file tools only convert `ValueError` raised during
    *path validation*; `backend.write()` is called outside that guard. So the
    exception escaped the tool, escaped the graph, and killed the run. The
    message meant to teach the model never reached it.

    Returning it as a failed `ToolMessage` is what makes the correction work,
    exactly as `ToolAllowlist` does for a tool the request did not activate.
    Only `HostPathError` is caught: a middleware that swallowed every
    `ValueError` would hide real faults behind a retry.
    """

    def _as_tool_error(self, request: Any, exc: HostPathError) -> ToolMessage:
        call = request.tool_call
        return ToolMessage(
            content=f"Error: {exc}",
            tool_call_id=call.get("id", ""),
            name=call.get("name"),
            status="error",
        )

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        try:
            return handler(request)
        except HostPathError as exc:
            return self._as_tool_error(request, exc)

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        try:
            return await handler(request)
        except HostPathError as exc:
            return self._as_tool_error(request, exc)


class DeclaredDelegatesOnly(AgentMiddleware):
    """Refuse `task` to a delegate this request did not declare.

    deepagents adds a `general-purpose` subagent of its own, with "the same
    capabilities as the main agent" and none of kingfisher's middleware. It is
    there whenever `task` is, so applying the caller's tool ceiling to declared
    delegates closes only half the door: a request that withheld `execute`
    could still ask `general-purpose` for it.

    It cannot be narrowed. Declaring one by the same name *duplicates* it
    rather than replacing it, and the switch that disables it resolves by the
    model's *provider* through a beta registry -- so a model deepagents does
    not recognise silently restores the unrestricted delegate. A boundary that
    depends on provider-name inference is not a boundary.

    Refusing the call is. This is the same `wrap_tool_call` seam `ToolAllowlist`
    uses, it holds whatever model is in play, and it fails closed: a delegate
    has to be named here to be reachable.

    The built-in is still *advertised* in the task tool's description, so a
    model may try it once and be told no. Wasteful, and much preferable to the
    alternative of trusting it.
    """

    def __init__(self, declared: tuple[str, ...]) -> None:
        self._declared = set(declared)
        super().__init__()

    def _refuse(self, request: Any) -> ToolMessage | None:
        call = request.tool_call
        if call.get("name") != "task":
            return None
        wanted = (call.get("args") or {}).get("subagent_type")
        if wanted in self._declared:
            return None
        offered = ", ".join(sorted(self._declared)) or "none"
        return ToolMessage(
            content=(
                f"Error: {wanted!r} is not a delegate this request may use. "
                f"Available: {offered}."
            ),
            tool_call_id=call.get("id", ""),
            name=call.get("name"),
            status="error",
        )

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return self._refuse(request) or handler(request)

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        refusal = self._refuse(request)
        return refusal if refusal is not None else await handler(request)
