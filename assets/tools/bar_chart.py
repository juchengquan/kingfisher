"""Draw a CSV's numbers as a bar chart, and save the picture rather than return it.

**The example of a tool whose purpose is a file.** An SVG handed back as the result
would cost the model every character of its markup to read, and leave the caller
nothing to open. So the chart is saved through the session's backend and the answer
is one line saying where it went -- the convention `docs/guides/tools.md` sets out
under *A tool that creates a file*, and `csv_filter` beside this one follows it too.

Drawn as SVG text by hand, because a plotting library would be a dependency for
every deployment that seeds this file, and a bar chart is a few rectangles.
"""

from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass
from html import escape
from pathlib import PurePosixPath
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from kingfisher import ToolContext

#: Bars drawn before the rest are only counted. Past this the labels crowd into a
#: chart nobody reads, and the answer says how many were left off.
MAX_BARS = 40

#: The layout, in pixels: one row per bar, a column for the labels, and the length
#: of the longest bar, which every other bar is scaled against.
ROW = 24
LABELS = 160
LONGEST = 400
TOP = 36

KB = 1024

#: What XML 1.0 admits below a space, and it is only these three. Everything else in
#: that range cannot appear in a well-formed document at all -- not escaped, not as a
#: character reference -- so a cell carrying one has to lose it before anything is
#: written. `escape` does not: it handles the markup characters and no more.
ALLOWED_CONTROLS = (0x09, 0x0A, 0x0D)
UNDRAWABLE = dict.fromkeys(c for c in range(0x20) if c not in ALLOWED_CONTROLS)


#: What one call reads. A backend hands a file back whole, into kingfisher's own
#: process, and nothing else bounds it: `docs/guides/tools.md` has the reason under
#: *A tool that reads a file*.
MAX_INPUT_BYTES = 32 * 1024 * 1024


def _fetched(runtime: ToolRuntime[ToolContext], file_path: str) -> bytes:
    """The file's bytes, through the session's backend -- wherever that keeps them."""
    (got,) = runtime.context.backend.download_files([file_path])
    if got.error or got.content is None:
        msg = f"{file_path}: {got.error or 'no content'}"
        raise FileNotFoundError(msg)
    if len(got.content) > MAX_INPUT_BYTES:
        msg = (
            f"{file_path} is {len(got.content)} bytes, past the {MAX_INPUT_BYTES} this "
            f"tool reads in one call -- ask for a smaller file, or a part of this one"
        )
        raise ValueError(msg)
    return got.content


#: What the `csv` module refuses to parse: one field longer than this raises
#: `_csv.Error`, and `csv.field_size_limit()` is 131,072 by default. A tool that reads
#: 32 MB therefore dies on a cell that is 0.6% of its own cap -- measured, all four
#: shipped CSV tools did, and through the failure guard the model was handed `Error:
#: Error: field larger than field limit (131072)`: no file, no column, no remedy, and
#: a type name reading `Error`. Raised deliberately rather than by widening the limit,
#: which is process-wide state an example tool has no business setting.
MAX_FIELD_BYTES = csv.field_size_limit()


def _parsed(file_path: str, rows: Any) -> list[list[str]]:
    """Every row, turning the one failure `csv` has into an answer a model can act on."""
    try:
        return [row for row in rows if row]
    except csv.Error as exc:
        msg = (
            f"{file_path}: one cell is longer than the {MAX_FIELD_BYTES} characters "
            f"the CSV reader parses ({exc}) -- ask for a file whose cells are smaller, "
            f"or read it with `read_file`"
        )
        raise ValueError(msg) from exc


def _size(count: int) -> str:
    """A byte count as a person reads one."""
    if count < KB:
        return f"{count} B"
    if count < KB * KB:
        return f"{count / KB:.1f} KB"
    return f"{count / KB / KB:.1f} MB"


@dataclass(frozen=True)
class _Chart:
    """What the drawing needs, read off the file."""

    title: str
    #: Each bar as `(label, number as written, number)`.
    bars: list[tuple[str, str, float]]
    #: Rows that had no number in their second column.
    skipped: int
    #: Cells that lost a character an SVG cannot carry.
    scrubbed: int


def _bars(file_path: str, text: str) -> _Chart:
    """Read the file into a title, the bars, and what could not be drawn as written."""
    rows = _parsed(file_path, csv.reader(io.StringIO(text, newline="")))
    if not rows or len(rows[0]) < 2:  # noqa: PLR2004 -- a label and a number
        msg = f"{file_path}: needs a header row naming two columns, a label and a number"
        raise ValueError(msg)
    header, *body = rows
    scrubbed = 0

    def drawable(cell: str) -> str:
        nonlocal scrubbed
        kept = cell.strip().translate(UNDRAWABLE)
        scrubbed += kept != cell.strip()
        return kept

    title = f"{drawable(header[1])} by {drawable(header[0])}"
    bars: list[tuple[str, str, float]] = []
    skipped = 0
    for row in body:
        written = row[1].strip() if len(row) > 1 else ""
        try:
            number = float(written)
        except ValueError:
            skipped += 1
            continue
        # Bars grow from zero, so a negative one has nowhere to go, and drawing it
        # as nothing would show a loss as an empty row.
        if number < 0:
            msg = f"{file_path}: {row[0]!r} is {written}, and a bar chart draws from zero"
            raise ValueError(msg)
        # A bar's length is its number over the longest, so one that is not finite has
        # no length of its own and takes every other bar's away with it: measured, one
        # `inf` row wrote `width="nan"` for itself and scaled every other bar to zero,
        # over an answer saying it had drawn them. `nan` passes the test above, and
        # `1e400` arrives here as `inf`, which is why this reads the number and not
        # the spelling.
        if not math.isfinite(number):
            msg = (
                f"{file_path}: {row[0]!r} is {written}, and a bar needs a finite length "
                f"-- every other bar is drawn as a share of the longest"
            )
            raise ValueError(msg)
        bars.append((drawable(row[0]), written, number))
    if not bars:
        msg = f"{file_path}: no row has a number in its second column, so there is nothing to draw"
        raise ValueError(msg)
    return _Chart(title, bars, skipped, scrubbed)


def _svg(title: str, bars: list[tuple[str, str, float]]) -> str:
    # `or 1` because a chart of zeros still has to divide by something.
    longest = max(number for _, _, number in bars) or 1
    width = LABELS + LONGEST + 80
    height = TOP + ROW * len(bars) + 12
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        'font-family="sans-serif" font-size="12">',
        f'<text x="{width // 2}" y="22" text-anchor="middle" font-size="14">'
        f"{escape(title)}</text>",
    ]
    for at, (label, written, number) in enumerate(bars):
        y = TOP + at * ROW
        length = number / longest * LONGEST
        parts += [
            f'<text x="{LABELS - 8}" y="{y + 16}" text-anchor="end">{escape(label)}</text>',
            f'<rect x="{LABELS}" y="{y + 4}" width="{length:.1f}" height="{ROW - 8}" '
            'fill="#4a7bb7"/>',
            f'<text x="{LABELS + length + 6:.1f}" y="{y + 16}">{escape(written)}</text>',
        ]
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


@tool
def bar_chart(
    file_path: str,
    runtime: ToolRuntime[ToolContext],
    save_as: str = "/outputs/chart.svg",
) -> str:
    """Draw a bar chart of a CSV's numbers and save it as an SVG image. Use when a
    comparison across a few dozen categories at most would read better as a picture.

    `file_path` is the same virtual path the file tools take, such as
    `/inputs/sales.csv`: a header row, then one bar per row, labelled by the first
    column and as long as the number in the second. A row with no number there is
    left out and counted.

    `save_as` is where the chart is saved, a full virtual path such as
    `/scratchpad/try1.svg`; leave it out for `/outputs/chart.svg`.
    """
    chart = _bars(file_path, _fetched(runtime, file_path).decode("utf-8-sig"))
    drawn = chart.bars[:MAX_BARS]
    data = _svg(chart.title, drawn).encode("utf-8")

    (sent,) = runtime.context.backend.upload_files([(save_as, data)])
    if sent.error:
        msg = f"not saved to {save_as}: {sent.error}"
        raise OSError(msg)

    note = f"bar chart of {chart.title}, {len(drawn)} bars"
    if len(chart.bars) > MAX_BARS:
        note += f", the first {MAX_BARS} of {len(chart.bars)}"
    if chart.skipped:
        note += f", {chart.skipped} row(s) with no number left out"
    if chart.scrubbed:
        note += f", {chart.scrubbed} cell(s) drawn without a character an SVG cannot carry"
    name = PurePosixPath(save_as).name
    return f"Saved {name} (SVG image, {_size(len(data))}) to {save_as} — {note}"


TOOLS = [bar_chart]
