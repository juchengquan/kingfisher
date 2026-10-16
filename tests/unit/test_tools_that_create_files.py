"""A tool that creates a file saves it, says where in one line, and never says it
saved a file that was refused.

Against the backend a build hands a tool, so a refused target is refused by the turn's
own rules. A bare backend refuses nothing, and a tool that ignores `.error` passes
against it.
"""

from __future__ import annotations

import csv
import io
import re
from xml.etree import ElementTree

import pytest
from pypdf import PdfReader

from kingfisher import backend_at
from kingfisher.infrastructure.harness.agent import build_agent
from kingfisher.infrastructure.harness.middlewares.large_pdf_guard import MAX_PDF_BYTES
from kingfisher.kinds.tools.catalogue import LocalToolRepository, tool_name
from tests.conftest import a_pdf, on_backend, repository_root

SALES = (
    "region,sales\n"
    "north,10\n"
    "south,25.5\n"
    "east,7\n"
    "west,12\n"
    "R&D <lab>,3\n"
    "central,0\n"
    "islands,\n"
    "offshore,n/a\n"
)

#: Three pages, the second with no text on it, as a scanned page has none.
REPORT = a_pdf("Revenue rose", "", "Costs fell")

#: Each tool, the arguments of one ordinary call, where it saves when not told, and a
#: `save_as` to tell it.
CALLS = {
    "bar_chart": (
        {"file_path": "/inputs/sales.csv"},
        "/outputs/chart.svg",
        "/scratchpad/try1.svg",
    ),
    "csv_filter": (
        {"file_path": "/inputs/sales.csv", "column": "region", "value": "north"},
        "/outputs/filtered.csv",
        "/scratchpad/north.csv",
    ),
    "pdf_text": (
        {"file_path": "/inputs/report.pdf"},
        "/scratchpad/report.p1-3.txt",
        "/scratchpad/try1.txt",
    ),
    "pdf_pages": (
        {"file_path": "/inputs/report.pdf", "pages": "1-2"},
        "/scratchpad/report.p1-2.pdf",
        "/scratchpad/try1.pdf",
    ),
}

LINE = re.compile(
    r"Saved (?P<name>\S+) \((?P<kind>[^,]+), (?P<size>\d+(?:\.\d)?) (?P<unit>B|KB|MB)\) "
    r"to (?P<path>/\S+)(?: — (?P<note>.+))?"
)


@pytest.fixture
def tools(shipped):
    return {tool_name(t): t for t in LocalToolRepository(shipped / "tools").tools}


@pytest.fixture
def handed(cfg, session_dir, fake_model):
    """The backend a turn hands its tools, with `SALES` and `REPORT` in `/inputs`."""
    (session_dir / "inputs" / "sales.csv").write_text(SALES, encoding="utf-8")
    (session_dir / "inputs" / "report.pdf").write_bytes(REPORT)
    return build_agent(cfg, backend=backend_at(cfg, session_dir), model=fake_model).context.backend


def _saved(backend, path: str) -> bytes | None:
    (got,) = backend.download_files([path])
    return got.content


@pytest.mark.parametrize("name", sorted(CALLS))
def test_a_file_goes_to_the_tools_default_when_save_as_is_left_out(name, tools, handed):
    """A default the result does not name is a file the agent has to go looking for."""
    args, default, _ = CALLS[name]

    said = on_backend(tools[name], handed, **args)

    assert _saved(handed, default)
    assert f" to {default} " in said


@pytest.mark.parametrize("name", sorted(CALLS))
def test_save_as_is_where_the_file_goes(name, tools, handed):
    """A `save_as` the tool ignored would leave every draft overwriting the last."""
    args, default, elsewhere = CALLS[name]

    said = on_backend(tools[name], handed, **args, save_as=elsewhere)

    assert _saved(handed, elsewhere)
    assert _saved(handed, default) is None, "saved to the default as well"
    assert f" to {elsewhere} " in said


@pytest.mark.parametrize("name", sorted(CALLS))
def test_a_refused_target_is_a_failure_and_nothing_is_written(name, tools, handed):
    """`upload_files` hands a refusal back on the response rather than raising it, so a
    tool that never reads `.error` answers "Saved" for a file that does not exist.
    """
    args, default, _ = CALLS[name]
    refused = f"/inputs/x{default[default.rindex('.') :]}"

    with pytest.raises(OSError, match=f"not saved to {refused}: permission_denied"):
        on_backend(tools[name], handed, **args, save_as=refused)

    assert _saved(handed, refused) is None
    assert _saved(handed, default) is None, "fell back to the default instead"


@pytest.mark.parametrize(("name", "kind", "unit"), [
    ("bar_chart", "SVG image", "KB"),
    ("csv_filter", "CSV", "B"),
    ("pdf_text", "text", "B"),
    ("pdf_pages", "PDF", "B"),
])
def test_the_result_is_one_line_naming_the_file_its_kind_size_and_place(
    name, kind, unit, tools, handed
):
    """The line is all the model is told, so a size that is not the file's, or a path
    that is not where it went, is believed.
    """
    args, default, _ = CALLS[name]

    said = on_backend(tools[name], handed, **args)

    found = LINE.fullmatch(said)
    assert found, said
    data = _saved(handed, default)
    assert data is not None
    assert (found["name"], found["kind"], found["path"]) == (
        default.rsplit("/", 1)[1],
        kind,
        default,
    )
    # Both units: the chart is past a kilobyte and the others are not.
    assert found["unit"] == unit
    shown = f"{len(data) / 1024:.1f}" if unit == "KB" else str(len(data))
    assert found["size"] == shown
    assert found["note"], "no note, though each tool has something to say"


def test_the_chart_is_an_svg_a_viewer_can_open(tools, handed):
    """A label holding `&` or `<` written as it stands makes a file no viewer opens,
    and the tool still answers "Saved".
    """
    said = on_backend(tools["bar_chart"], handed, file_path="/inputs/sales.csv")

    data = _saved(handed, "/outputs/chart.svg")
    assert data is not None
    root = ElementTree.fromstring(data)  # noqa: S314 -- our own tool's output
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    svg = "{http://www.w3.org/2000/svg}"
    labels = [one.text for one in root.iter(f"{svg}text")]
    assert "R&D <lab>" in labels
    # One bar per row with a number, `central`'s zero among them; the two without
    # one are left out and counted.
    assert len(list(root.iter(f"{svg}rect"))) == 6
    assert "6 bars" in said
    assert "2 row(s) with no number left out" in said


#: Every spelling that gets through `float()` with no length to draw. `Infinity` is
#: how Python also writes one, and `1e400` is a number that looks finite and overflows
#: to `inf` on the way in -- which is why the guard reads the number, not the text.
NO_LENGTH = ["inf", "nan", "Infinity", "-nan", "1e400"]


@pytest.mark.parametrize("written", NO_LENGTH)
def test_a_number_a_bar_cannot_be_as_long_as_is_refused(written, tools, handed, session_dir):
    """Every bar is drawn as a share of the longest, so one with no finite length takes
    every other bar's length away with it: measured, one `inf` row wrote `width="nan"`
    for itself, scaled every other bar to zero, and the answer said it had drawn them
    both. Refused like a negative one, which has nowhere to go for the same reason.
    """
    (session_dir / "inputs" / "odd.csv").write_text(
        f"name,n\nalpha,5\nbeta,{written}\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="finite length"):
        on_backend(tools["bar_chart"], handed, file_path="/inputs/odd.csv")

    assert _saved(handed, "/outputs/chart.svg") is None, "refused, so nothing was written"


#: Where a cell holding one of these ends up in the drawing. Both, because the tool
#: scrubs three cells and the rule covered one: with the character only ever in the
#: label, dropping the scrub from the *title* left 137 tests green -- and the title is
#: the header row, which is the half a caller does not choose row by row.
IN_THE_CHART = {
    "a label": ("name,n\nal{bad}pha,5\n", "alpha"),
    "the title": ("na{bad}me,n\nalpha,5\n", "n by name"),
}


@pytest.mark.parametrize("control", ["\x00", "\x0b", "\x1f"])
@pytest.mark.parametrize("where", sorted(IN_THE_CHART))
def test_a_cell_holding_what_xml_forbids_is_drawn_without_it(
    where, control, tools, handed, session_dir
):
    """XML 1.0 admits a tab, a newline and a carriage return below a space and nothing
    else at all -- not escaped, not as a character reference -- so a cell holding one
    of the others left a file no viewer can open, `not well-formed (invalid token)`,
    while the answer said `Saved chart.svg (SVG image, 335 B)`. `escape` does not touch
    them: it handles the markup characters and no more.

    Every cell that reaches the drawing, not just the one a row supplies. The tool
    scrubs the two header cells as well, and nothing reached them: mutating that call
    away left this file green while the title came out `n by na\x00me` and the SVG
    stopped parsing.
    """
    shape, expected = IN_THE_CHART[where]
    (session_dir / "inputs" / "odd.csv").write_text(
        shape.format(bad=control), encoding="utf-8"
    )

    said = on_backend(tools["bar_chart"], handed, file_path="/inputs/odd.csv")

    data = _saved(handed, "/outputs/chart.svg")
    assert data is not None
    root = ElementTree.fromstring(data)  # noqa: S314 -- our own tool's output
    assert expected in [one.text for one in root.iter("{http://www.w3.org/2000/svg}text")]
    assert "1 cell(s) drawn without a character an SVG cannot carry" in said


@pytest.mark.parametrize("control", ["\t", "\n", "\r"])
def test_a_cell_holding_a_character_xml_admits_keeps_it(control, tools, handed, session_dir):
    """The other half, and the reason the three are named rather than the range: a rule
    stripping every control character would pass the one above and quietly rewrite a
    label a file is entitled to hold.

    All three, because this drove the tab alone and the three are the whole claim:
    narrowing the set to `(0x09,)` left the file green while a legal label holding a
    newline was rewritten and counted as scrubbed.

    Quoted, which is how a CSV carries a newline inside one field -- unquoted it is a
    row boundary and the tool never sees one cell.

    Asserted on the file's bytes rather than on what the parser hands back, because XML
    normalises a carriage return to a newline on the way in: the character is in the
    document, and `ElementTree` returning `al\npha` for it is the standard working
    rather than the tool having changed anything. The parse is asserted separately,
    which is the half that would fail if the character were not admitted.
    """
    (session_dir / "inputs" / "odd.csv").write_text(
        f'name,n\n"al{control}pha",5\n', encoding="utf-8"
    )

    said = on_backend(tools["bar_chart"], handed, file_path="/inputs/odd.csv")

    data = _saved(handed, "/outputs/chart.svg")
    assert data is not None
    assert f"al{control}pha".encode() in data, "a character XML admits was taken out"
    ElementTree.fromstring(data)  # noqa: S314 -- our own tool's output
    assert "cannot carry" not in said


@pytest.mark.parametrize("name", ["bar_chart", "csv_filter"])
def test_a_cell_the_csv_reader_will_not_parse_is_said_rather_than_raised(
    name, tools, handed, session_dir
):
    """These two read a CSV the way the profile tools do and died the same way: one
    cell of 200,000 characters -- 0.6% of their own 32 MB cap -- and the model was
    handed `Error: Error: field larger than field limit (131072)`, naming no file, no
    column and no remedy.

    Both, because the sentence is written out in each file for the reason `_fetched` is:
    these are examples a deployment copies one at a time.
    """
    (session_dir / "inputs" / "wide.csv").write_text(
        'a,b\n1,"' + "y" * 200_000 + '"\n', encoding="utf-8"
    )
    asked = {"column": "a", "value": "1"} if name == "csv_filter" else {}

    with pytest.raises(ValueError, match="longer than the 131072 characters") as refused:
        on_backend(tools[name], handed, file_path="/inputs/wide.csv", **asked)

    assert "/inputs/wide.csv" in str(refused.value)
    assert "read it with `read_file`" in str(refused.value)


def test_the_filter_keeps_exactly_the_rows_whose_cell_equals_the_value(tools, handed, session_dir):
    """A match by prefix or by case keeps a row nobody asked for, a short row read past
    its end stops the export, and an export is believed without being read.
    """
    (session_dir / "inputs" / "orders.csv").write_text(
        "id,region,status\n"
        "1,north,open\n"
        "2,south,open\n"
        "3, north ,closed\n"
        "4,North,open\n"
        "5,northeast,open\n"
        "6\n",
        encoding="utf-8",
    )

    said = on_backend(
        tools["csv_filter"], handed, file_path="/inputs/orders.csv", column="region", value="north"
    )

    data = _saved(handed, "/outputs/filtered.csv")
    assert data is not None
    assert list(csv.reader(io.StringIO(data.decode("utf-8")))) == [
        ["id", "region", "status"],
        ["1", "north", "open"],
        ["3", " north ", "closed"],
    ]
    assert "2 of 6 rows" in said


def test_the_text_holds_each_page_asked_for_under_its_own_number(tools, handed, session_dir):
    """Counted from zero, each page's text sits under the number before it, and the
    agent quotes page 4 as page 3.
    """
    (session_dir / "inputs" / "long.pdf").write_bytes(a_pdf("one", "two", "three", "four", "five"))

    said = on_backend(tools["pdf_text"], handed, file_path="/inputs/long.pdf", pages="2-3,5")

    assert _saved(handed, "/scratchpad/long.p2-3_5.txt") == (
        b"--- page 2 ---\ntwo\n--- page 3 ---\nthree\n--- page 5 ---\nfive\n"
    )
    assert "pages 2-3, 5 of 5" in said


#: How a caller says "the whole document": by naming a span that covers it, and by
#: leaving `pages` out. Both ways in one test, because the cap was reported for the
#: first and not the second -- a span is clipped to the document before the cap is
#: applied, so the default span `1-500` had nothing past it to name on a 503-page
#: report, and the docstring recommends leaving it out.
WHOLE = {"the span named": False, "left to its default": True}


@pytest.mark.parametrize("way", sorted(WHOLE), ids=sorted(WHOLE))
def test_pages_past_the_cap_are_named_as_left_out(way, tools, handed, session_dir):
    """A cut that says nothing reads as the end of the document."""
    cap = tools["pdf_text"].func.__globals__["MAX_PAGES"]
    texts = [f"p{n}" for n in range(1, cap + 4)]
    (session_dir / "inputs" / "long.pdf").write_bytes(a_pdf(*texts))
    asked = {} if WHOLE[way] else {"pages": f"1-{cap + 3}"}

    said = on_backend(tools["pdf_text"], handed, file_path="/inputs/long.pdf", **asked)

    left = f"at most {cap} a call, so {cap + 1}-{cap + 3} left out"
    assert f"pages 1-{cap} of {cap + 3}; {left}" in said
    saved = _saved(handed, f"/scratchpad/long.p1-{cap}.txt")
    assert saved is not None
    assert f"--- page {cap} ---" in saved.decode()
    assert f"--- page {cap + 1} ---" not in saved.decode()


def test_a_span_the_document_satisfies_is_not_reported_as_cut_short(tools, handed, session_dir):
    """The other direction, and the one that keeps the rule above honest: the note
    names what the *caller* asked for and did not get, so a span this document has
    every page of must not carry it, however many pages are past the span.
    """
    cap = tools["pdf_text"].func.__globals__["MAX_PAGES"]
    (session_dir / "inputs" / "long.pdf").write_bytes(
        a_pdf(*[f"p{n}" for n in range(1, cap + 4)])
    )

    said = on_backend(tools["pdf_text"], handed, file_path="/inputs/long.pdf", pages="1-20")

    assert f"pages 1-20 of {cap + 3}" in said
    assert "left out" not in said


#: A span the document only partly has, and the part of it that is missing. Two
#: shapes, because a span cut at the end and a span naming a page far past it are the
#: same silence spelt differently.
PAST_THE_END = {"3-5,9": "4-5, 9", "2-9": "4-9"}


@pytest.mark.parametrize("pages", sorted(PAST_THE_END), ids=sorted(PAST_THE_END))
def test_pages_the_document_does_not_have_are_named_as_asked_for(
    pages, tools, handed, session_dir
):
    """The other half of what the rule above calls "what the caller asked for and did
    not get". A span is clipped to the document before it is expanded -- which is what
    keeps `1-99999999` cheap -- and the clip was silent: `3-5,9` on a three-page report
    answered `pages 3 of 3`, so a model that asked for nine pages and got one was told
    nothing had been left out, against a docstring promising it names them.

    The pages that *are* there are asserted too, since a note beside a missing page is
    no use if the extraction stopped at it.
    """
    (session_dir / "inputs" / "three.pdf").write_bytes(a_pdf("one", "two", "three"))

    said = on_backend(tools["pdf_text"], handed, file_path="/inputs/three.pdf", pages=pages)

    assert f"asked for {PAST_THE_END[pages]}, which this document does not have" in said
    saved = _saved(handed, f"/scratchpad/three.p{'3' if pages == '3-5,9' else '2-3'}.txt")
    assert saved is not None and "three" in saved.decode()


def test_a_span_the_document_has_every_page_of_is_not_told_it_is_missing_any(
    tools, handed, session_dir
):
    """The control this one needs of its own: the rule above is satisfied by a note on
    every answer, and the cap's control next door asserts on the word `left out`,
    which this note does not use.
    """
    (session_dir / "inputs" / "three.pdf").write_bytes(a_pdf("one", "two", "three"))

    said = on_backend(tools["pdf_text"], handed, file_path="/inputs/three.pdf", pages="1-3")

    assert "pages 1-3 of 3" in said
    assert "does not have" not in said


#: How a model might write the one word `pages` takes that is not a span. The tool
#: case-folds before comparing, and a model writing `All` at the start of a sentence
#: is the reason -- held here, because nothing else passed anything but the constant.
def _spellings(word: str) -> list[str]:
    return [word, word.upper(), word.capitalize(), f" {word} "]


def test_the_word_for_every_page_is_the_one_the_docstring_tells_the_model_to_pass(
    tools, handed, session_dir
):
    """`pages` takes one word that is not a span, and the model learns it here and
    nowhere else. Read off the constant and passed, rather than written out: spelt
    `all` in the test, the word could be changed in the tool and this would still
    pass, while every call the docstring recommends began failing.

    Every spelling a model might send, because the tool strips and case-folds before
    comparing and nothing held either -- spelt one way, both could go and the suite
    stayed green.
    """
    everything = tools["pdf_text"].func.__globals__["EVERYTHING"]
    (session_dir / "inputs" / "three.pdf").write_bytes(a_pdf("one", "two", "three"))

    assert f"pass `{everything}`" in _reads(tools["pdf_text"])
    for written in _spellings(everything):
        said = on_backend(
            tools["pdf_text"], handed, file_path="/inputs/three.pdf", pages=written
        )

        assert "pages 1-3 of 3" in said, written
        assert "does not have" not in said, "every page there is cannot name one there is not"


def test_left_to_its_default_the_whole_of_a_long_document_is_extracted(
    tools, handed, session_dir
):
    """At fifty pages a call, an agent asked about a 300-page report sampled three
    ranges, missed the figure on page 120, and answered that the report had none.
    """
    texts = ["filler"] * 119 + ["Third quarter revenue was 4.7 million euros."] + ["filler"] * 180
    (session_dir / "inputs" / "long.pdf").write_bytes(a_pdf(*texts))

    said = on_backend(tools["pdf_text"], handed, file_path="/inputs/long.pdf")

    saved = _saved(handed, "/scratchpad/long.p1-300.txt")
    assert saved is not None
    assert "--- page 120 ---\nThird quarter revenue was 4.7 million euros." in saved.decode()
    assert "pages 1-300 of 300" in said


def _reads(tool) -> str:
    """A tool's description as one line, which is how a model reads it.

    Line breaks taken out before matching: a docstring wraps where the column ran out,
    so `up to 500 pages a call` is `a\n    call` in the source and a plain `in` test
    sees neither. The sentence moved one word and the pin below went green on the
    wrap -- found by writing it.
    """
    return " ".join(tool.description.split())


def test_the_docstring_names_the_cap_the_tool_applies(tools):
    """The model learns the cap from the docstring alone, and plans its calls by it."""
    cap = tools["pdf_text"].func.__globals__["MAX_PAGES"]

    assert f"up to {cap} pages a call" in _reads(tools["pdf_text"])


def test_the_slice_docstring_names_the_size_the_tool_refuses_past(tools):
    """The same rule for the neighbour, which had the constant held against the real
    guard one line away and the sentence the model reads held by nothing -- so a guard
    that moved would leave `pdf_pages` advising a size it no longer applies, and the
    model deciding how many pages to ask for by it.
    """
    globals_ = tools["pdf_pages"].func.__globals__
    cap = globals_["MAX_SLICE_BYTES"]

    assert f"{cap // globals_['KB']} KB" in _reads(tools["pdf_pages"])

def test_a_page_with_no_text_is_named(tools, handed):
    """A scanned page saved as an empty section is believed to say nothing."""
    said = on_backend(tools["pdf_text"], handed, file_path="/inputs/report.pdf")

    assert said.endswith("— pages 1-3 of 3; no text on 2")


@pytest.mark.parametrize(("pages", "says"), [
    ("4-9", "has 3 page(s); pages '4-9' names none of them"),
    ("0", "has 3 page(s); pages '0' names none of them"),
    ("first", "pages 'first': write page numbers and ranges"),
])
def test_pages_that_name_no_page_of_the_document_are_a_failure(pages, says, tools, handed):
    """Page 0 taken as the list's last page, or a range past the end saved as an empty
    file, each answers "Saved" for text the document does not hold there.
    """
    with pytest.raises(ValueError, match=re.escape(says)):
        on_backend(tools["pdf_text"], handed, file_path="/inputs/report.pdf", pages=pages)


def test_a_file_that_is_not_a_pdf_is_a_failure_naming_it(tools, handed, session_dir):
    """pdfminer's own words name neither the file nor what went wrong with it."""
    (session_dir / "inputs" / "notes.pdf").write_text("plain text\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"^/inputs/notes\.pdf: not a PDF this tool can read"):
        on_backend(tools["pdf_text"], handed, file_path="/inputs/notes.pdf")


def test_two_ranges_left_to_the_default_land_in_two_files(tools, handed):
    """One default name for every range would leave only the last range read."""
    on_backend(tools["pdf_text"], handed, file_path="/inputs/report.pdf", pages="1")
    on_backend(tools["pdf_text"], handed, file_path="/inputs/report.pdf", pages="3")

    assert _saved(handed, "/scratchpad/report.p1.txt") == b"--- page 1 ---\nRevenue rose\n"
    assert _saved(handed, "/scratchpad/report.p3.txt") == b"--- page 3 ---\nCosts fell\n"

def test_the_slice_holds_exactly_the_pages_asked_for(tools, handed, session_dir):
    """Counted from zero, the slice is the page after each one asked for, and the
    agent describes a chart from the wrong page.
    """
    (session_dir / "inputs" / "long.pdf").write_bytes(a_pdf("one", "two", "three", "four", "five"))

    said = on_backend(tools["pdf_pages"], handed, file_path="/inputs/long.pdf", pages="2-3,5")

    saved = _saved(handed, "/scratchpad/long.p2-3_5.pdf")
    assert saved is not None
    assert [page.extract_text() for page in PdfReader(io.BytesIO(saved)).pages] == [
        "two",
        "three",
        "five",
    ]
    assert "pages 2-3, 5 of 5; open it with read_file" in said


@pytest.mark.parametrize(("texts", "says"), [
    ([f"page {n} " + "x" * 1500 for n in range(1, 201)], "ask for fewer pages"),
    (["x" * (MAX_PDF_BYTES + 1024)], "page 1 alone is"),
])
def test_a_slice_too_large_to_read_whole_is_refused_and_nothing_is_written(
    texts, says, tools, handed, session_dir
):
    """Saved, it is a file kingfisher will never hand the model, and the model is told
    "Saved" and to open it.
    """
    (session_dir / "inputs" / "big.pdf").write_bytes(a_pdf(*texts))
    pages = f"1-{len(texts)}" if len(texts) > 1 else "1"

    with pytest.raises(ValueError, match=re.escape(says)):
        on_backend(tools["pdf_pages"], handed, file_path="/inputs/big.pdf", pages=pages)

    assert _saved(handed, f"/scratchpad/big.p{pages}.pdf") is None


def test_the_slice_limit_is_the_one_the_guard_applies(tools):
    """Set below the guard, the tool refuses slices the model could have read; set
    above, it saves slices the guard will replace with advice.
    """
    assert tools["pdf_pages"].func.__globals__["MAX_SLICE_BYTES"] == MAX_PDF_BYTES


@pytest.mark.parametrize(("pages", "says"), [
    ("4-9", "has 3 page(s); pages '4-9' names none of them"),
    ("0", "has 3 page(s); pages '0' names none of them"),
    ("seven", "pages 'seven': write page numbers and ranges"),
])
def test_pages_naming_no_page_of_the_document_are_no_slice(pages, says, tools, handed):
    """Page 0 taken as the list's last page slices a page nobody asked for."""
    with pytest.raises(ValueError, match=re.escape(says)):
        on_backend(tools["pdf_pages"], handed, file_path="/inputs/report.pdf", pages=pages)


def test_a_file_pypdf_cannot_read_is_a_failure_naming_it(tools, handed, session_dir):
    """pypdf's own words name neither the file nor what went wrong with it."""
    (session_dir / "inputs" / "notes.pdf").write_text("plain text\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"^/inputs/notes\.pdf: not a PDF this tool can read"):
        on_backend(tools["pdf_pages"], handed, file_path="/inputs/notes.pdf", pages="1")

def test_a_file_already_at_the_target_is_replaced(tools, handed):
    """A tool that refused a taken default would fail every second call that left
    `save_as` out.
    """
    on_backend(
        tools["csv_filter"], handed, file_path="/inputs/sales.csv", column="region", value="north"
    )

    said = on_backend(
        tools["csv_filter"], handed, file_path="/inputs/sales.csv", column="region", value="south"
    )

    assert said.startswith("Saved filtered.csv ")
    assert _saved(handed, "/outputs/filtered.csv") == b"region,sales\nsouth,25.5\n"


def test_the_guides_recipe_says_saved_only_for_a_file_that_was(handed):
    """The guide's example is what gets copied, and its `.error` check is the line a
    copy is likeliest to lose. Run as written, so a cut there goes red here.
    """
    guide = (repository_root() / "docs" / "guides" / "tools.md").read_text(encoding="utf-8")
    section = guide.split("\n## A tool that creates a file\n", 1)[1].split("\n## ", 1)[0]
    source = re.findall(r"```python\n(.*?)```", section, re.DOTALL)[0]
    namespace: dict = {}
    exec(compile(source, "tools.md", "exec"), namespace)  # noqa: S102 -- our own guide
    (save_table,) = namespace["TOOLS"]
    table = {
        "columns": ["region", "sales"],
        "rows": [["north", "10"], ["south", "25.5"], ["east", "7"]],
    }

    with pytest.raises(OSError, match="permission_denied") as refused:
        on_backend(save_table, handed, **table, save_as="/inputs/table.csv")
    said = on_backend(save_table, handed, **table)

    assert _saved(handed, "/inputs/table.csv") is None
    assert _saved(handed, "/outputs/table.csv")
    # The two lines the guide quotes are the ones this code produces, the failure as
    # a tool's exception reaches the model.
    assert said in guide
    assert f"Error: OSError: {refused.value}" in guide
