"""Ask the definitions who reaches what, in the one walk both askers share."""

from __future__ import annotations

from typing import TYPE_CHECKING

from kingfisher.domain.access import AccessError, AccessReport, Stated
from kingfisher.domain.capabilities import ALL

if TYPE_CHECKING:
    from collections.abc import Mapping

    from kingfisher.domain.access import SourceIds

#: One kind of definition and what this deployment has of it, as the callers
#: hold it: `("agent", {name: spec})`. A pair rather than two arguments because
#: every function here walks both and neither ever wants one alone.
Kind = tuple[str, "Mapping[str, object]"]


def stated(spec: object) -> Stated:
    """What one definition says about who reaches what."""
    return Stated(
        source_ids=getattr(spec, "source_ids", ALL),
        entries={
            field: dict(entries) for field, entries in getattr(spec, "audiences", {}).items()
        },
    )


def undeclared_in(specs: Mapping[str, object], *, kind: str, vocabulary: SourceIds) -> str | None:
    """The first definition naming a source id this deployment does not declare."""
    for name, spec in sorted(specs.items()):
        said = stated(spec)
        for where, audience in (
            (f"{kind} {name!r}", said.source_ids),
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


def refuse_undeclared(*kinds: Kind, vocabulary: SourceIds) -> None:
    """Stop a deployment whose definitions name source ids it never declared."""
    for kind, specs in kinds:
        if (complaint := undeclared_in(specs, kind=kind, vocabulary=vocabulary)) is not None:
            raise AccessError(complaint)


def audit(*kinds: Kind, vocabulary: SourceIds) -> AccessReport:
    """What this deployment's policy leaves open, in one pass over the files."""
    unrestricted: list[tuple[str, str]] = []
    narrowed: list[tuple[str, str]] = []
    for kind, specs in kinds:
        for name, spec in sorted(specs.items()):
            said = stated(spec)
            if said.source_ids == ALL:
                unrestricted.append((kind, name))
            narrowed.extend(
                vocabulary.narrowing_in(
                    said.entries, source_ids=said.source_ids, where=f"{kind} {name}"
                )
            )
    return AccessReport(unrestricted=tuple(unrestricted), narrowed=tuple(narrowed))
