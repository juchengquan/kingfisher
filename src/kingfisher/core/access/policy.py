"""Reading `source_ids.yaml`: the source ids this deployment declares."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Final

import yaml

from kingfisher.core.definitions.fields import text, unrecognised

from .source_ids import DATAZONE_PREFIX, AccessError, SourceIds

if TYPE_CHECKING:
    from pathlib import Path


_WENT = (
    "audiences live in the definitions now: write `source_ids:` in the agent's or "
    "subagent's own file, a tool's in its tool file as ToolSpec(tool, source_ids=[...]), "
    "and a skill's on its `skills:` entry"
)

#: Sections the central format defined, refused rather than ignored: an upgrading
#: deployment's policy would otherwise be read and dropped in silence.
MOVED: Final[Mapping[str, str]] = dict.fromkeys(("agents", "subagents", "tools"), _WENT)


def load_access_policy(path: Path) -> SourceIds | None:
    """The policy at `path`, or `None` if there is no file there."""
    if not path.is_file():
        return None
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        msg = f"{path}: is not valid YAML -- {exc}"
        raise AccessError(msg) from exc
    except OSError as exc:
        msg = f"{path}: cannot be read -- {exc}"
        raise AccessError(msg) from exc

    if document is None:
        msg = (
            f"{path}: is empty. A policy file that exists but says nothing is "
            f"not the same as no policy -- delete it, or give it a 'source_ids' section"
        )
        raise AccessError(msg)
    if not isinstance(document, dict):
        msg = f"{path}: is a mapping of sections, not {type(document).__name__}"
        raise AccessError(msg)
    return parse(document, source=path.name)


#: The declared source ids and what each covers, beside the ones written as a set.
_Vocabulary = tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]


def _vocabulary(raw: object, source: str) -> _Vocabulary:
    """The declared source ids, what each covers, and what each requires."""
    if raw is None:
        msg = (
            f"{source}: missing required section 'source_ids'; it is the closed "
            f"vocabulary every definition's audience is checked against"
        )
        raise AccessError(msg)
    if isinstance(raw, list):
        return {str(name): () for name in raw}, {}
    if not isinstance(raw, Mapping):
        msg = (
            f"{source}: 'source_ids' is a list of names, or a mapping of name to "
            f"[a, b] for what it covers or {{a, b}} for what it requires"
        )
        raise AccessError(msg)

    _refuse_retired_spelling(raw, source)

    declared: dict[str, tuple[str, ...]] = {}
    compounds: dict[str, tuple[str, ...]] = {}
    for name, body in raw.items():
        if body is None:
            declared[str(name)] = ()
        elif isinstance(body, (list, tuple)):
            declared[str(name)] = _covers(body, name=str(name), source=source)
        elif isinstance(body, Mapping):
            compounds[str(name)] = _requires(body, name=str(name), source=source)
        else:
            msg = (
                f"{source}: source id {name!r} is [a, b] for what it covers, "
                f"{{a, b}} for what it requires, or nothing at all -- got {body!r}"
            )
            raise AccessError(msg)
        # Declared either way: `names` is what says a name exists, compound or not.
        declared.setdefault(str(name), ())
    return declared, compounds


def _covers(body: Sequence[object], *, name: str, source: str) -> tuple[str, ...]:
    """The source ids a name hands out, from the list form."""
    if not body:
        msg = (
            f"{source}: source id {name!r} covers nothing -- leave the body out "
            f"to declare a plain source id"
        )
        raise AccessError(msg)
    # The inline half of `_refuse_granted_compounds`'s rule, here because an inline
    # requirement has no name to look up there.
    for one in body:
        if isinstance(one, Mapping):
            parts = ", ".join(str(part) for part in one) or "..."
            msg = (
                f"{source}: source id {name!r} covers {{{parts}}}, which is a "
                f"requirement rather than a source id anyone holds. Handing one "
                f"over is the requirement defeated by the file that writes it; "
                f"cover [{parts}] instead, which reaches the same callers"
            )
            raise AccessError(msg)
    return tuple(str(one) for one in body)


def _requires(body: Mapping[object, object], *, name: str, source: str) -> tuple[str, ...]:
    """The source ids a caller must hold together, from the set form."""
    # `{finance: senior}` is a mapping with a value, not a set of two names: read as a set it
    # would throw away what was written beside `finance`.
    if valued := sorted(str(key) for key, value in body.items() if value is not None):
        msg = (
            f"{source}: source id {name!r} requires {{{', '.join(valued)}}} with "
            f"something written after it. A requirement is a set of names -- "
            f"write {{a, b}}, not {{a: b}}"
        )
        raise AccessError(msg)
    return tuple(str(key) for key in body)


#: The keywords the vocabulary took before shapes replaced them. `A: {}`, the old short form
#: for a plain name, is refused beside them: read as a set it requires nothing, so everyone.
_RETIRED: Final[tuple[str, ...]] = ("contains", "all_of")


def _refuse_retired_spelling(raw: Mapping[object, object], source: str) -> None:
    """Refuse a whole vocabulary written the old way, naming every line to change.

    One refusal for the file rather than one per name, so an upgrading deployment fixes it
    once instead of restarting for each.
    """
    changes: list[tuple[str, str]] = []
    for name, body in raw.items():
        if not isinstance(body, Mapping):
            continue
        # `{contains, all_of}` is two names in a set, not a keyword with a value,
        # so the value is what separates a retired spelling from a legal one.
        written = tuple(key for key in _RETIRED if body.get(key) is not None)
        if not (written or body == {}):
            continue
        if not written:
            changes.append((f"{name}: {{}}", f"{name}:"))
            continue
        listed = body[written[0]]
        parts = (
            ", ".join(str(one) for one in listed)
            if isinstance(listed, (list, tuple))
            else "..."
        )
        was = f"{name}: {{{written[0]}: [{parts}]}}"
        now = f"{name}: [{parts}]" if written[0] == "contains" else f"{name}: {{{parts}}}"
        changes.append((was, now))
    if not changes:
        return

    width = max(len(was) for was, _ in changes)
    lines = "\n".join(f"  {was.ljust(width)}  ->  {now}" for was, now in changes)
    msg = (
        f"{source}: this vocabulary is written in the retired spelling. A list is "
        f"what a name covers and a set is what it requires, so the shape says which "
        f"and there is no word to get wrong. {len(changes)} source id(s) to "
        f"change:\n\n{lines}"
    )
    raise AccessError(msg)


def _closed(declared: Mapping[str, tuple[str, ...]], source: str) -> dict[str, tuple[str, ...]]:
    """Each source id's transitive closure, itself included, with cycles refused."""
    for name, covers in declared.items():
        for one in covers:
            if one not in declared:
                msg = (
                    f"{source}: source id {name!r} covers {one!r}, which is not "
                    f"declared; this file defines {', '.join(sorted(declared))}"
                )
                raise AccessError(msg)

    closure: dict[str, tuple[str, ...]] = {}

    def walk(name: str, path: tuple[str, ...]) -> tuple[str, ...]:
        if name in path:
            loop = " -> ".join((*path[path.index(name) :], name))
            msg = (
                f"{source}: source ids cover themselves: {loop}. Expansion "
                f"follows every link, so a loop would never finish -- one of "
                f"these has to stop covering the next"
            )
            raise AccessError(msg)
        if name in closure:
            return closure[name]
        reached: list[str] = [name]
        for one in declared[name]:
            reached.extend(n for n in walk(one, (*path, name)) if n not in reached)
        # Written only once the whole subtree returned without raising, so a
        # memoised entry can never be a partial answer taken from inside a loop.
        closure[name] = tuple(reached)
        return closure[name]

    return {name: walk(name, ()) for name in declared}


def parse(document: Mapping[str, object], source: str) -> SourceIds:
    """One vocabulary document, from its decoded fields."""
    complaint = unrecognised(
        document, known={"source_ids", "datazones"}, declined=MOVED, noun="section"
    )
    if complaint is not None:
        msg = f"{source}: {complaint}"
        raise AccessError(msg)
    declared, compounds = _vocabulary(document.get("source_ids"), source)
    if reserved := sorted(name for name in declared if name.startswith(DATAZONE_PREFIX)):
        msg = (
            f"{source}: {', '.join(map(repr, reserved))} begins {DATAZONE_PREFIX!r}, "
            f"which is how a datazone is held -- declare datazones under 'datazones:'"
        )
        raise AccessError(msg)
    _refuse_undeclared_parts(declared, compounds, source)
    _refuse_granted_compounds(declared, compounds, source)
    _refuse_compound_loops(compounds, source)
    return SourceIds(
        names=_closed(declared, source),
        compounds=compounds,
        datazones=_datazones(document.get("datazones"), source),
    )


def _datazones(raw: object, source: str) -> frozenset[str]:
    """The declared datazones: a list of names, or none at all."""
    if raw is None:
        return frozenset()
    if isinstance(raw, str) or not isinstance(raw, list):
        msg = f"{source}: 'datazones' is a list of names -- write datazones: [finance, hr]"
        raise AccessError(msg)
    named = [text(one) for one in raw]
    if "" in named:
        msg = f"{source}: 'datazones' holds an empty name"
        raise AccessError(msg)
    if twice := sorted({one for one in named if named.count(one) > 1}):
        msg = f"{source}: 'datazones' names {', '.join(map(repr, twice))} twice"
        raise AccessError(msg)
    return frozenset(named)


def _refuse_granted_compounds(
    declared: Mapping[str, tuple[str, ...]], compounds: Mapping[str, tuple[str, ...]], source: str
) -> None:
    """Refuse a covers list that hands out a compound rather than its parts."""
    for name, holds in declared.items():
        for one in holds:
            if one in compounds:
                parts = ", ".join(compounds[one])
                msg = (
                    f"{source}: source id {name!r} covers {one!r}, which is derived "
                    f"rather than held -- it means all of {{{parts}}}. Handing it "
                    f"over directly is the requirement defeated by the file that "
                    f"declares it; cover [{parts}] instead, which reaches the same "
                    f"callers and says why"
                )
                raise AccessError(msg)


def _refuse_undeclared_parts(
    declared: Mapping[str, tuple[str, ...]], compounds: Mapping[str, tuple[str, ...]], source: str
) -> None:
    """Refuse a compound built from a name this file never declares."""
    for name, parts in compounds.items():
        for part in parts:
            if part not in declared:
                msg = (
                    f"{source}: source id {name!r} requires {part!r}, which is not "
                    f"declared; this file defines {', '.join(sorted(declared))}"
                )
                raise AccessError(msg)


def _refuse_compound_loops(compounds: Mapping[str, tuple[str, ...]], source: str) -> None:
    """Refuse a compound that requires itself, directly or through others."""
    walked: set[str] = set()

    def walk(name: str, path: tuple[str, ...]) -> None:
        if name in path:
            loop = " -> ".join((*path[path.index(name) :], name))
            msg = (
                f"{source}: source ids require themselves: {loop}. A requirement "
                f"loop can never be entered, so none of these is ever held -- "
                f"one of them has to stop requiring the next"
            )
            raise AccessError(msg)
        if name in walked:
            return
        for part in compounds.get(name, ()):
            walk(part, (*path, name))
        walked.add(name)

    for name in compounds:
        walk(name, ())
