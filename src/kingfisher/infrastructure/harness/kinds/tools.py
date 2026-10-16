"""The tool picture one build works from, resolved once."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from kingfisher.core.definitions.catalogue import Definitions
from kingfisher.core.definitions.subagents import INTERPRETER_TOOL, TASK_TOOL
from kingfisher.core.definitions.tools.spec import Found, Offering, out_of_reach
from kingfisher.domain.access import reaches
from kingfisher.domain.capabilities import (
    ALL,
    Capabilities,
    CapabilityError,
    Selection,
    narrowed,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from kingfisher.config import Config


def registered_tools(graph: Any) -> tuple[str, ...] | None:
    """Tool names the compiled agent can actually dispatch, or `None` if unreadable.

    Telling them apart takes a second look, because the obvious one does not work:
    `create_agent(model, tools=[])` compiles to `['__start__', 'model']` with **no
    tool node at all**, which is exactly the shape of a hand-written graph that
    dispatches nothing. Measured, not assumed. What separates them is the `model`
    node -- an agent graph keeps one whether or not it has tools, so a tool node
    missing from *that* shape is a definite none, and anything else is a shape with
    no answer in it.
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
    walked: Sequence[Found], *, builtin: tuple[str, ...], where: str
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

    #: What this build has to offer, or `None` when the probe was skipped.
    #:
    #: `None` rather than an empty `Offering`, because the two mean opposite
    #: things: nothing offered would narrow every grant to nothing, while a
    #: skipped probe means nothing was ever narrowed. The probe is only skipped
    #: when no definition names a tool either, so nothing below is ever asked to
    #: enumerate what it does not know.
    offering: Offering | None = None
    #: What the request asked for. Held so the grants can be *derived* rather
    #: than stored beside what they came from -- which is what this dataclass
    #: used to do, and it needed an `unrestricted` flag to compensate, because a
    #: stored `ALL` could no longer say whether everything was granted or
    #: nothing was narrowed. `permitted` answers that from the request directly.
    asked: Capabilities = field(default_factory=Capabilities)
    #: The built tool *objects*, by name, taken off the probe graph. Needed
    #: only for a helper: `SubAgentMiddleware` registers what a spec carries,
    #: and these are constructed from the backend inside `create_deep_agent`
    #: where nothing here can reach them -- except off an assembled graph,
    #: which is what the probe already is.
    objects: Mapping[str, Any] = field(default_factory=dict)
    #: Every workspace tool with the file it came from, kept because a grant no
    #: longer resolves to a name. Two files may each define a `fetch`, so what
    #: an agent registers has to be chosen as *objects* -- a name would pick one
    #: of the two out of a dictionary and lose the other before any narrowing
    #: ran.
    found: tuple[Found, ...] = ()
    #: Tools whose own audience shuts this caller out, as a grant writes them. Taken
    #: out of the grant itself rather than out of `found`: a definition naming one is
    #: still naming a real tool, and `refuse_unknown` must not call it a typo.
    out_of_reach: frozenset[str] = frozenset()

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
        # The grant, not the request, once a tool has been taken out of it: an
        # unrestricted request is otherwise "no allowlist", and that would put the
        # tool straight back for the parent and for every subagent it caps.
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
        # `spelt` first, for the reason it exists: `narrowed` is set membership
        # and would drop a long-form grant without a word.
        granted = narrowed(self.offering.spelt(self.asked.tools), by=self.offering.workspace)
        return tuple(name for name in granted or () if name not in self.out_of_reach)


def _private_tools(
    catalogue: Definitions, name: str, *, held: frozenset[str] | None = None
) -> tuple[Found, ...]:
    """The tools a subagent brings itself that this caller may use, or none."""
    repository = catalogue.bundled_tools.get(name)
    if repository is None:
        return ()
    # A bundle is held whatever the request granted, and that is the only grant it
    # skips: a carried tool's own audience still shuts out a caller it does not admit.
    owned = tuple(
        one for one in repository.found if held is None or reaches(one.audience, held)
    )
    spec = catalogue.subagents.specs.get(name)
    if spec is None or spec.carried:
        return owned
    # Filtered here as well as refused at load, so a build that skipped `warm` still
    # hands a subagent only what its definition lists from the folder.
    listed = set(spec.bundled.get("tools", ()))
    return tuple(one for one in owned if one.name in listed)


def _tool_objects(graph: Any) -> Mapping[str, Any]:
    """The built tool objects a compiled graph dispatches, by name."""
    node = getattr(graph, "nodes", {}).get("tools")
    by_name = getattr(getattr(node, "bound", None), "tools_by_name", None)
    if not isinstance(by_name, dict):
        return {}
    # `eval` here is the probe's own sandbox, wired before the grant is known and
    # never narrowed: a helper holding it would reach `task()` past its own
    # allowlist, and its runtime would outlive the turn, since `release_interpreter`
    # closes only the agent's. Without it a nested helper holds what a top-level
    # subagent does.
    left_out = {TASK_TOOL, INTERPRETER_TOOL}
    return {name: tool for name, tool in by_name.items() if name not in left_out}


def _resolve_tools(  # noqa: PLR0913 -- the sixth is who is asking, which nothing
    # here can derive and the composition root already knows.
    # Separate parameters rather than one bundle: folding them up would hide
    # what each one is for.
    where: str,
    capabilities: Capabilities,
    workspace_tools: Sequence[Found],
    # `Any` rather than the record it actually returns: naming that type here would
    # make this module import `infrastructure.harness.agent`, which imports this one
    # -- the cycle `test_no_module_in_the_package_can_reach_itself` refuses. The
    # probe takes the graph off it below, which is all this function wants.
    assemble: Callable[[tuple[Any, ...]], Any],
    *,
    names_needed: bool = False,
    #: What a refusal may list, where there is a caller to narrow it for. Passed in
    #: rather than worked out here: who is asking is the composition root's to know.
    in_reach: Callable[[str], bool] | None = None,
    held: frozenset[str] | None = None,
) -> _ToolSurface:
    """What this request may call, and what this agent offers at all.

    `names_needed` is the fourth job: a *definition* naming tools has to be checked
    against what exists, and what exists includes the built-in set. A caller asks for
    it only when some activated definition actually names one. Measured on a build
    with one subagent: 1 compile and 12.6ms when it names no tools, 2 and 20.3ms when
    it does -- so the probe is the ~7.7ms, and the runs that never needed it still
    skip it.
    """
    unrestricted = capabilities.builtin_tools == ALL and capabilities.tools == ALL
    if not names_needed and not workspace_tools and unrestricted:
        return _ToolSurface()

    probe = assemble(()).graph
    # Our own probe, so `None` is not reachable here; `or ()` keeps a shape
    # change upstream from becoming a crash at the one site that would.
    builtin = registered_tools(probe) or ()
    _refuse_shadowed(workspace_tools, builtin=builtin, where=where)
    offering = Offering.of(workspace_tools, builtin=builtin)
    offering.refuse_unknown(
        capabilities.builtin_tools,
        capabilities.tools,
        subject="this request",
        in_reach=in_reach,
    )
    return _ToolSurface(
        offering=offering,
        asked=capabilities,
        objects=_tool_objects(probe),
        found=tuple(workspace_tools),
        out_of_reach=out_of_reach(workspace_tools, held),
    )
