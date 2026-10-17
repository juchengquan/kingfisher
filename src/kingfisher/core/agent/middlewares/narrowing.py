"""Applying a request's capabilities to the agent that runs it."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from deepagents.middleware.skills import SkillsMiddleware
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig

from kingfisher.core.agent.middlewares.results import failed
from kingfisher.core.definitions.skills.registry import KEY, alist_skills, list_skills, qualified


def _tool_name(tool: Any) -> str | None:
    name = getattr(tool, "name", None)
    if isinstance(name, str):
        return name
    if isinstance(tool, dict):  # server-side tool definitions
        value = tool.get("name")
        return value if isinstance(value, str) else None
    return None


class GrantedOnlyMiddleware(AgentMiddleware):
    """Offer and run only the tools a request was granted, and through `task` only the
    subagents it declared; `None` for either narrows nothing."""

    def __init__(
        self,
        tools: tuple[str, ...] | None = None,
        *,
        subagents: tuple[str, ...] | None = None,
        subject: str = "this request",
    ) -> None:
        self._allowed = None if tools is None else set(tools)
        self._declared = None if subagents is None else set(subagents)
        self._subject = subject
        super().__init__()

    def _filter(self, request: Any) -> Any:
        if self._allowed is None:
            return request
        kept = [
            tool
            for tool in request.tools
            if (name := _tool_name(tool)) is None or name in self._allowed
        ]
        # `override`: langchain deprecated mutating a ModelRequest in place.
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
        if self._allowed is not None and name not in self._allowed:
            return failed(
                call,
                f"Error: {name} is not available for {self._subject}. "
                f"Available tools: {', '.join(sorted(self._allowed))}.",
            )
        if name != "task" or self._declared is None:
            return None
        wanted = (call.get("args") or {}).get("subagent_type")
        if wanted in self._declared:
            return None
        offered = ", ".join(sorted(self._declared)) or "none"
        # A missing argument is a different mistake from a refused name: told the name was
        # refused, a live model that had sent `subagentType` called the tool broken and gave up.
        detail = (
            "no subagent_type was given -- the argument is `subagent_type`"
            if wanted is None
            else f"{wanted!r} is not a subagent this request may use"
        )
        return failed(call, f"Error: {detail}. Available: {offered}.")

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return self._refuse(request) or handler(request)

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        refusal = self._refuse(request)
        return refusal if refusal is not None else await handler(request)


class NarrowedSkillsMiddleware(SkillsMiddleware):
    """A skills index restricted to the skills a request activated."""

    def __init__(self, *, allowed: tuple[str, ...], **kwargs: Any) -> None:
        self._allowed = set(allowed)
        super().__init__(**kwargs)

    def _qualified(self) -> list[Any]:
        """Every skill every source offers, tagged with the source it came from."""
        found = []
        for label, path in zip(self.source_labels, self.sources, strict=True):
            for one in list_skills(self._backend, path):
                found.append({**one, KEY: qualified(label, one["name"])})
        return found

    async def _aqualified(self) -> list[Any]:
        """The same, for a graph run on an event loop."""
        found = []
        for label, path in zip(self.source_labels, self.sources, strict=True):
            for one in await alist_skills(self._backend, path):
                found.append({**one, KEY: qualified(label, one["name"])})
        return found

    # `config` is unread but annotated: langchain warns, per agent built, on a hook whose
    # parameter is not a `RunnableConfig`.
    # `is not None`, as deepagents asks, not `in`: `None` in this input channel requests a
    # reload, and read as "already loaded" it hands an empty index to a deployment driving
    # the graph itself.
    def before_agent(self, state: Any, runtime: Any, config: RunnableConfig) -> Any:  # noqa: ARG002
        if state.get("skills_metadata") is not None:
            return None
        return {"skills_metadata": self._qualified()}

    async def abefore_agent(self, state: Any, runtime: Any, config: RunnableConfig) -> Any:  # noqa: ARG002
        if state.get("skills_metadata") is not None:
            return None
        return {"skills_metadata": await self._aqualified()}

    def _format_skills_list(self, skills: list[Any]) -> str:
        return super()._format_skills_list(
            [s for s in skills if s.get(KEY, s.get("name")) in self._allowed]
        )
