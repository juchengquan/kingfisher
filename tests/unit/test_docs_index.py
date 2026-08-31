"""`docs/README.md` has to keep listing what is actually in `docs/`.

An index is worth exactly as much as its completeness. A page listing
twenty-six of twenty-seven design documents is worse than no page at all,
because a reader who trusts it stops looking -- and the one it drops is the
newest, since a new document is added by someone who was not thinking about
this file.

Two directions, because the drift goes both ways: a document nothing links to
is invisible, and a link to a document that has moved is a dead end. Neither
fails anywhere else. `docs/design/` is history and is not rewritten, so nothing
else in this repository has a reason to open these files at all.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.conftest import repository_root

#: `[text](path)` with a relative target. Absolute URLs are left alone -- they
#: are not this repository's files and cannot be checked by looking at disk.
LINK = re.compile(r"\[[^\]]+\]\((?!https?://)([^)#]+)")

DOCS = repository_root() / "docs"
INDEX = DOCS / "README.md"


def _linked() -> set[Path]:
    """Every repository file the index points at, resolved."""
    return {(DOCS / target).resolve() for target in LINK.findall(INDEX.read_text(encoding="utf-8"))}


def _documents() -> set[Path]:
    """Every document under `docs/`, except the index itself."""
    return {p.resolve() for p in DOCS.rglob("*.md") if p.resolve() != INDEX.resolve()}


def test_the_index_lists_every_document() -> None:
    """The direction that matters, and the one that breaks silently.

    A document added without a line here is not half-indexed; it is missing from
    the only page that says what exists. Named individually in the failure rather
    than counted, because "27 != 28" does not say which one to go and read.
    """
    missing = sorted(p.relative_to(DOCS).as_posix() for p in _documents() - _linked())

    assert not missing, (
        f"{missing} are in docs/ and not in docs/README.md. The index is what a reader "
        "trusts instead of opening the folder, so a document it omits is one nobody finds"
    )


def test_the_index_links_nothing_that_moved() -> None:
    """The other direction. A dead link is a worse answer than no link.

    Cheap to break: `docs/design/` filenames are cited from `config.py`,
    `seeding.py` and `pyproject.toml` too, so a rename has more than one place
    to go stale -- this catches the one that is a link.
    """
    dead = sorted(
        target
        for target in LINK.findall(INDEX.read_text(encoding="utf-8"))
        if not (DOCS / target).exists()
    )

    assert not dead, f"docs/README.md links {dead}, which are not there"


def test_the_index_is_reachable_from_the_front_page() -> None:
    """An index nothing points at is a document, not an index.

    The README already sends people to `docs/formats.md`; this is the other
    thing in that folder worth naming, and without a link from the front page a
    reader finds it by listing the directory -- which is what the index is for
    saving them.
    """
    readme = (repository_root() / "README.md").read_text(encoding="utf-8")

    assert "docs/README.md" in readme, "the front page does not mention the index"
