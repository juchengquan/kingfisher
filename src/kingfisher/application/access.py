"""Ask the definitions who reaches what, in the one walk both askers share.

Two places ask, and they must agree. `Kingfisher` asks at construction to decide
whether a deployment may start; `inventory` asks to describe one. They differ
only in what they do with the answer -- raise, or report -- which is the
difference between building a deployment and describing it, and is not a reason
to walk the catalogue twice.

It was walked five times. Three methods on `Kingfisher` each called
`defined_subagents` and then looped the same `(agent, subagent)` pair, and
`inventory` hand-copied two of the three. Nothing forced them to agree, and
`inventory._unrestricted` said so in its own docstring: *"the same walk
`Kingfisher` does at construction, and it has to agree with it."* A rule
enforced by one reading and checked by another is a rule with a seam in it.

**Specs in, never fetched.** The two callers legitimately look at different
sets: `Kingfisher` passes the shared catalogue with `session_dir=None`, because
it is deciding about the deployment before any session exists, while `inventory`
may be describing a session that has uploaded definitions of its own. Fetching
here would have to choose one, and choosing would make the other wrong.

The rules themselves stay in `domain/access.py` -- what a group means, what an
audience admits, what narrows past what. This is only the walk that applies them
to a deployment's own files, which is what an application module is for.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kingfisher.domain.access import AccessError, AccessReport, Stated
from kingfisher.domain.capabilities import ALL

if TYPE_CHECKING:
    from collections.abc import Mapping

    from kingfisher.domain.access import Groups

#: One kind of definition and what this deployment has of it, as the callers
#: hold it: `("agent", {name: spec})`. A pair rather than two arguments because
#: every function here walks both and neither ever wants one alone.
Kind = tuple[str, "Mapping[str, object]"]


def stated(spec: object) -> Stated:
    """What one definition says about who reaches what.

    `getattr` rather than a type, for the reason `inventory` gave first: there
    are two spec types and they say this identically, so asking by attribute is
    what lets one walk serve both without either knowing about the other.

    The defaults are the two absences: a definition with no `groups:` line is
    reachable by everyone, and one with no audienced fields restricts none of
    its entries.
    """
    return Stated(
        groups=getattr(spec, "groups", ALL),
        entries={
            field: dict(entries) for field, entries in getattr(spec, "audiences", {}).items()
        },
    )


def undeclared_in(specs: Mapping[str, object], *, kind: str, vocabulary: Groups) -> str | None:
    """The first definition naming a group this deployment does not declare.

    Returned rather than raised, so that the caller decides. `inventory` reports
    it, because a listing is where somebody goes *because* something is broken
    and one unloadable kind must not take the other two down with it;
    `Kingfisher` raises, because a definition it cannot honour is a deployment
    that must not start.

    The first rather than all of them: a definition that cannot be honoured
    stops the deployment, so there is no second one to reach. Sorted, so which
    one that is does not depend on a dict's order.
    """
    for name, spec in sorted(specs.items()):
        said = stated(spec)
        for where, audience in (
            (f"{kind} {name!r}", said.groups),
            *(
                (f"{kind} {name!r}: {field} entry {entry!r}", who)
                for field, entries in said.entries.items()
                for entry, who in entries.items()
            ),
        ):
            try:
                vocabulary.refuse_undeclared(audience, where=where, error=AccessError)
            except AccessError as exc:
                return str(exc)
    return None


def refuse_undeclared(*kinds: Kind, vocabulary: Groups) -> None:
    """Stop a deployment whose definitions name groups it never declared.

    The closed vocabulary's other end. The caller's end is `Groups.expand`,
    which refuses an unknown name in a group list; this is the same rule pointed
    at the files, and without it the vocabulary is only half closed --
    `groups: [analists]` is not an error, it invents a group nobody is in, and
    the only symptom is an agent quietly reachable by no one, found weeks later
    by whoever needed it.

    Here rather than in `domain.access.parse`, and `audit` below with it, for a
    reason rather than a preference: `parse` reads one document and has no
    catalogue to check against. This is the first moment both are known.

    Refused rather than reported, unlike the two things `audit` returns. Those
    are judgements about files somebody may have meant; this is a name
    misspelled in a file the same deployment wrote, next to the file that lists
    the spellings, and no reading of it is correct.
    """
    for kind, specs in kinds:
        if (complaint := undeclared_in(specs, kind=kind, vocabulary=vocabulary)) is not None:
            raise AccessError(complaint)


def audit(*kinds: Kind, vocabulary: Groups) -> AccessReport:
    """What this deployment's policy leaves open, in one pass over the files.

    Two findings, and they are gathered together because they are two readings
    of the same line rather than two searches. A definition's `groups:` says
    whether anyone is excluded at all; its entries say whether one of them asks
    for more. Walking twice to learn both was how the three methods this
    replaces came to exist.

    Run *after* `refuse_undeclared`, and the order carries meaning: a typo makes
    a line both undeclared and narrowing, and described as a narrowing it would
    be an explanation of the wrong fault. Not enforced here, because a function
    that raised to protect its own call order would be the third opinion on
    something the two callers already agree about.
    """
    unrestricted: list[tuple[str, str]] = []
    narrowed: list[tuple[str, str]] = []
    for kind, specs in kinds:
        for name, spec in sorted(specs.items()):
            said = stated(spec)
            if said.groups == ALL:
                unrestricted.append((kind, name))
            narrowed.extend(
                vocabulary.narrowing_in(
                    said.entries, groups=said.groups, where=f"{kind} {name}"
                )
            )
    return AccessReport(unrestricted=tuple(unrestricted), narrowed=tuple(narrowed))
