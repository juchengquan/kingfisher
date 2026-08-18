"""What has to be true of subagents together, rather than of one.

`reading` decides whether a definition is well-formed. Nothing it can see says
whether a *catalogue* is coherent: two folders may each define a `surveyor`, a
helper may name a helper that names it back, and a delegate defined to be a
different model may resolve to the one it exists not to be. Each of those is
three well-formed definitions and one broken deployment.

So these take a set, or a spec and what the deployment bound, and refuse. They
are checked when a catalogue loads rather than per request, because a set of
definitions is either coherent or it is not.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from kingfisher.domain.capabilities import ALL, CapabilityError
from kingfisher.domain.subagent import RunOn, SubagentError, SubagentSpec, Wanted
from kingfisher.domain.tool import split_reference


def refuse_two_of_a_name(activated: Sequence[str], *, subject: str) -> None:
    """Refuse a roster that would hold two delegates answering to one name.

    An agent picks a delegate out of a dictionary keyed by name, so two of a
    name is not a conflict it reports -- it is one delegate that quietly never
    exists. Measured: handing deepagents two subagents called `profiler`
    compiles one, with no error and nothing to say which survived.

    The catalogue itself keeps both, because two folders may each hold a
    `profiler.yaml` and refusing the pair on sight stopped the whole deployment
    over a clash no single agent had yet asked for. This is where the clash
    actually happens, so this is where it is refused -- and by then there is a
    reference to name each one by.

    Cheaper here than the same rule is for tools, and worth knowing why: a
    request activates *no* delegates by default, so a caller who never asked for
    two can never trip this. `tools` defaults to everything, which is why that
    axis had to split a grant from what an agent carries instead of refusing.
    """
    seen: dict[str, list[str]] = {}
    for written in activated:
        seen.setdefault(split_reference(written)[1], []).append(written)
    clashing = sorted((name, wrote) for name, wrote in seen.items() if len(wrote) > 1)
    if clashing:
        name, wrote = clashing[0]
        msg = (
            f"{subject} activates {len(wrote)} subagents called {name!r}, and an "
            f"agent reaches a delegate by name -- one would never run. "
            f"Activate the one you meant: {', '.join(sorted(wrote))}"
        )
        raise CapabilityError(msg)


def refuse_cycles(specs: Mapping[str, SubagentSpec]) -> None:
    """Refuse a catalogue where delegation can reach itself.

    Delegation nests to any depth, so this is the only thing standing between a
    catalogue and an agent that builds forever. It replaces a rule that bounded
    the depth at one -- `refuse_helpers_with_helpers` -- which made a cycle
    impossible by making the shape impossible, and cost every catalogue the
    ability to say `a` consults `b` consults `c`.

    Enforced on the *catalogue* rather than per request, for the reason the old
    rule was: a set of definitions is either coherent or it is not, whoever
    activates what. A per-request check would pass for one caller and fail for
    another against identical files. It also falls out of work already being
    done -- compiling each definition once needs a dependency order, and a cycle
    is precisely what makes one impossible.

    What it does *not* bound is cost, and that is worth stating because it is
    the assumption this rule invites. A catalogue with no cycle at all can still
    describe an enormous number of paths; compiling once per definition rather
    than once per path is what makes that free, and lives in `delegation`.

    The message names the whole loop rather than one edge of it, the same way a
    tool collision names both files: whoever reads it may own none of them, and
    an edge alone does not say which link to cut.
    """
    # Iterative depth-first, so a catalogue deep enough to matter cannot take
    # the interpreter's recursion limit with it -- the one bound this rule
    # removes is the one that used to make that impossible.
    seen: set[str] = set()
    for start in sorted(specs):
        if start in seen:
            continue
        path: list[str] = []
        on_path: set[str] = set()
        stack: list[tuple[str, bool]] = [(start, False)]
        while stack:
            name, leaving = stack.pop()
            if leaving:
                on_path.discard(path.pop())
                continue
            # `on_path` before `seen`, and the order is the whole check: a node
            # reached twice is ordinary in a DAG, a node reached while still on
            # the current path is the loop. Testing `seen` first skipped straight
            # past every cycle and reported a clean catalogue.
            if name in on_path:
                loop = [*path[path.index(name) :], name]
                # A definition that says `subagents: ['*']` never names the loop
                # it made, so the message has to. It is always a loop: `*` is
                # every definition in the catalogue, and every catalogue holding
                # it holds that one.
                by_star = specs[path[-1]].subagents == ALL if path else False
                how = (
                    f" -- {path[-1]!r} names every subagent with `*`, and that includes itself"
                    if by_star
                    else ""
                )
                msg = (
                    f"subagents reach themselves: {' -> '.join(loop)}{how}. Delegation "
                    f"nests to any depth, so a loop would build without end -- one "
                    f"of these has to stop naming the next"
                )
                raise SubagentError(msg)
            if name in seen:
                continue
            path.append(name)
            on_path.add(name)
            seen.add(name)
            stack.append((name, True))
            spec = specs.get(name)
            # `*` means every definition here, which is what `subagent_helpers`
            # expands it to when it builds. This read it as *no* edges, so a
            # definition saying it consults everything passed the walk and then
            # recursed without bound at build time -- `_with_helpers` has no
            # re-entry guard and says in a comment that it needs none, because
            # this ran. The two have to agree about what `*` means or the
            # guarantee is only about the catalogues that avoid it.
            if spec is None or spec.subagents is None:
                named: tuple[str, ...] = ()
            elif spec.subagents == ALL:
                named = tuple(specs)
            else:
                named = spec.subagents
            # Reverse-sorted onto a stack, so they pop in order and a loop is
            # reported by the same path every time rather than by whichever
            # branch the dict happened to yield first.
            for helper in sorted(named, reverse=True):
                if helper in specs:
                    stack.append((helper, False))


def resolved_model(
    wanted: tuple[Wanted, ...], *, override: RunOn | None = None
) -> tuple[Wanted, ...]:
    """What a delegate would run, in order, once the request has had its say.

    The override replaces wholesale, and that includes replacing an *alias* with
    a model, and a list with one entry. A caller naming a concrete model has
    said something more specific than the file did, and keeping the file's
    candidates beside it would mean resolving several answers to one question --
    including the case where the file's second choice quietly outranks the
    caller's only one.

    Almost nothing else left, and that is the result rather than an oversight.
    This was `resolved_endpoint` and returned a `(provider, model)` pair,
    carrying two refusals with it: an operator override that could only ever say
    "all delegates", and the endpoint grant. The first was deleted before this
    change; the second cannot live here any more, because the endpoint is no
    longer written in the definition -- it follows from the model, through a
    catalogue only `Config` holds. Binding an alias needs that catalogue too.

    Which is the layering working rather than fighting it. The domain does not
    read deployment configuration, and
    `test_domain_imports_only_the_standard_library_and_itself` holds it to that,
    so a rule needing the catalogue belongs where the catalogue is. What is
    still a question about *names* -- may this request name this model, may it
    reach this endpoint -- stays in `capabilities`.

    Takes the candidates rather than the spec that carries them, which is the
    rule this package already states about `Config`: a domain rule that needs a
    value takes the value. It is also what lets one rule serve both definition
    formats -- an agent names a model exactly as a delegate does, and this
    module cannot import `domain.agent` without a cycle, since that format is
    written in terms of this one.
    """
    if override is not None:
        return (Wanted(model=override.model),)
    return wanted
