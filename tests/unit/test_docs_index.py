"""`docs/` is small on purpose now, and three rules keep it that way.

Thirty-eight documents became five when the design history was condensed into
`decisions.md` and `findings.md`. That is worth about 106,000 tokens an agent no
longer greps through, and it decays the moment somebody adds a document nothing
points at, or a status line quietly turns `docs/design/` back into an archive.
"""

from __future__ import annotations

import ast
import importlib
import re

import pytest

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
    """A document nobody links is one nobody finds."""
    text = INDEX.read_text(encoding="utf-8")
    linked = {(DOCS / target).resolve() for target in LINK.findall(text)}
    present = {p.resolve() for p in DOCS.rglob("*.md") if p.resolve() != INDEX.resolve()}

    missing = sorted(p.relative_to(DOCS).as_posix() for p in present - linked)

    assert not missing, (
        f"{missing} are in docs/ and not in docs/README.md. The index is what a reader "
        "trusts instead of opening the folder, so a document it omits is one nobody finds"
    )


def test_the_index_links_nothing_that_moved() -> None:
    """The other direction, and the one this change could most easily have broken."""
    dead = sorted(
        target
        for target in LINK.findall(INDEX.read_text(encoding="utf-8"))
        if not (DOCS / target).exists()
    )

    assert not dead, f"docs/README.md links {dead}, which are not there"


#: `decisions.md` carries its own index, one row per section, and the page is
#: long enough that the index is what a reader trusts instead of scrolling.
DECISIONS = DOCS / "decisions.md"

#: A GitHub heading anchor: lowercased, punctuation dropped, spaces to hyphens.
#: Enough for the headings this page has -- none of them repeat, which is what
#: would otherwise need a `-1` suffix.
def _anchor(heading: str) -> str:
    return re.sub(r"[^a-z0-9 -]", "", heading.lower()).replace(" ", "-")


def _sections(text: str) -> list[str]:
    return [line[3:].strip() for line in text.splitlines() if line.startswith("## ")]


def test_the_decisions_index_and_its_sections_agree() -> None:
    """An index that has quietly stopped being complete is worse than none.

    Named rather than counted, for the reason the rule above it gives: "19 != 20"
    does not say which section to go and look at.
    """
    text = DECISIONS.read_text(encoding="utf-8")
    preamble = text.split("\n## ", 1)[0]

    linked = set(re.findall(r"\]\(#([a-z0-9-]+)\)", preamble))
    present = {_anchor(h): h for h in _sections(text)}

    unindexed = sorted(present[a] for a in present.keys() - linked)
    assert not unindexed, (
        f"{unindexed} are sections of decisions.md with no row in its index — "
        "the index is what a reader trusts instead of scrolling, so a section it "
        "omits is one nobody finds"
    )

    dangling = sorted(linked - present.keys())
    assert not dangling, (
        f"the decisions index links {dangling}, which are not headings — a row "
        "left behind by a rename points at nothing while looking like an answer"
    )


def test_the_page_index_does_not_cite_a_decisions_section_that_moved() -> None:
    """`docs/README.md` names sections of `decisions.md` to send a reader to one."""
    cited = set(re.findall(r"under \*([^*]+)\* in\s+`?decisions\.md",
                           INDEX.read_text(encoding="utf-8")))
    headings = set(_sections(DECISIONS.read_text(encoding="utf-8")))

    assert cited, "no section citations found — this rule is about nothing"
    assert cited <= headings, (
        f"docs/README.md sends a reader to {sorted(cited - headings)} in "
        "decisions.md, which has no such section"
    )


def test_design_holds_only_what_is_still_proposed() -> None:
    """The rule that stops the folder becoming an archive again.

    `docs/design/` used to be history -- twenty-seven documents, twenty-five of them
    describing work long since finished, and about 90,000 tokens an agent would grep
    through to answer a question `decisions.md` now answers in three lines. It is now
    for arguments still being made.
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
    """An index nothing points at is just another document."""
    assert "docs/README.md" in (ROOT / "README.md").read_text(encoding="utf-8"), (
        "the front page does not mention the index"
    )

    instructions = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    for page in (
        "docs/guides/formats.md",
        "docs/guides/tools.md",
        "docs/decisions.md",
        "docs/findings.md",
    ):
        assert page in instructions, f"CLAUDE.md does not send an agent to {page}"


#: A path to a document, as prose names one, wherever it is written.
#: Deliberately not spelled out in the comment above: this pattern matches its own
#: example, and the first run of this rule reported itself.
CITATION = re.compile(r"docs/[A-Za-z0-9_./-]+\.md")

#: Where a citation can hide. Everything tracked that is text and is *not* under
#: `docs/`, because that directory has its own two rules above and because
#: `docs/README.md` names removed documents on purpose -- its recovery commands
#: are `git show <commit>^:docs/design/...`, which are supposed to be paths that
#: no longer resolve.
#: `assets_examples` joined the four when `call_cap.py` started naming
#: `guides/middleware.md`. It was outside the rule for no reason anybody had
#: stated -- it is tracked text and it is not under `docs/`, which is the whole
#: of what the sentence above asks for.
CITED_FROM = ("src", "tests", "service", ".github", "assets_examples")

#: The root files, named rather than globbed: the repository root also holds a
#: `.venv`, a `.git` and whatever a developer left there, and a rule that reads
#: the working directory finds different things on different machines.
CITED_FROM_FILES = ("Dockerfile", "compose.yaml", "pyproject.toml", "CLAUDE.md", "README.md")

#: Suffixes worth opening. `Dockerfile` has none, which is why the files above
#: are named individually rather than filtered.
TEXT = {".py", ".md", ".toml", ".yaml", ".yml"}


def _cited() -> list[tuple[str, int, str]]:
    """`(file, line, path)` for every document cited outside `docs/`."""
    files = [ROOT / name for name in CITED_FROM_FILES]
    for folder in CITED_FROM:
        files += [p for p in sorted((ROOT / folder).rglob("*")) if p.suffix in TEXT]

    found = []
    for path in files:
        if not path.is_file():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            found += [
                (path.relative_to(ROOT).as_posix(), number, cited)
                for cited in CITATION.findall(line)
            ]
    return found


def test_no_code_cites_a_document_that_is_not_there() -> None:
    """A pointer to a deleted document is worse than none.

    Found by doing it. Retiring the last two proposals on 2026-09-04 left
    `Dockerfile` naming `nothing-at-rest-on-this-machine.md`, and
    `presentation/cli/__init__.py` was already naming `the-verb-that-runs-a-task.md`,
    removed days earlier by the commit that was cleaning up after it.
    """
    cited = _cited()
    assert cited, "nothing was checked -- the collector found no citations at all"

    dead = [
        f"{where}:{line} cites {path}"
        for where, line, path in cited
        if not (ROOT / path).is_file()
    ]

    assert not dead, (
        "these name a document that is not there:\n  "
        + "\n  ".join(dead)
        + "\nPoint them at what replaced it, or say the argument in place -- "
        "`decisions.md` is where a removed document's reasoning went"
    )


#: ```python fences, with the line the fence opens on so a failure is clickable.
PYTHON_FENCE = re.compile(r"^```python\n(.*?)^```", re.M | re.S)

#: Which documents have their Python held to the package, and which do not.
#:
#: The split is the one `CLAUDE.md` already draws. A document describing what
#: exists can be checked against it. **A proposal names things that do not exist
#: yet, which is what makes it a proposal** -- so resolving its imports would
#: fail for the one reason that is not a defect. Every entry is checked today,
#: because every document left describes what exists; the `False` case is kept
#: in the type rather than deleted, since the next proposal will need it.
#:
#: Deny by default. A new document with Python in it fails
#: `test_every_document_with_python_is_classified` until it appears here, which
#: is where somebody decides which kind it is rather than discovering later that
#: nothing looked.
CHECKED_SNIPPETS: dict[str, bool] = {
    "README.md": True,
    "docs/guides/formats.md": True,
    "docs/guides/middleware.md": True,
    "docs/guides/ports.md": True,
    "docs/guides/tools.md": True,
}


def _documents_with_python() -> set[str]:
    """Every tracked document carrying at least one ```python fence."""
    found = set()
    for path in [ROOT / "README.md", *sorted(DOCS.rglob("*.md"))]:
        if PYTHON_FENCE.search(path.read_text(encoding="utf-8")):
            found.add(path.relative_to(ROOT).as_posix())
    return found


def _snippets() -> list[tuple[str, int, str]]:
    """`(document, line, source)` for every fence in a checked document."""
    out = []
    for name, checked in sorted(CHECKED_SNIPPETS.items()):
        if not checked:
            continue
        text = (ROOT / name).read_text(encoding="utf-8")
        for match in PYTHON_FENCE.finditer(text):
            out.append((name, text[: match.start()].count("\n") + 1, match.group(1)))
    return out


def _snippet_id(case: tuple[str, int, str]) -> str:
    name, line, _ = case
    return f"{name}:{line}"


def test_every_document_with_python_is_classified() -> None:
    """A document nobody classified is one nobody checks, silently."""
    present = _documents_with_python()
    assert present == set(CHECKED_SNIPPETS), (
        "documents with Python that CHECKED_SNIPPETS does not classify: "
        f"{sorted(present - set(CHECKED_SNIPPETS))}; classified but carrying no "
        f"Python any more: {sorted(set(CHECKED_SNIPPETS) - present)}"
    )


@pytest.mark.parametrize("case", _snippets(), ids=_snippet_id)
def test_a_documented_snippet_parses(case: tuple[str, int, str]) -> None:
    """Code in a document is code. Nothing imports it, so nothing compiled it."""
    name, line, source = case
    try:
        ast.parse(source)
    except SyntaxError as exc:
        pytest.fail(f"{name}:{line} does not parse -- {exc.msg} at offset {exc.offset}")


@pytest.mark.parametrize("case", _snippets(), ids=_snippet_id)
def test_a_documented_snippet_imports_what_exists(case: tuple[str, int, str]) -> None:
    """The check that would have caught the bug this rule was written after."""
    name, line, source = case
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ImportFrom) or node.level:
            continue
        if not node.module or node.module.split(".")[0] != "kingfisher":
            continue
        try:
            module = importlib.import_module(node.module)
        except ImportError as exc:
            pytest.fail(f"{name}:{line} imports {node.module}, which does not import -- {exc}")
        for alias in node.names:
            assert hasattr(module, alias.name), (
                f"{name}:{line} imports {alias.name} from {node.module}, which does "
                "not define it"
            )


def test_the_snippet_collector_finds_the_fences_it_claims_to() -> None:
    """A rule parametrised over an empty list passes."""
    collected = {name for name, _, _ in _snippets()}
    assert collected == {
        "README.md",
        "docs/guides/formats.md",
        "docs/guides/middleware.md",
        "docs/guides/ports.md",
        "docs/guides/tools.md",
    }
    assert len(_snippets()) >= 11, "the fences stopped being found"

    # The classifier has to actually read files, not trust the table: a document
    # listed as carrying Python while carrying none is the entry to delete.
    assert "docs/decisions.md" not in _documents_with_python()
