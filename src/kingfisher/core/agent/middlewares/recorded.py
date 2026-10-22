"""How a recorded result is written down: a row's arguments, summary and size, and the
id the result ends with in what the model is handed."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from kingfisher.core.session.store import ENTRY_ID

#: How much of one argument, and of a result's first line, a row keeps.
ARG_LIMIT = 60
LINE_LIMIT = 100

_KB = 1024

#: An attachment's kind, as one and as several.
_KINDS = {"image": ("an image", "images"), "PDF": ("a PDF", "PDFs"), "file": ("a file", "files")}


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def arguments(args: Mapping[str, Any]) -> str:
    """Each argument as `name=value`. `repr` keeps a value on one line and a string
    in its quotes, so `path='/inputs/a b.csv'` reads as one argument.
    """
    return " ".join(f"{name}={_cut(repr(value), ARG_LIMIT)}" for name, value in args.items())


def summary(tool: str, args: str, text: str, blocks: Sequence[Mapping[str, Any]] = ()) -> str:
    """The tool, its arguments and the first line of what it returned, then any of the
    result's `blocks` its saved copy, which is `text` alone, left out.
    """
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    called = f"{tool} {args}" if args else tool
    said = f"{called} -- {_cut(first, LINE_LIMIT)}" if first else called
    left = _not_kept([one for one in blocks if one.get("type") != "text"])
    return f"{said} ({left})" if left else said


def _not_kept(attached: Sequence[Mapping[str, Any]]) -> str:
    """The attachments a saved copy left out, by kind and size, or `""` for none."""
    if not attached:
        return ""
    kinds = {_kind(one) for one in attached}
    known = [size for one in attached if (size := _inline_bytes(one)) is not None]
    # A total of some of them would understate the rest.
    of = ""
    if len(known) == len(attached):
        of = f" of {readable_size(sum(known))}{' in all' if len(attached) > 1 else ''}"
    if len(attached) == 1:
        return f"text only; {_KINDS[kinds.pop()][0]}{of} was not kept"
    many = _KINDS[kinds.pop()][1] if len(kinds) == 1 else "attachments"
    return f"text only; {len(attached)} {many}{of} were not kept"


def _kind(block: Mapping[str, Any]) -> str:
    mime = str(block.get("mime_type") or "")
    if block.get("type") == "image" or mime.startswith("image/"):
        return "image"
    return "PDF" if mime == "application/pdf" else "file"


def _inline_bytes(block: Mapping[str, Any]) -> int | None:
    """What a block carries in base64, or `None` for one held elsewhere, by URL or id."""
    data = block.get("base64")
    return None if data is None else len(data) * 3 // 4


def readable_size(size: int) -> str:
    """Bytes under a KB, the nearest KB under an MB, and MB to a tenth.

    The unit is chosen after rounding, so a size just short of an MB never reads as
    1024 KB.
    """
    if size < _KB:
        return f"{size} B"
    if (kb := (size + _KB // 2) // _KB) < _KB:
        return f"{kb} KB"
    return f"{size / (_KB * _KB):.1f} MB"


def stamp(number: str) -> str:
    """A result's id as the last line of what the model is handed."""
    return f"\n[{number}]"


#: What `stamp` writes, as a pattern a longer one may end in.
STAMP_PATTERN = rf"\n\[{ENTRY_ID.pattern}\]"

#: Not `STAMP_PATTERN`: a `FilesystemMiddleware` a deployment puts in place may offload a
#: result to deepagents' stub, which numbers its lines, so the id ends the text, not a line.
_ENDING = re.compile(rf"\[(?P<id>{ENTRY_ID.pattern})\]\s*\Z")


def stamped_id(text: str) -> str | None:
    """The id `text` ends in, or `None` where it ends in none."""
    found = _ENDING.search(text)
    return None if found is None else found.group("id")
