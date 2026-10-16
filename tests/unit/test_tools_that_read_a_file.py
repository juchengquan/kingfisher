"""The shipped tools that read a file, and what bounds the work when they do.

A tool runs in kingfisher's own process, on the turn's thread, outside every fence the
shell has -- and the turn deadline cannot stop it, because `overrun` is read between
stream chunks. So whatever a tool is going to spend has to be bounded by the tool.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from kingfisher import backend_at
from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.definitions.tools.catalogue import LocalToolRepository, tool_name
from tests.conftest import a_pdf, on_backend


@pytest.fixture
def tools(shipped):
    return {tool_name(t): t for t in LocalToolRepository(shipped / "tools").tools}


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
    for one in LocalToolRepository(shipped / "tools").tools:
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
