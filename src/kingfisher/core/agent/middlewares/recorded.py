"""How a recorded result is written down: a row's arguments and summary, and the id the
result ends with in what the model is handed."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from kingfisher.core.session.store import ENTRY_ID

#: How much of one argument, and of a result's first line, a row keeps.
ARG_LIMIT = 60
LINE_LIMIT = 100


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def arguments(args: Mapping[str, Any]) -> str:
    """Each argument as `name=value`. `repr` keeps a value on one line and a string
    in its quotes, so `path='/inputs/a b.csv'` reads as one argument.
    """
    return " ".join(f"{name}={_cut(repr(value), ARG_LIMIT)}" for name, value in args.items())


def summary(tool: str, args: str, text: str) -> str:
    """The tool, its arguments and the first line of what it returned."""
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    called = f"{tool} {args}" if args else tool
    return f"{called} -- {_cut(first, LINE_LIMIT)}" if first else called


def stamp(number: str) -> str:
    """A result's id as the last line of what the model is handed."""
    return f"\n[{number}]"


#: What `stamp` writes, as a pattern a longer one may end in.
STAMP_PATTERN = rf"\n\[{ENTRY_ID.pattern}\]"

#: Not `STAMP_PATTERN`: a stub deepagents left numbers its lines, so the id is matched at
#: the end of the text, not as a line of its own.
_ENDING = re.compile(rf"\[(?P<id>{ENTRY_ID.pattern})\]\s*\Z")


def stamped_id(text: str) -> str | None:
    """The id `text` ends in, or `None` where it ends in none."""
    found = _ENDING.search(text)
    return None if found is None else found.group("id")
