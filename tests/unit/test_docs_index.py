"""`docs/` is small on purpose now, and three rules keep it that way.

Thirty-eight documents became five when the design history was condensed into
`decisions.md` and `findings.md`. That is worth about 106,000 tokens an agent no
longer greps through, and it decays the moment somebody adds a document nothing
points at, or a status line quietly turns `docs/design/` back into an archive.

Nothing else in this repository opens these files, so nothing else would notice.
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

    The folder is empty as of 2026-09-04, so this is a standing guard rather than
    a live check -- kept because the rule is about what happens when somebody adds
    the next proposal, and that is exactly when nobody is thinking about it.
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
    for page in (
        "docs/guides/formats.md",
        "docs/guides/tools.md",
        "docs/decisions.md",
        "docs/findings.md",
    ):
        assert page in instructions, f"CLAUDE.md does not send an agent to {page}"


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
    """A document nobody classified is one nobody checks, silently.

    The same shape as `THIRD_PARTY` in `test_architecture`: the table is what
    has to be edited to take something on, and editing it is where the question
    gets asked.
    """
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
    """The check that would have caught the bug this rule was written after.

    `docs/guides/formats.md` told a reader to write `from
    kingfisher.infrastructure.subagent_store import LocalSubagentRepository`.
    That module had not existed for two refactors, and the line below it reached
    for `cfg.subagents_dir`, a property removed on purpose. Both had been wrong
    since before anyone last read the page.

    Parsing alone would not have caught either one -- both are valid Python.
    What was missing is that nothing ever resolved the names, because a fenced
    block is imported by nothing and the architecture suite parses `src/` alone.

    Attributes as well as modules, since `subagent_store` was only half of it:
    a module that still exists having lost the name a reader is told to import
    from it is the same failure arriving one line later.
    """
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
    """A rule parametrised over an empty list passes.

    That is how a collector reading the wrong root went unnoticed in
    `test_architecture` twice, and this one reads two roots -- `README.md` is
    not under `docs/`. Both are asserted present, and the count is asserted
    non-trivial rather than exact, so adding an example does not fail a test
    about collection.
    """
    collected = {name for name, _, _ in _snippets()}
    assert collected == {"README.md", "docs/guides/formats.md", "docs/guides/tools.md"}
    assert len(_snippets()) >= 11, "the fences stopped being found"

    # The classifier has to actually read files, not trust the table: a document
    # listed as carrying Python while carrying none is the entry to delete.
    assert "docs/decisions.md" not in _documents_with_python()
