"""What has to be true of subagents together, rather than of one."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from kingfisher.domain.capabilities import ALL, CapabilityError
from kingfisher.kinds.subagents.spec import BUNDLE_KEYS, RunOn, SubagentError, SubagentSpec
from kingfisher.kinds.tools.spec import split_reference


def refuse_two_of_a_name(activated: Sequence[str], *, subject: str) -> None:
    """Refuse a roster that would hold two delegates answering to one name.

    An agent picks a delegate out of a dictionary keyed by name, so two of a name is
    not a conflict it reports -- it is one delegate that quietly never exists.
    Measured: handing deepagents two subagents called `profiler` compiles one, with
    no error and nothing to say which survived.
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
    """Refuse a catalogue where delegation can reach itself."""
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


def _differs(listed: Sequence[str], held: Sequence[str], *, half: str, where: str) -> str | None:
    """How one half of what a definition lists as bundled and its folder disagree.

    Both directions, because they are different mistakes with the same cure. A name
    the folder lost is a definition gone stale; a file the folder holds and the
    definition does not list is a capability that would arrive, or on upgrading from
    when the folder granted by itself would vanish, with no line in any file to show
    it -- and a subset check would let that one through.
    """
    missing = sorted(set(listed) - set(held))
    arrived = sorted(set(held) - set(listed))
    if not missing and not arrived:
        return None
    said = []
    if missing:
        said.append(
            f"{half}: lists {', '.join(missing)} as source: bundled, which "
            f"{where}/{half}/ does not hold"
        )
    if arrived:
        said.append(
            f"{where}/{half}/ holds {', '.join(arrived)}, which {half}: does not list "
            f"-- add each as {{name: <it>, source: bundled}}, or move it out of the "
            f"folder"
        )
    return "; ".join(said)


def miscounted(
    spec: SubagentSpec,
    *,
    where: str | None,
    tools: Sequence[str] = (),
    skills: Sequence[str] = (),
) -> str | None:
    """How a definition's bundled entries differ from the folder they come from.

    Asked as well as refused, for the reason `Offering.moved` gives: startup refuses
    this and `doctor` has to report what startup refuses, and a check reachable only
    through the constructor is one a listing cannot see.
    """
    if spec.carried:
        return None  # it brought its own, and there is no folder to compare them with
    listed = {half: names for half, names in spec.bundled.items() if names}
    if where is None:
        if not listed:
            return None
        # The rename, caught from the side the definition is on. `orphaned_assets`
        # reports the folder left behind; this reports the file that walked away
        # from it, and only this one knows what the file thought it had.
        return (
            f"lists entries as source: bundled and owns no folder to take them from "
            f"-- a delegate's own folder is subagents/{spec.name}/, holding this "
            f"definition"
        )
    held = {"tools": tools, "skills": skills}
    differences = [
        found
        for half in BUNDLE_KEYS
        if (found := _differs(listed.get(half, ()), held[half], half=half, where=where))
        is not None
    ]
    return "; ".join(differences) or None


def refuse_miscounted(
    spec: SubagentSpec,
    *,
    where: str | None,
    tools: Sequence[str] = (),
    skills: Sequence[str] = (),
) -> None:
    """Refuse a definition whose bundled entries and its own folder disagree."""
    found = miscounted(spec, where=where, tools=tools, skills=skills)
    if found is not None:
        msg = (
            f"subagent {spec.name!r}: {found}. Only what is listed reaches the "
            f"delegate, so the list and the folder have to match"
        )
        raise SubagentError(msg)


def resolved_model(
    wanted: str | None, *, override: RunOn | None = None
) -> str | None:
    """What a delegate would run, in order, once the request has had its say."""
    if override is not None:
        return override.model
    return wanted
