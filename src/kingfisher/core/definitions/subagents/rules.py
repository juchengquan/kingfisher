"""What has to be true of subagents together, rather than of one."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath

from kingfisher.core.access.capabilities import ALL, CapabilityError
from kingfisher.core.definitions.tools.spec import split_reference

from .spec import (
    BUNDLE_KEYS,
    RunOn,
    SubagentError,
    SubagentSpec,
)


def refuse_two_of_a_name(activated: Sequence[str], *, subject: str) -> None:
    """Refuse a roster that would hold two subagents answering to one name.

    Measured: deepagents handed two subagents called `probe` compiles one, with no error
    and nothing to say which survived.
    """
    seen: dict[str, list[str]] = {}
    for written in activated:
        seen.setdefault(split_reference(written)[1], []).append(written)
    clashing = sorted((name, wrote) for name, wrote in seen.items() if len(wrote) > 1)
    if clashing:
        name, wrote = clashing[0]
        msg = (
            f"{subject} activates {len(wrote)} subagents called {name!r}, and an "
            f"agent reaches a subagent by name -- one would never run. "
            f"Activate the one you meant: {', '.join(sorted(wrote))}"
        )
        raise CapabilityError(msg)


def refuse_cycles(specs: Mapping[str, SubagentSpec]) -> None:
    """Refuse a catalogue where delegation can reach itself."""
    # Iterative, so a deep catalogue cannot hit the recursion limit.
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
            # `on_path` before `seen`: a node reached twice is ordinary in a DAG, one
            # reached while still on the path is the loop. `seen` first passes every cycle.
            if name in on_path:
                loop = [*path[path.index(name) :], name]
                # `subagents: ['*']` never names the loop it made, so the message has to;
                # and it is always one, since `*` includes the definition itself.
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
            # `*` is every definition here, as `subagent_helpers` expands it: the two must
            # agree, since `_with_helpers` has no re-entry guard and relies on this walk.
            if spec is None or spec.subagents is None:
                named: tuple[str, ...] = ()
            elif spec.subagents == ALL:
                named = tuple(specs)
            else:
                named = spec.subagents
            # Reverse-sorted onto a stack so they pop in order: a loop is reported by
            # the same path every time.
            for helper in sorted(named, reverse=True):
                # A name that is no key is no edge -- unknown, or shared by two files.
                # Safe because the build refuses it against these same keys first.
                if helper in specs:
                    stack.append((helper, False))


def _differs(
    listed: Sequence[str],
    held: Sequence[str],
    *,
    half: str,
    where: str,
    buried: Sequence[str] = (),
) -> str | None:
    """How one half of what a definition lists as bundled and its folder disagree.

    Both directions: a name the folder lacks is a stale definition, and a file the list
    lacks is a capability arriving with no line to show it, which a subset check passes.
    """
    missing = sorted(set(listed) - set(held))
    arrived = sorted(set(held) - set(listed))
    # A listed skill in a subfolder is buried, not missing: the file is plainly there.
    deep = {PurePosixPath(one).name: one for one in buried}
    sunk = [name for name in missing if name in deep]
    missing = [name for name in missing if name not in deep]
    if not missing and not arrived and not sunk:
        return None
    said = []
    if sunk:
        said.append(
            f"{half}: lists {', '.join(sunk)} as source: bundled, which sits in a "
            f"subfolder of {where}/{half}/ ({', '.join(deep[name] for name in sunk)}) -- "
            f"a subagent is given its folder as one source, so move each to sit "
            f"directly in it"
        )
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
    buried: Sequence[str] = (),
) -> str | None:
    """How a definition's bundled entries differ from the folder they come from.

    Asked as well as refused, so `doctor` can report what startup refuses.
    """
    if spec.carried:
        return None  # it carried its own: there is no folder to compare
    listed = {half: names for half, names in spec.bundled.items() if names}
    if where is None:
        if not listed:
            return None
        # A renamed definition: `orphaned_assets` reports the folder it left, and only
        # this knows what the file expected to find there.
        return (
            f"lists entries as source: bundled and owns no folder to take them from "
            f"-- a subagent's own folder is subagents/{spec.name}/, holding this "
            f"definition"
        )
    held = {"tools": tools, "skills": skills}
    differences = [
        found
        for half in BUNDLE_KEYS
        if (
            found := _differs(
                listed.get(half, ()),
                held[half],
                half=half,
                where=where,
                buried=buried if half == "skills" else (),
            )
        )
        is not None
    ]
    return "; ".join(differences) or None


def refuse_miscounted(
    spec: SubagentSpec,
    *,
    where: str | None,
    tools: Sequence[str] = (),
    skills: Sequence[str] = (),
    buried: Sequence[str] = (),
) -> None:
    """Refuse a definition whose bundled entries and its own folder disagree."""
    found = miscounted(spec, where=where, tools=tools, skills=skills, buried=buried)
    if found is not None:
        msg = (
            f"subagent {spec.name!r}: {found}. Only what is listed reaches the "
            f"subagent, so the list and the folder have to match"
        )
        raise SubagentError(msg)


def resolved_model(
    wanted: str | None, *, override: RunOn | None = None
) -> str | None:
    """The model a subagent would run once the request has had its say."""
    if override is not None:
        return override.model
    return wanted
