"""The shipped tools that read a file, and what bounds the work when they do.

A tool runs in kingfisher's own process, on the turn's thread, outside every fence the
shell has -- and the turn deadline cannot stop it, because `overrun` is read between
stream chunks. So whatever a tool is going to spend has to be bounded by the tool.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any

import pytest

from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.definitions.tools.catalogue import LocalToolRepository, tool_name
from kingfisher.plugins import backend_at
from tests.conftest import a_pdf, on_backend


@pytest.fixture
def tools(shipped):
    return {tool_name(one.tool): one.tool for one in LocalToolRepository(shipped / "tools").found}


@pytest.fixture
def handed(cfg, session_dir, fake_model):
    """The backend a turn hands its tools."""
    return build_agent(
        cfg, backend=backend_at(cfg, session_dir), model=fake_model
    ).context.backend


def _module_of(tool: Any) -> dict[str, Any]:
    """The module a tool was defined in, whichever shape it was written in."""
    if hasattr(tool, "func"):
        return tool.func.__globals__
    if hasattr(tool, "_run"):
        return type(tool)._run.__globals__
    return tool.__globals__


def _readers(shipped) -> dict[str, dict[str, Any]]:
    """Every shipped tool whose module reads a file, by name.

    Discovered rather than listed, so a tool added to the set is covered the day it
    ships -- and `test_a_tool_reads_a_file_one_way` is the other half, since a tool
    reading one some other way would be invisible to this walk.
    """
    found = {}
    for one in [entry.tool for entry in LocalToolRepository(shipped / "tools").found]:
        module = _module_of(one)
        if "_fetched" in module:
            found[tool_name(one)] = module
    return found


@dataclass
class _Got:
    """What a backend hands back for one file."""

    content: bytes | None
    error: str | None = None


class _Backend:
    """A backend holding one answer, so a tool's own read is what is under test."""

    def __init__(self, content: bytes) -> None:
        self._content = content

    def download_files(self, paths: list[str]) -> list[_Got]:
        return [_Got(self._content) for _ in paths]


@dataclass
class _Runtime:
    context: Any


def _asked(content: bytes) -> _Runtime:
    return _Runtime(context=type("_Ctx", (), {"backend": _Backend(content)})())


#: Every shipped file that asks a backend for a file, by its path under the assets.
#: Named rather than counted: `assert len(...) >= 2` stood here against nine, so a walk
#: that lost seven of them passed four rules -- the threshold shape the commit before
#: deleted from `test_shipped_assets.py` with the reason written out, and then this one
#: wrote again.
#:
#: Paths rather than tool names, and that is what reaches the ninth. The behavioural
#: rules below load what `tools/` offers, and `mask_secrets` is a *subagent's own* tool,
#: two directories further down: it shipped reading a file with no cap at all while all
#: six rules here passed, because none of them walked where it lives.
FETCHES_A_FILE = frozenset({
    "subagents/redactor/tools/mask_secrets.py",
    "tools/bar_chart.py",
    "tools/csv_filter.py",
    "tools/csv_profile/profile.py",
    "tools/line_count.py",
    "tools/logs/access/status_codes.py",
    "tools/logs/log_levels.py",
    "tools/pdf_pages.py",
    "tools/pdf_text.py",
})


def _fetches_a_file(shipped) -> dict[str, str]:
    """Every shipped `.py` whose source asks a backend for a file, by path and source.

    The whole tree, not the tool catalogue: a bundled tool is a tool, and it reads a
    file the same way.
    """
    return {
        path.relative_to(shipped).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(shipped.rglob("*.py"))
        if "download_files" in path.read_text(encoding="utf-8")
    }


def test_every_shipped_file_that_reads_one_is_named_here(shipped):
    """Both directions, so neither a new reader nor a deleted one passes quietly. This
    is the universe the rules below stand on, and it has been wrong twice: once as a
    count, and once as a walk that did not reach a subagent's own folder.
    """
    walked = set(_fetches_a_file(shipped))

    assert walked == FETCHES_A_FILE


#: Every shipped file that parses a CSV, by its path under the assets. Named rather than
#: counted, and that is the whole reason this set exists: the comment in all three of
#: them, the guide and `decisions.md` each said *four* shipped CSV tools from the day it
#: was written, when there were three -- so the count was never a fact about the tree,
#: and nothing could tell it from one.
PARSES_A_CSV = frozenset({
    "tools/bar_chart.py",
    "tools/csv_filter.py",
    "tools/csv_profile/profile.py",
})


def _parses_a_csv(shipped) -> dict[str, str]:
    """Every shipped `.py` whose source parses a CSV, by path and source.

    `csv.reader` rather than `import csv`: `sql_query` writes its answer as CSV and
    parses none, so the reader's field limit cannot reach it.
    """
    return {
        path.relative_to(shipped).as_posix(): body
        for path in sorted(shipped.rglob("*.py"))
        if "csv.reader" in (body := path.read_text(encoding="utf-8"))
    }


def test_every_shipped_file_that_parses_a_csv_is_named_here(shipped):
    """Both directions, so neither a new parser nor a deleted one passes quietly."""
    walked = set(_parses_a_csv(shipped))

    assert walked == PARSES_A_CSV


def _guards_the_field_limit(body: str) -> bool:
    """Whether this source both takes the reader's field limit and converts its failure.

    Read off the syntax rather than searched for as text, because the words survive the
    thing going away: `MAX_FIELD_BYTES` is named again in the refusal message, and the
    comment above it writes `_csv.Error`, which holds `csv.Error` inside it. Measured --
    as substrings, renaming the assignment and retyping the handler both left this
    green.
    """
    tree = ast.parse(body)
    takes = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "field_size_limit"
        for node in ast.walk(tree)
    )
    converts = any(
        isinstance(node, ast.ExceptHandler)
        and isinstance(node.type, ast.Attribute)
        and node.type.attr == "Error"
        and isinstance(node.type.value, ast.Name)
        and node.type.value.id == "csv"
        for node in ast.walk(tree)
    )
    return takes and converts


def test_every_csv_parser_answers_the_cell_it_cannot_read(shipped):
    """`csv` raises on a field past `csv.field_size_limit()`, which is 131,072 against
    the 32 MB these admit -- a cell of 128 KB. Unconverted it reaches the model as
    `Error: Error: field larger than field limit (131072)`, naming no file, no column
    and no remedy.

    Over the walk rather than the three names, so a fourth parser is held the day it
    ships: these are examples copied one file at a time, and the behavioural rules for
    this are written per tool, so a new one arrives with nothing asking whether it
    converts anything.
    """
    parsers = _parses_a_csv(shipped)
    assert parsers, "no shipped file parses a CSV -- this rule is about nothing"

    unguarded = [
        where for where, body in sorted(parsers.items()) if not _guards_the_field_limit(body)
    ]
    assert not unguarded, (
        f"{unguarded} parse a CSV without taking the reader's limit and turning its "
        "one failure into an answer -- name the file and the limit, as the others do"
    )


def test_the_field_limit_rule_reads_the_syntax_and_not_the_words():
    """The half the shipped tools cannot drive, all three of them being guarded: with
    nothing to refuse there, accepting every parser left this rule green.
    """
    guarded = (
        "import csv\n"
        "MAX_FIELD_BYTES = csv.field_size_limit()\n"
        "def parse(rows):\n"
        "    try:\n"
        "        return list(rows)\n"
        "    except csv.Error as exc:\n"
        "        raise ValueError(f'past {MAX_FIELD_BYTES} characters') from exc\n"
    )
    assert _guards_the_field_limit(guarded)

    # The limit written out instead of asked for: its name still in the message below,
    # which is what a text search finds.
    assert not _guards_the_field_limit(
        guarded.replace("csv.field_size_limit()", "131_072")
    )
    # And the one failure no longer converted, with `csv.Error` still in the source.
    assert not _guards_the_field_limit(
        guarded.replace("except csv.Error as exc", "except KeyError as exc")
        + "#: said of `csv.Error` in a comment\n"
    )


def _helper_in(body: str) -> str:
    """The `_fetched` helper as one file spells it, with its cap above it."""
    start = body.index("#: What one call reads.")
    end = body.index("return got.content", start) + len("return got.content")
    return body[start:end]


def test_a_tool_reads_a_file_one_way(shipped):
    """Every shipped file that asks a backend for a file carries the same `_fetched`,
    with the same cap above it.

    Nine copies is the arrangement these examples are in on purpose -- each one is read
    on its own -- and the hazard that comes with it is one copy drifting. The cap is the
    thing worth not drifting: a file reading one some other way, or an older `_fetched`
    pasted into a new tool, is one with no bound at all. That is not hypothetical;
    `mask_secrets` was exactly that, and the version of this rule that walked the tool
    catalogue could not see it.
    """
    spellings: dict[str, list[str]] = {}
    unbounded = []
    for where, body in sorted(_fetches_a_file(shipped).items()):
        if "_fetched" not in body or "MAX_INPUT_BYTES" not in body:
            unbounded.append(where)
            continue
        spellings.setdefault(_helper_in(body), []).append(where)

    assert not unbounded, f"{unbounded} read a file with no bound of their own"
    assert len(spellings) == 1, f"`_fetched` is written {len(spellings)} ways: {spellings}"


def test_a_file_past_the_cap_is_refused(shipped):
    """A backend hands a file back whole, into this process, and the parse that follows
    is on the turn's thread -- so an unbounded read is a turn nothing can stop.
    Measured on the CSV path: 32 MB peaked at 358 MB of RSS, 188 MB at 1.8 GB.
    """
    for name, module in sorted(_readers(shipped).items()):
        cap = module["MAX_INPUT_BYTES"]
        with pytest.raises(ValueError, match="past the") as refused:
            module["_fetched"](_asked(b"x" * (cap + 1)), "/inputs/big.dat")

        said = str(refused.value)
        assert str(cap) in said, name
        assert str(cap + 1) in said, f"{name} does not say how big the file was"


def test_a_file_at_the_cap_is_read(shipped):
    """The boundary, and the control the rule above needs: a cap that refused
    everything would pass it. At the cap exactly, since that is the off-by-one that
    would make a documented limit a lie.
    """
    for name, module in sorted(_readers(shipped).items()):
        cap = module["MAX_INPUT_BYTES"]

        assert len(module["_fetched"](_asked(b"x" * cap), "/inputs/big.dat")) == cap, name


def test_every_reader_names_the_same_cap(shipped):
    """One number, so a model told a size by one tool is not refused by the next.

    It is a constant per module rather than a shared import on purpose -- these are
    examples a deployment copies one file at a time -- and that is exactly why every
    copy has to be compared against the others somewhere. No count here: this file
    said "seven" beside a docstring saying nine, against a tree holding nine, which is
    the drift `FETCHES_A_FILE` above is pinned by path to keep out.
    """
    caps = {name: module["MAX_INPUT_BYTES"] for name, module in _readers(shipped).items()}

    assert len(set(caps.values())) == 1, caps


class _Watched:
    """One page, forwarding what the tool reads and recording what it releases."""

    def __init__(self, page: Any, released: list[tuple[str, int]]) -> None:
        self._page = page
        self._released = released

    def extract_text(self, *args: Any, **kwargs: Any) -> Any:
        return self._page.extract_text(*args, **kwargs)

    def flush_cache(self) -> None:
        self._released.append(("flush_cache", id(self._page)))
        self._page.flush_cache()

    @property
    def get_textmap(self) -> Any:
        released, page = self._released, self._page

        class _Map:
            def cache_clear(self) -> None:
                released.append(("get_textmap", id(page)))
                page.get_textmap.cache_clear()

        return _Map()


class _WatchedPages:
    def __init__(self, pages: Any, released: list[tuple[str, int]]) -> None:
        self._seen: dict[int, _Watched] = {}
        self._pages = pages
        self._released = released

    def __len__(self) -> int:
        return len(self._pages)

    def __getitem__(self, index: int) -> Any:
        if index not in self._seen:
            self._seen[index] = _Watched(self._pages[index], self._released)
        return self._seen[index]


def test_each_page_is_released_as_soon_as_its_text_is_out(tools, handed, session_dir, monkeypatch):
    """pdfplumber holds a page's objects and its text map until the document closes, so
    at this tool's own cap the whole document's worth was alive at once: 500 dense pages
    peaked at 1,655 MB of RSS from a 1.6 MB file. Released per page it is 93 MB, and a
    quarter faster.

    Both calls are asserted because either alone leaves most of it: measured, 1,020 MB
    with `flush_cache` only and 1,626 MB with `get_textmap.cache_clear` only. The cap
    did not have to move, which is why this is a release and not a smaller number.
    """
    import pdfplumber

    released: list[tuple[str, int]] = []
    opened = pdfplumber.open

    class _Pdf:
        def __init__(self, pdf: Any) -> None:
            self._pdf = pdf
            self.pages = _WatchedPages(pdf.pages, released)

        def __enter__(self) -> Any:
            return self

        def __exit__(self, *args: Any) -> None:
            self._pdf.close()

    monkeypatch.setattr(pdfplumber, "open", lambda *a, **k: _Pdf(opened(*a, **k)))
    (session_dir / "inputs" / "few.pdf").write_bytes(a_pdf("one", "two", "three"))

    said = on_backend(tools["pdf_text"], handed, file_path="/inputs/few.pdf")

    assert "pages 1-3 of 3" in said, "it still answered, so the proxy did not change it"
    assert [kind for kind, _ in released].count("flush_cache") == 3
    assert [kind for kind, _ in released].count("get_textmap") == 3
    assert len({at for _, at in released}) == 3, "three pages, each released once"
