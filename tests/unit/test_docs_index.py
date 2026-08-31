"""`docs/` is small on purpose now, and three rules keep it that way.

Thirty-eight documents became five when the design history was condensed into
`decisions.md` and `findings.md`. That is worth about 106,000 tokens an agent no
longer greps through, and it decays the moment somebody adds a document nothing
points at, or a status line quietly turns `docs/design/` back into an archive.

Nothing else in this repository opens these files, so nothing else would notice.
"""

from __future__ import annotations

import re

from tests.conftest import repository_root

#: `[text](path)` with a relative target. Absolute URLs are somebody else's
#: files and cannot be checked by looking at disk.
LINK = re.compile(r"\[[^\]]+\]\((?!https?://)([^)#]+)")

ROOT = repository_root()
DOCS = ROOT / "docs"
INDEX = DOCS / "README.md"

#: A design document that has been built has somewhere else to be: its decisions
#: in `decisions.md`, anything it measured about upstream in `findings.md`. These
#: are the words the old documents opened their status with for "built".
#:
#: Matched on the *first* word, because a substring search does not work here and
#: quietly said the opposite: "designed, not implemented" contains "implemented",
#: so both surviving proposals were reported as settled the first time this ran.
BUILT = ("implemented", "built", "audited")


def test_the_index_lists_every_document() -> None:
    """A document nobody links is one nobody finds.

    Named individually rather than counted, because "4 != 5" does not say which
    one to go and read.
    """
    text = INDEX.read_text(encoding="utf-8")
    linked = {(DOCS / target).resolve() for target in LINK.findall(text)}
    present = {p.resolve() for p in DOCS.rglob("*.md") if p.resolve() != INDEX.resolve()}

    missing = sorted(p.relative_to(DOCS).as_posix() for p in present - linked)

    assert not missing, (
        f"{missing} are in docs/ and not in docs/README.md. The index is what a reader "
        "trusts instead of opening the folder, so a document it omits is one nobody finds"
    )


def test_the_index_links_nothing_that_moved() -> None:
    """The other direction, and the one this change could most easily have broken.

    Twenty-five documents were deleted here. `config.py`, `seeding.py` and
    `pyproject.toml` cited three of them by path and were repointed at
    `decisions.md`; this catches the same mistake made in a link.
    """
    dead = sorted(
        target
        for target in LINK.findall(INDEX.read_text(encoding="utf-8"))
        if not (DOCS / target).exists()
    )

    assert not dead, f"docs/README.md links {dead}, which are not there"


def test_design_holds_only_what_is_still_proposed() -> None:
    """The rule that stops the folder becoming an archive again.

    `docs/design/` used to be history -- twenty-seven documents, twenty-five of
    them describing work long since finished, and about 90,000 tokens an agent
    would grep through to answer a question `decisions.md` now answers in three
    lines. It is now for arguments still being made.

    So a document here that says it was built has finished its job: its decisions
    belong in `decisions.md`, anything it measured about deepagents belongs in
    `findings.md`, and the file belongs in git history. This fails when one stays.
    """
    settled = []
    for path in sorted((DOCS / "design").glob("*.md")):
        status = next(
            (
                line
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.startswith("**Status:**")
            ),
            "",
        )
        opening = status.removeprefix("**Status:**").strip().lower()
        first = re.split(r"[^a-z]+", opening, maxsplit=1)[0]
        if first in BUILT:
            settled.append(path.name)

    assert not settled, (
        f"{settled} are in docs/design/ and say they were built. Move the decisions to "
        "docs/decisions.md and anything measured about upstream to docs/findings.md, "
        "then delete the file -- git keeps it, and agents stop paying to grep it"
    )


def test_the_front_page_and_the_agent_instructions_both_point_here() -> None:
    """An index nothing points at is just another document.

    Two readers, two doors. A person arrives at `README.md`; an agent is handed
    `CLAUDE.md` before it does anything, and the whole reason that file exists is
    to stop it searching for what these three pages already say.
    """
    assert "docs/README.md" in (ROOT / "README.md").read_text(encoding="utf-8"), (
        "the front page does not mention the index"
    )

    instructions = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    for page in ("docs/formats.md", "docs/decisions.md", "docs/findings.md"):
        assert page in instructions, f"CLAUDE.md does not send an agent to {page}"
