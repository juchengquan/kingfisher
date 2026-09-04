"""The tool picture one build works from, resolved once.

What the workspace defines, what a request may call, what a delegate keeps to
itself, and what the compiled graph can actually dispatch. Four questions with
one answer between them, which is why `_ToolSurface` exists rather than four
functions each walking the catalogue again.

Apart from assembly because it is a *resolution*, not a step: it reads the
catalogue and the grant and produces names, and knows nothing about graphs,
middleware or models. `build_agent` asks it once and wires what comes back.

`registered_tools` is the one that looks out of place and is not. It reads names
off a graph that has already been built, which is the same subject from the
far end -- what the surface promised, checked against what the agent can
dispatch. The withheld report asks it precisely because a grant is a claim about
the surface and the graph is the fact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from kingfisher.domain.capabilities import (
    ALL,
    Capabilities,
    CapabilityError,
    Selection,
    narrowed,
)
from kingfisher.infrastructure.catalogue import Definitions
from kingfisher.infrastructure.harness.delegation import TASK_TOOL
from kingfisher.tools.spec import Found, Offering

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from langgraph.graph.state import CompiledStateGraph

    from kingfisher.config import Config


#: One entry of a deployment's middleware registry.
#:
#: `Callable` is imported at run time for this line, rather than under
#: `TYPE_CHECKING` with `Mapping` and `Sequence`: this is a module-level
#: assignment rather than an annotation, and `from __future__ import
#: annotations` makes annotations strings while doing nothing for a value.
#:
#: Two shapes, and the ellipsis is the honest way to say so. An entry may be a
#: zero-argument factory, which is what a registry has held since before
#: settings existed; or a class, which `_instantiate` calls with its own
def registered_tools(graph: Any) -> tuple[str, ...] | None:
    """Tool names the compiled agent can actually dispatch, or `None` if unreadable.

    Derived from the graph rather than listed here, because a hardcoded list
    would drift the first time deepagents adds or renames a tool. The path into
    the tool node is not a public contract, so a shape we do not recognise is
    reported rather than raised: taking down every build over an introspection
    detail would be the worse trade, and a rename upstream is meant to fail
    `test_a_real_build_is_readable` instead.

    It used to answer `()` for both "no tools" and "cannot read this", and said
    so -- callers "read [it] as *cannot check*". They were the same answer
    because nothing needed them apart: every graph here is one `build_agent`
    made, and those always dispatch something.

    A compiled subagent is the first graph kingfisher will be handed rather than
    have built, and there the difference is the whole point -- a listing that
    prints "no tools" for a graph it could not read has stated a fact it does
    not have. So `()` now means none, and `None` means unreadable.

    Telling them apart takes a second look, because the obvious one does not
    work: `create_agent(model, tools=[])` compiles to `['__start__', 'model']`
    with **no tool node at all**, which is exactly the shape of a hand-written
    graph that dispatches nothing. Measured, not assumed. What separates them is
    the `model` node -- an agent graph keeps one whether or not it has tools, so
    a tool node missing from *that* shape is a definite none, and anything else
    is a shape with no answer in it.
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
    """The tools this workspace defines, as a grant would write them.

    Knowable off disk, unlike the built-in set. That asymmetry is why the two
    axes resolve differently and why only one of them needs a probe.

    Written forms rather than bare names, because a bare list said `fetch,
    fetch` once two files could each define one -- which read as a workspace
    with a stutter rather than two tools a grant has to choose between.
    """
    found = (catalogue or Definitions.from_config(cfg)).tools.found
    return tuple(sorted(Offering.of(found).workspace))


def _refuse_shadowed(
    walked: Sequence[Found], *, builtin: tuple[str, ...], where: str
) -> None:
    """What the workspace defines, refusing anything that shadows a built-in.

    `tools_by_name` is a dict, so a workspace tool called `read_file` would take
    the name in silence and the real one would simply stop existing -- the same
    "quietly different from what you asked for" failure the capability checks
    refuse elsewhere. It matters more now that the two are granted separately: a
    shadowed name would be permitted by one axis and enforced as the other.

    Takes where they came from as text rather than the `Config` it used to
    derive a directory from: the only use is naming the place to go and rename
    them, and a catalogue that is not a directory can still say where it is.
    """
    shadowed = tuple(sorted({one.name for one in walked} & set(builtin)))
    if shadowed:
        msg = (
            f"workspace tool(s) {', '.join(shadowed)} would replace a built-in of "
            f"the same name; rename them in {where}"
        )
        raise CapabilityError(msg)


@dataclass(frozen=True)
class _ToolSurface:
    """The tool picture one build works from, resolved once.

    Four values rather than one flat allowlist, because a *delegate* narrows
    the two axes separately and cannot do that from their union: `tools:
    [http_fetch]` must cost it no built-in, which is only expressible while the
    halves are still apart. `permitted` puts them back together for the
    parent's own allowlist, which is one flat list by the time it reaches
    `ToolAllowlist`.

    The default is the skipped probe: nothing narrowed, nothing offered known,
    and `permitted` `None` for "no allowlist at all". Safe because the probe is
    only skipped when no definition names a tool either, so the `ALL`s below
    are never asked to enumerate anything.
    """

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

    @property
    def carried(self) -> tuple[Any, ...]:
        """The tool objects this agent registers: granted, minus the ambiguous.

        A delegate names which `fetch` it wants and gets it. The agent holding
        the grant cannot name anything -- it dispatches by name -- so a pair it
        cannot tell apart is left out and `ambiguous` says which.
        """
        if self.offering is None:
            return tuple(one.tool for one in self.found)
        return tuple(
            one.tool for one in self.offering.carried(self.granted_workspace, self.found)
        )

    @property
    def ambiguous(self) -> tuple[str, ...]:
        """Names granted to this run that only a delegate can ask for."""
        if self.offering is None:
            return ()
        return self.offering.ambiguous(self.granted_workspace, self.found)

    @property
    def offers(self) -> Offering:
        """The offering, or an empty one for the callers that only read names."""
        return self.offering or Offering()

    @property
    def permitted(self) -> tuple[str, ...] | None:
        """The parent's allowlist, or `None` for no restriction at all."""
        if self.offering is None:
            return None
        return self.offering.permitted(self.asked.builtin_tools, self.asked.tools)

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
        return narrowed(self.offering.spelt(self.asked.tools), by=self.offering.workspace) or ()


def _private_tools(catalogue: Definitions, name: str) -> tuple[Found, ...]:
    """The tools a delegate brings itself, or none.

    A lookup rather than a walk: `Definitions.bundled_tools` imported these at
    startup, so a broken one has already failed by the time any of this runs and
    what is left here cannot raise.

    Empty for every delegate without a bundle, which is every delegate today --
    so a deployment that writes none pays a dictionary lookup per activated
    subagent and nothing else.
    """
    repository = catalogue.bundled_tools.get(name)
    return tuple(repository.found) if repository is not None else ()


def _tool_objects(graph: Any) -> Mapping[str, Any]:
    """The built tool objects a compiled graph dispatches, by name.

    `registered_tools` reads the same dict for its keys; a helper needs the
    values. `task` is excluded deliberately and not for tidiness: the harvested
    one is bound to *this* graph's delegate list, so handing it to a helper
    would let the helper reach every delegate the parent can. A delegate that
    may consult one gets a fresh `task` from its own `SubAgentMiddleware`.
    """
    node = getattr(graph, "nodes", {}).get("tools")
    by_name = getattr(getattr(node, "bound", None), "tools_by_name", None)
    if not isinstance(by_name, dict):
        return {}
    return {name: tool for name, tool in by_name.items() if name != TASK_TOOL}


def _resolve_tools(
    # answers came from; folding them up would hide what each one is for.
    where: str,
    capabilities: Capabilities,
    workspace_tools: Sequence[Found],
    assemble: Callable[[tuple[Any, ...]], CompiledStateGraph],
    *,
    names_needed: bool = False,
) -> _ToolSurface:
    """What this request may call, and what this agent offers at all.

    Costs a throwaway assembly, and only when it has to. Both offered sets must
    be known to resolve `ALL` on either axis, and only one can be read off disk:
    the built-in set is a property of an assembled graph. A request that narrows
    neither axis, in a workspace that defines no tools, skips it entirely.

    `names_needed` is the fourth job: a *definition* naming tools has to be
    checked against what exists, and what exists includes the built-in set. A
    caller asks for it only when some activated definition actually names one.
    Measured on a build with one delegate: 1 compile and 12.6ms when it names
    no tools, 2 and 20.3ms when it does -- so the probe is the ~7.7ms, and the
    runs that never needed it still skip it.

    The same probe the shadow check has always needed, doing four jobs now
    rather than one.
    """
    unrestricted = capabilities.builtin_tools == ALL and capabilities.tools == ALL
    if not names_needed and not workspace_tools and unrestricted:
        return _ToolSurface()

    probe = assemble(())
    # Our own probe, so `None` is not reachable here; `or ()` keeps a shape
    # change upstream from becoming a crash at the one site that would.
    builtin = registered_tools(probe) or ()
    _refuse_shadowed(workspace_tools, builtin=builtin, where=where)
    offering = Offering.of(workspace_tools, builtin=builtin)
    offering.refuse_unknown(
        capabilities.builtin_tools, capabilities.tools, subject="this request"
    )
    return _ToolSurface(
        offering=offering,
        asked=capabilities,
        objects=_tool_objects(probe),
        found=tuple(workspace_tools),
    )
