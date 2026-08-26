"""Applying a request's capabilities to the agent that runs it.

Named `narrowing`, not `capabilities`: `domain/capabilities.py` is the value
object a caller passes, and this is the machinery that enforces it. Two files
with one name across two layers made every import a small act of guessing.

It was `scoping`, which satisfied that rule and broke another. "Narrow" is the
word the domain uses for this and uses everywhere -- `narrowed()` is the
function, `intersect` says "narrow these capabilities by another set", and
`Capabilities` calls itself the narrowing axis. "Scope" appeared here and
nowhere else in this sense: every other use in the package means a lifetime
(`session-scoped`, `turn-scoped`) or a Python scope, and `backend` already says
a permission is "scoped to a route" about a different mechanism entirely. One
operation, one word, and the domain had already chosen it.

Three middleware, because the three restrictions bite in different places:

`ToolAllowlist` works at two layers, and it needs both. Filtering
`ModelRequest.tools` stops the tool being *offered*; on its own that is not a
boundary, which a live run against MiniMax-M3 demonstrated — the model called
`execute` anyway, from memory, because the system prompt still describes the
shell, and `ToolNode` ran it because the tool was still registered there.
Refusing the call in `wrap_tool_call` is what actually holds. The filter is kept
alongside it so the model is not tempted in the first place, and so its context
is not spent on tool schemas it may not use.

`NarrowedSkills` filters what the skills index advertises. Note what that is and
is not: removing a skill from the listing means the agent is not *told* about
it. The file is still on disk, so this is guidance, not a boundary — the
boundary comes from the deny rules `build_agent` adds alongside it, and even
those are bypassable by `execute`. Stated plainly rather than implied, because
a guarantee that quietly is not one is worse than none.

`DeclaredDelegatesOnly` refuses `task` to a delegate the request did not
declare. It is here rather than beside the subagent machinery because the thing
it closes is a capability hole: deepagents supplies a `general-purpose` delegate
carrying the main agent's tools and none of kingfisher's middleware, so a
request that withheld `execute` could ask that one for it.

`HostPathGuard` used to be here too, and is not, because it applies no
capability. It turns a rejected host path into a `ToolMessage` the model can act
on, and it now sits in `backend`, beside the `reject_host_path` that raises what
it catches -- one mechanism in one file rather than two halves that never
mention each other.
"""

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

from kingfisher.infrastructure.harness.skill_registry import KEY, qualified


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

    `subject` names *whose* surface this is, which on a delegate is not the
    request. A delegate's allowlist is its own definition narrowed by the
    request, and "this request" on a delegate names the one layer that is
    usually innocent: `extractor` ships `builtin_tools: [read_file, ls, glob,
    grep]`, so a run that narrowed nothing still read `execute is not available
    for this request` -- and the flags on the command line were the first place
    looked. Same lesson as the `subagent_type` typo below: naming the wrong
    cause costs more than naming none.

    Who, and deliberately not why. The allowlist is the intersection of two
    layers by the time it arrives here, and a name can be missing from either
    or both -- the middleware holds the result, not the two sides, and cannot
    tell which one dropped it without knowing whether the name is a built-in or
    a workspace tool, which it also does not know. So it names the wall rather
    than guessing at who built it, and that sentence is true in both cases.
    """

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
    """A skills index restricted to the skills a request activated.

    Overrides the formatting seam rather than rewriting the assembled prompt:
    string surgery on a system message would break the moment deepagents
    changes its wording.

    And overrides the *loading* seam, which is a bigger claim and needs its
    reason. deepagents merges every source into a dictionary keyed by name, so
    two parties who never met and both shipped a `lookup` end up as one skill
    and the model is never told the other exists. Skills arriving from several
    places is what a catalogue looks like after long enough, so the merge is
    undone here and both survive -- addressed by `source::name`, which is what a
    request grants and what `_allowed` holds.

    A skill can survive that where a *tool* cannot, and the difference is how
    each is reached: a tool is called by name through a dictionary, so two can
    never coexist whatever anyone writes; a skill is read by the path the
    listing hands the model, so two only need telling apart.

    Both loaders are overridden, and that is not belt-and-braces. `before_agent`
    and `abefore_agent` do not delegate -- each builds its own
    `dict[str, SkillMetadata]` -- so overriding one would leave a synchronous
    run and an `astream` run offering different skills, and it would fail *open*:
    the async path would keep silently dropping one. `LocalShellBackend` is the
    opposite case and worth not confusing with this one; its `aexecute` is the
    protocol default, `await asyncio.to_thread(self.execute, ...)`, so
    `ConfinedLocalShellBackend` overriding `execute` alone genuinely covers both.
    """

    def __init__(self, *, allowed: tuple[str, ...], **kwargs: Any) -> None:
        self._allowed = set(allowed)
        super().__init__(**kwargs)

    def _qualified(self) -> list[Any]:
        """Every skill every source offers, tagged with the source it came from.

        The listing deepagents would have built, minus the merge. Each entry
        keeps its own metadata -- including `path`, which is what the model is
        told to read and therefore what makes two same-named skills two
        different things rather than one ambiguous one.
        """
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
