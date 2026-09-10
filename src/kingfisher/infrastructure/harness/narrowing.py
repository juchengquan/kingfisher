"""Applying a request's capabilities to the agent that runs it."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from deepagents.middleware.skills import (
    SkillsMiddleware,
    _alist_skills_with_errors,
    _list_skills_with_errors,
)
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig

from kingfisher.kinds.skills.registry import KEY, qualified


def _tool_name(tool: Any) -> str | None:
    name = getattr(tool, "name", None)
    if isinstance(name, str):
        return name
    if isinstance(tool, dict):  # server-side tool definitions
        value = tool.get("name")
        return value if isinstance(value, str) else None
    return None


class ToolAllowlist(AgentMiddleware):
    """Restrict the tools the model is offered, per call."""

    def __init__(self, allowed: tuple[str, ...], *, subject: str = "this request") -> None:
        self._allowed = set(allowed)
        self._subject = subject
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
        """A `ToolMessage` when the call must not run, `None` when it may."""
        call = request.tool_call
        name = call.get("name")
        if name in self._allowed:
            return None
        return ToolMessage(
            content=(
                f"Error: {name} is not available for {self._subject}. "
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


class NarrowedSkills(SkillsMiddleware):
    """A skills index restricted to the skills a request activated."""

    def __init__(self, *, allowed: tuple[str, ...], **kwargs: Any) -> None:
        self._allowed = set(allowed)
        super().__init__(**kwargs)

    def _qualified(self) -> list[Any]:
        """Every skill every source offers, tagged with the source it came from."""
        found = []
        for label, path in zip(self.source_labels, self.sources, strict=True):
            skills, _error = _list_skills_with_errors(self._backend, path)
            for one in skills:
                found.append({**one, KEY: qualified(label, one["name"])})
        return found

    async def _aqualified(self) -> list[Any]:
        """The same, on the path `astream` takes. See the class docstring."""
        found = []
        for label, path in zip(self.source_labels, self.sources, strict=True):
            skills, _error = await _alist_skills_with_errors(self._backend, path)
            for one in skills:
                found.append({**one, KEY: qualified(label, one["name"])})
        return found

    # `config` is annotated rather than left as `Any`, and langchain is why: it
    # inspects every hook's signature at construction and warns when this
    # parameter is not a `RunnableConfig`. Nothing here reads it -- deepagents'
    # own hooks carry the same unused argument -- but a warning per agent built
    # is a warning nobody reads by the tenth one.
    def before_agent(self, state: Any, runtime: Any, config: RunnableConfig) -> Any:  # noqa: ARG002
        if "skills_metadata" in state:
            return None
        return {"skills_metadata": self._qualified()}

    async def abefore_agent(self, state: Any, runtime: Any, config: RunnableConfig) -> Any:  # noqa: ARG002
        if "skills_metadata" in state:
            return None
        return {"skills_metadata": await self._aqualified()}

    def _format_skills_list(self, skills: list[Any]) -> str:
        return super()._format_skills_list(
            [s for s in skills if s.get(KEY, s.get("name")) in self._allowed]
        )


class DeclaredDelegatesOnly(AgentMiddleware):
    """Refuse `task` to a delegate this request did not declare."""

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
        # A missing argument is a different mistake from a refused name, and
        # saying the wrong one costs the whole turn. Observed live: a model
        # sent `subagentType`, read back "None is not a delegate this request
        # may use. Available: ..., reviewer, ...", reported the tool as broken
        # "despite listing reviewer as available", and answered around it
        # rather than retrying. The name it could not find was its own typo.
        detail = (
            "no subagent_type was given -- the argument is `subagent_type`"
            if wanted is None
            else f"{wanted!r} is not a delegate this request may use"
        )
        return ToolMessage(
            content=f"Error: {detail}. Available: {offered}.",
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
