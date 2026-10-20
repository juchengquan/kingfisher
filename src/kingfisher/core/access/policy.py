"""Reading `source_ids.yaml`: the source ids this deployment declares."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Final

import yaml

from kingfisher.core.definitions import documents
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

#: Said of a name written with a body, which once covered or required others.
_NO_BODIES = (
    "a name stands only for itself: a caller sends the names a role covers, and a "
    "definition needing several held at once writes them as a set, {a, b}, in its own "
    "source_ids:"
)


def load_access_policy(path: Path) -> SourceIds | None:
    """The policy at `path`, or `None` if there is no file there."""
    if not path.is_file():
        return None
    try:
        document = documents.load(path.read_text(encoding="utf-8"))
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


def parse(document: Mapping[str, object], source: str) -> SourceIds:
    """One vocabulary document, from its decoded fields."""
    complaint = unrecognised(
        document, known={"source_ids", "datazones"}, declined=MOVED, noun="section"
    )
    if complaint is not None:
        msg = f"{source}: {complaint}"
        raise AccessError(msg)
    raw = document.get("source_ids")
    if raw is None:
        msg = (
            f"{source}: missing required section 'source_ids'; it is the closed "
            f"vocabulary every definition's audience is checked against"
        )
        raise AccessError(msg)
    names = _listed(raw, section="source_ids", example="[sales_db, audit_log]", source=source)
    if reserved := sorted(name for name in names if name.startswith(DATAZONE_PREFIX)):
        msg = (
            f"{source}: {', '.join(map(repr, reserved))} begins {DATAZONE_PREFIX!r}, "
            f"which is how a datazone is held -- declare datazones under 'datazones:'"
        )
        raise AccessError(msg)
    raw_zones = document.get("datazones")
    datazones = (
        frozenset()
        if raw_zones is None
        else _listed(raw_zones, section="datazones", example="[finance, hr]", source=source)
    )
    return SourceIds(names=names, datazones=datazones)


def _listed(raw: object, *, section: str, example: str, source: str) -> frozenset[str]:
    """One section's names: a list of them, each written once."""
    # `{a, b}` and `a: [b]` arrive as mappings, `- a: [b]` and `- [a, b]` as items with bodies.
    if isinstance(raw, Mapping) or (
        isinstance(raw, list) and any(isinstance(one, (Mapping, list)) for one in raw)
    ):
        msg = f"{source}: '{section}' is a list of names -- {_NO_BODIES}"
        raise AccessError(msg)
    if isinstance(raw, str) or not isinstance(raw, list):
        msg = f"{source}: '{section}' is a list of names -- write {section}: {example}"
        raise AccessError(msg)
    named = [text(one) for one in raw]
    if "" in named:
        msg = f"{source}: '{section}' holds an empty name"
        raise AccessError(msg)
    if twice := sorted({one for one in named if named.count(one) > 1}):
        msg = f"{source}: '{section}' names {', '.join(map(repr, twice))} twice"
        raise AccessError(msg)
    return frozenset(named)
