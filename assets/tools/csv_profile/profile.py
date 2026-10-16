"""The two tools themselves, sharing one notion of a column.

Both answer a question the agent would otherwise answer by reading the whole
file into its context: what is in this CSV, and is any of it missing. A 20,000
row file costs the same handful of tokens here as a 20 row one, which is the
argument for a tool over `read_file` and the reason to bound the answer rather
than the input.

A relative import, which is the thing a folder buys. Flat, `columns` would have
had to be `_columns` to stay out of the loader's way, and the leading underscore
would have been about kingfisher rather than about the code.
"""

from __future__ import annotations

import csv
import io
from collections import Counter
from dataclasses import dataclass

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from kingfisher import ToolContext

from .columns import profile_column, shown

#: Rows read before stopping. A profile is a shape, not a census: the type and
#: the missing-value pattern of a column settle long before the file ends, and
#: reading 2 million rows to say "number, 3% missing" costs the deployment time
#: the agent then waits on.
SAMPLE_ROWS = 5_000


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


@dataclass(frozen=True)
class _Read:
    """One file as a profile saw it."""

    header: list[str]
    #: One list of cells per column, in the file's own order.
    columns: list[list[str]]
    rows: int
    #: Rows whose cell count was not the header's, whichever way.
    ragged: int


#: What the `csv` module refuses to parse: one field longer than this raises
#: `_csv.Error`, and `csv.field_size_limit()` is 131,072 by default. A tool that reads
#: 32 MB therefore dies on a cell that is 0.6% of its own cap -- measured, all four
#: shipped CSV tools did, and through the failure guard the model was handed `Error:
#: Error: field larger than field limit (131072)`: no file, no column, no remedy, and
#: a type name reading `Error`. Raised deliberately rather than by widening the limit,
#: which is process-wide state an example tool has no business setting.
#:
#: Said the same way in `csv_filter.py` and `bar_chart.py`, which read a CSV the same
#: way and are copied one file at a time, for the reason `_fetched` is.
MAX_FIELD_BYTES = csv.field_size_limit()


def _refuse_wide(file_path: str, exc: csv.Error) -> ValueError:
    """The one failure `csv` has, as an answer a model can act on."""
    return ValueError(
        f"{file_path}: one cell is longer than the {MAX_FIELD_BYTES} characters the CSV "
        f"reader parses ({exc}) -- ask for a file whose cells are smaller, or read it "
        f"with `read_file`"
    )


def _report_names(header: list[str]) -> list[str]:
    """Each column's name for the report, carrying its position where a name repeats.

    A reader handed `region` twice cannot tell which column is which, and the file is
    the only place that distinction lives -- so the repeated ones say which column of
    the file they are. Unique names are left exactly as the file wrote them.
    """
    seen = Counter(header)
    return [
        f"{name} (column {at + 1})" if seen[name] > 1 else name
        for at, name in enumerate(header)
    ]


def _read(runtime: ToolRuntime[ToolContext], file_path: str) -> _Read:
    """Gather the sampled cells, by column position.

    By position because a name may appear twice and two columns called `region` are
    two columns. Keyed by name they merged into one list holding both their cells:
    two rows reported four filled, a column half empty reported a quarter missing,
    and the same line was printed once per spelling with no way to tell them apart.
    """
    text = _fetched(runtime, file_path).decode("utf-8-sig")
    try:
        return _gathered(text)
    except csv.Error as exc:
        raise _refuse_wide(file_path, exc) from exc


def _gathered(text: str) -> _Read:
    """The walk itself, so the one `csv` failure is caught around the whole read.

    Separate from `_read` above because `csv` raises on the row it is handed, which is
    inside the loop: a `try` around `next(reader)` alone catches the header's cell and
    not the body's.
    """
    with io.StringIO(text, newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            return _Read([], [], 0, 0)

        gathered: list[list[str]] = [[] for _ in header]
        seen = ragged = 0
        for row in reader:
            if len(row) != len(header):
                ragged += 1
            # Truncated to the header, which is what a column profile can speak about:
            # the cells past it belong to no column and were silently dropped before,
            # so the count above is what says the file is not rectangular.
            for at, value in enumerate(row[: len(header)]):
                gathered[at].append(value)
            seen += 1
            if seen >= SAMPLE_ROWS:
                break
    return _Read(header, gathered, seen, ragged)


@tool
def csv_profile(file_path: str, runtime: ToolRuntime[ToolContext]) -> str:
    """Summarise a CSV: every column's type, how much is missing, how varied.

    Use before analysing a file you have not seen. Cheaper and more reliable
    than reading it, and the answer does not grow with the file.

    `file_path` is the same virtual path the file tools take -- `/inputs/<name>` --
    rooted at this session, read through the session's backend.
    """
    read = _read(runtime, file_path)
    if not read.header:
        return f"{file_path}: empty"

    lines = [
        profile_column(name, cells).line()
        for name, cells in zip(_report_names(read.header), read.columns, strict=True)
    ]
    said = []
    if read.rows >= SAMPLE_ROWS:
        said.append(f"first {read.rows} rows")
    if read.ragged:
        said.append(f"{read.ragged} row(s) not {len(read.header)} cells wide")
    note = f" ({', '.join(said)})" if said else ""
    return f"{len(read.header)} columns, {read.rows} rows{note}\n" + "\n".join(lines)


@tool
def csv_columns(file_path: str, runtime: ToolRuntime[ToolContext]) -> str:
    """Just the column names of a CSV, one per line.

    The cheap half of `csv_profile`, for when the question is only what the
    file contains and not what state it is in.

    `file_path` is the same virtual path the file tools take -- `/inputs/<name>` --
    rooted at this session, read through the session's backend.
    """
    header = _read(runtime, file_path).header
    # `shown`, because this answer is one name a line and a name may hold a newline:
    # three columns came back as four names, one of them invented.
    return "\n".join(shown(name) for name in header) if header else f"{file_path}: empty"
