"""The tool picture one build works from, resolved once."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from kingfisher.core.access.capabilities import (
    ALL,
    Capabilities,
    CapabilityError,
    Selection,
    narrowed,
)
from kingfisher.core.access.reach import reaches
from kingfisher.core.definitions.catalogue import Definitions
from kingfisher.core.definitions.subagents import INTERPRETER_TOOL, TASK_TOOL
from kingfisher.core.definitions.subagents.spec import SubagentError
from kingfisher.core.definitions.tools.catalogue import ToolError
from kingfisher.core.definitions.tools.spec import FoundTool, Offering, out_of_reach

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from kingfisher.core.config import Config


def registered_tools(graph: Any) -> tuple[str, ...] | None:
    """Tool names the compiled agent can actually dispatch, or `None` if unreadable.

    `create_agent(model, tools=[])` compiles with no tool node at all, the shape of a
    hand-written graph that dispatches nothing. An agent graph keeps its `model` node either
    way, so a tool node missing beside one is a definite none.
    """
    nodes = getattr(graph, "nodes", None)
    if not hasattr(nodes, "get"):
        return None
    by_name = getattr(getattr(nodes.get("tools"), "bound", None), "tools_by_name", None)
    if isinstance(by_name, dict):
        return tuple(sorted(by_name))
    return () if "model" in nodes else None


def workspace_tool_names(
    cfg: Config, *, catalogue: Definitions | None = None
) -> tuple[str, ...]:
    """The tools this workspace defines, as a grant would write them."""
    found = (catalogue or Definitions.from_config(cfg)).tools.found
    return tuple(sorted(Offering.of(found).workspace))


def _refuse_shadowed(
    walked: Sequence[FoundTool], *, builtin: tuple[str, ...], where: str
) -> None:
    """What the workspace defines, refusing anything that shadows a built-in."""
    shadowed = tuple(sorted({one.name for one in walked} & set(builtin)))
    if shadowed:
        msg = (
            f"workspace tool(s) {', '.join(shadowed)} would replace a built-in of "
            f"the same name; rename them in {where}"
        )
        raise CapabilityError(msg)


@dataclass(frozen=True)
class _ToolSurface:
    """The tool picture one build works from, resolved once."""

    #: What this build has to offer, or `None` when the probe was skipped. Not an empty
    #: `Offering`, which would narrow every grant to nothing; a skipped probe narrowed nothing.
    offering: Offering | None = None
    #: What the request asked for, so the grants are derived rather than stored: a stored
    #: `ALL` cannot say whether everything was granted or nothing was narrowed.
    asked: Capabilities = field(default_factory=Capabilities)
    #: The built tool objects, by name, off the probe graph. Only a helper needs them, and
    #: `create_deep_agent` builds them where nothing else can reach.
    objects: Mapping[str, Any] = field(default_factory=dict)
    #: Every workspace tool with its file: two files may each define a `fetch`, and a name
    #: would lose one of them before any narrowing ran.
    found: tuple[FoundTool, ...] = ()
    #: Tools whose audience shuts this caller out. Taken out of the grant, not `found`: a
    #: definition naming one names a real tool, which `refuse_unknown` must not call a typo.
    out_of_reach: frozenset[str] = frozenset()
    #: Every name a call anywhere under this build can carry, bundles included -- what a gate
    #: is judged against. Not `offering.workspace`, which spells a name two files share the
    #: long way: a gate written that way was accepted and then matched nothing.
    dispatchable: frozenset[str] = frozenset()
    #: Names only a compiled subagent's own folder holds, each with whose folder it is, so a
    #: gate on one is refused for the reason it cannot fire rather than as a name nothing
    #: answers to.
    only_compiled: Mapping[str, str] = field(default_factory=dict)

    @property
    def carried(self) -> tuple[Any, ...]:
        """The tool objects this agent registers: granted, minus the ambiguous."""
        if self.offering is None:
            return tuple(one.tool for one in self.found)
        return tuple(
            one.tool for one in self.offering.carried(self.granted_workspace, self.found)
        )

    @property
    def offers(self) -> Offering:
        """The offering, or an empty one for the callers that only read names."""
        return self.offering or Offering()

    @property
    def permitted(self) -> tuple[str, ...] | None:
        """The parent's allowlist, or `None` for no restriction at all."""
        if self.offering is None:
            return None
        # The grant once a tool is taken out of it: an unrestricted request is no allowlist,
        # which would hand the tool back to the parent and every subagent it caps.
        tools = self.granted_workspace if self.out_of_reach else self.asked.tools
        return self.offering.permitted(self.asked.builtin_tools, tools)

    @property
    def granted_builtin(self) -> Selection:
        """The request's built-in grant, resolved against what was offered."""
        if self.offering is None:
            return ALL
        return narrowed(self.asked.builtin_tools, by=self.offering.builtin) or ()

    @property
    def granted_workspace(self) -> Selection:
        """The request's workspace grant, resolved against what was offered."""
        if self.offering is None:
            return ALL
        # `spelt` first: `narrowed` is set membership and would drop a long-form grant silently.
        granted = narrowed(self.offering.spelt(self.asked.tools), by=self.offering.workspace)
        return tuple(name for name in granted or () if name not in self.out_of_reach)


def bundled_tools_in(catalogue: Definitions) -> tuple[tuple[FoundTool, str, str], ...]:
    """Every tool a bundle holds that loads, with where a rename would go and whose it is.

    Every bundle the catalogue holds, not only the subagents a request activates: a name
    that replaces a built-in is a fact about the workspace, and a deployment would find
    out from whichever request came first otherwise.
    """
    # Neither failure is raised here: it would land on the catalogue's `tools` row and send
    # a reader to the wrong directory. The subagent rows and `subagent tools` name the
    # file to open, and `warm` refuses both for a service.
    try:
        bundles = catalogue.bundled_tools
    except SubagentError:
        return ()
    loaded: list[tuple[FoundTool, str, str]] = []
    for owner, repository in bundles.items():
        try:
            found = repository.found
        except ToolError:
            continue
        # A member of both readers, not a `getattr` with a default: a default answers
        # nothing for whichever reader lacks it.
        loaded.extend((one, repository.where, owner) for one in found)
    return tuple(loaded)


def handed_graphs_in(catalogue: Definitions) -> frozenset[str]:
    """The subagents whose graph arrives built, so nothing kingfisher adds reaches it.

    Guarded like the read above, and for the same reason: a definition that will not
    parse is answered by the subagent rows, not by the catalogue's `tools`.
    """
    try:
        specs = catalogue.subagents.specs
    except SubagentError:
        return frozenset()
    return frozenset(name for name, spec in specs.items() if spec.build is not None)


def _private_tools(
    catalogue: Definitions, name: str, *, held: frozenset[str] | None = None
) -> tuple[FoundTool, ...]:
    """The tools a subagent brings itself that this caller may use, or none."""
    repository = catalogue.bundled_tools.get(name)
    if repository is None:
        return ()
    # A bundle skips the request's grant and no other: a tool's own audience still applies.
    owned = tuple(
        one for one in repository.found if held is None or reaches(one.audience, held)
    )
    spec = catalogue.subagents.specs.get(name)
    if spec is None or spec.carried:
        return owned
    # Filtered here as well as refused at load, so a build that skipped `warm` still hands
    # a subagent only what its definition lists.
    listed = set(spec.bundled.get("tools", ()))
    return tuple(one for one in owned if one.name in listed)


def _tool_objects(graph: Any) -> Mapping[str, Any]:
    """The built tool objects a compiled graph dispatches, by name."""
    node = getattr(graph, "nodes", {}).get("tools")
    by_name = getattr(getattr(node, "bound", None), "tools_by_name", None)
    if not isinstance(by_name, dict):
        return {}
    # Not the probe's `eval`, never narrowed: a helper holding it would reach `task()` past
    # its allowlist, and it would outlive the turn, as `release_interpreter` closes only the
    # agent's.
    left_out = {TASK_TOOL, INTERPRETER_TOOL}
    return {name: tool for name, tool in by_name.items() if name not in left_out}


def _resolve_tools(  # noqa: PLR0913 -- who is asking is passed in: only the composition root knows
    where: str,
    capabilities: Capabilities,
    workspace_tools: Sequence[FoundTool],
    # `Any`: naming the record it returns would import `core.agent.assembly`, which imports
    # this module.
    assemble: Callable[[tuple[Any, ...]], Any],
    *,
    #: What `bundled_tools_in` loaded, so the probe judges a subagent's own tools too.
    bundled: Sequence[tuple[FoundTool, str, str]] = (),
    #: What `handed_graphs_in` found, so a gate is not offered a graph it cannot reach.
    handed: frozenset[str] = frozenset(),
    names_needed: bool = False,
    #: What a refusal may list, for this caller.
    in_reach: Callable[[str], bool] | None = None,
    held: frozenset[str] | None = None,
) -> _ToolSurface:
    """What this request may call, and what this agent offers at all.

    `names_needed` asks for the probe a definition naming tools needs, to check them against
    the built-ins; it costs a compile, so a caller asks only when one does.
    """
    unrestricted = capabilities.builtin_tools == ALL and capabilities.tools == ALL
    if not names_needed and not workspace_tools and not bundled and unrestricted:
        return _ToolSurface()

    probe = assemble(()).graph
    # `None` is unreachable on our own probe; `or ()` keeps an upstream shape change from
    # crashing here.
    builtin = registered_tools(probe) or ()
    _refuse_shadowed(workspace_tools, builtin=builtin, where=where)
    for found, holder, _ in bundled:
        _refuse_shadowed((found,), builtin=builtin, where=holder)
    offering = Offering.of(workspace_tools, builtin=builtin)
    offering.refuse_unknown(
        capabilities.builtin_tools,
        capabilities.tools,
        subject="this request",
        in_reach=in_reach,
    )
    dispatchable = (
        frozenset(builtin)
        | {one.name for one in workspace_tools}
        # Left out where the bundle is a compiled subagent's: deepagents hands a graph it
        # did not build none of this agent's gates, so a gate on a name nothing else holds
        # would be accepted and never fire -- the outcome gates are refused to prevent.
        | {one.name for one, _, owner in bundled if owner not in handed}
    )
    return _ToolSurface(
        offering=offering,
        asked=capabilities,
        objects=_tool_objects(probe),
        found=tuple(workspace_tools),
        out_of_reach=out_of_reach(workspace_tools, held),
        dispatchable=dispatchable,
        only_compiled={
            one.name: owner
            for one, _, owner in bundled
            if owner in handed and one.name not in dispatchable
        },
    )
