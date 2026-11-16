"""A deployment's own wording for kingfisher's prompt pieces, in `prompts/` beside its
definitions, read and checked with them."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import TYPE_CHECKING

from .importing import skipped

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from importlib.resources.abc import Traversable
    from pathlib import Path

#: The folder beside the definitions a deployment's replacements are read from.
PROMPTS_FOLDER = "prompts"

#: Where kingfisher's own pieces ship: read as files, so naming them imports no agent code.
SHIPPED = "kingfisher.core.agent.prompts"

#: Pieces a replacement may not name, each guarding what a rewording could drop.
FIXED = frozenset(
    {
        # What is saved is sent on every later call and handed back with the turn's files.
        "memory/never_save",
        # Without it a reminder already sent went out again 6 times in 6 (#1288).
        "turn/unfinished",
    }
)

#: A blank kingfisher fills, `{{name}}`, and one deepagents fills, `{name}`, in the two
#: templates it formats: a replacement dropping either loses what fills it.
_BLANK = re.compile(r"\{\{([^{}]*)\}\}|(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)\}(?!\})")


class WordingError(ValueError):
    """A replacement kingfisher cannot use, said with the file and what to change."""


def blanks(text: str) -> frozenset[str]:
    """Each blank `text` leaves to be filled, as written between its braces."""
    return frozenset(
        f"{{{single}}}" if single is not None else f"{{{{{double}}}}}"
        for double, single in (match.groups() for match in _BLANK.finditer(text))
    )


def _walk(folder: Traversable, under: str = "") -> Iterator[tuple[str, Traversable]]:
    for entry in sorted(folder.iterdir(), key=lambda one: one.name):
        if skipped(entry.name):
            continue
        if entry.is_dir():
            yield from _walk(entry, f"{under}{entry.name}/")
        elif entry.name.endswith(".md"):
            yield f"{under}{entry.name.removesuffix('.md')}", entry


@cache
def shipped() -> Mapping[str, str]:
    """Every piece kingfisher ships, by name: its path under `prompts/` without `.md`."""
    found = _walk(resources.files(SHIPPED))
    return {name: entry.read_text(encoding="utf-8") for name, entry in found}


@dataclass(frozen=True)
class Wording:
    """The pieces a deployment replaced, by name; an empty text says nothing there."""

    replaced: tuple[tuple[str, str], ...] = ()

    def text(self, name: str) -> str | None:
        """The replacement for `name`, or `None` to send kingfisher's own."""
        return dict(self.replaced).get(name)


def read_wording(folder: Path) -> Wording:
    """The replacements in `folder`, each checked against the piece it replaces."""
    if not folder.is_dir():
        return Wording()
    ours = shipped()
    replaced: dict[str, str] = {}
    for path in sorted(folder.rglob("*")):
        relative = path.relative_to(folder)
        if not path.is_file() or any(skipped(part) for part in relative.parts):
            continue
        where = f"{PROMPTS_FOLDER}/{relative.as_posix()}"
        if path.suffix != ".md":
            msg = f"{where} is not a prompt piece: each replacement is a `.md` file"
            raise WordingError(msg)
        name = relative.with_suffix("").as_posix()
        if name in FIXED:
            msg = f"{where} replaces {name!r}, which kingfisher always sends as it ships"
            raise WordingError(msg)
        if name not in ours:
            msg = (
                f"{where} names no piece kingfisher sends; `kingfisher prompt --pieces` "
                "lists the names"
            )
            raise WordingError(msg)
        text = path.read_text(encoding="utf-8").strip()
        # Empty is how a deployment says nothing there; anything else carries every blank,
        # or the fact a blank holds is never sent and nothing says so.
        if text and (wanted := blanks(ours[name])) != (found := blanks(text)):
            missing, extra = sorted(wanted - found), sorted(found - wanted)
            said = "; ".join(
                part
                for part in (
                    f"it leaves out {', '.join(missing)}" if missing else "",
                    f"it adds {', '.join(extra)}, which nothing fills" if extra else "",
                )
                if part
            )
            msg = f"{where} does not keep the blanks of {name!r}: {said}"
            raise WordingError(msg)
        replaced[name] = text
    return Wording(tuple(sorted(replaced.items())))
