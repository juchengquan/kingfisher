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

from .columns import fitted, profile_column

#: Rows read before stopping. A profile is a shape, not a census: the type and
#: the missing-value pattern of a column settle long before the file ends, and
#: reading 2 million rows to say "number, 3% missing" costs the deployment time
#: the agent then waits on.
SAMPLE_ROWS = 5_000


#: Columns gathered, profiled and named. The row cap above is 5,000 and this one is two
#: hundred because the two cost differently: a row adds a number to a count, and a
#: column adds a line of the answer and a list holding every sampled cell. Measured, a
#: 10 MB header of 5,242,880 columns answered 334,433,239 characters and peaked at 1,833
#: MB resident -- and a plausible 4,000-column feature table answered 218,913, some
#: 55,000 tokens for one call asking what shape a file is.
MAX_COLUMNS = 200


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

    #: Every name the header holds, in the file's own order: the number of columns a
    #: reader is told and the width a row is judged against, profiled or not.
    header: list[str]
    #: One list of cells per column profiled, which is the first `MAX_COLUMNS` of them.
    columns: list[list[str]]
    rows: int
    #: Rows whose cell count was not the header's, whichever way.
    ragged: int

    @property
    def profiled(self) -> list[str]:
        """The names of the columns whose cells were gathered, so the two line up."""
        return self.header[: len(self.columns)]


#: What the `csv` module refuses to parse: one field longer than this raises
#: `_csv.Error`, and `csv.field_size_limit()` is 131,072 by default. A tool that
#: reads 32 MB therefore dies on a cell of 128 KB -- measured, every shipped tool
#: that parses a CSV did, on one cell of 200,000 characters, and through the failure
#: guard the model was handed `Error: Error: field larger than field limit (131072)`:
#: no file, no column, no remedy, and a type name reading `Error`. Raised
#: deliberately rather than by widening the limit, which is process-wide state an
#: example tool has no business setting.
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

    Counted after fitting, not before: two names that differ only past the cut are one
    name in the answer, and counting the file's spelling left both lines identical with
    nothing to tell them apart -- which is the whole failure this function exists for.
    """
    names = [fitted(name) for name in header]
    seen = Counter(names)
    return [
        f"{name} (column {at + 1})" if seen[name] > 1 else name
        for at, name in enumerate(names)
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

        gathered: list[list[str]] = [[] for _ in header[:MAX_COLUMNS]]
        seen = ragged = 0
        for row in reader:
            if len(row) != len(header):
                ragged += 1
            # Truncated to the columns gathered, which past `MAX_COLUMNS` is fewer
            # than the header holds: the cells past it belong to no column anybody is
            # told about, and were silently dropped before, so the count above is what
            # says the file is not rectangular.
            for at, value in enumerate(row[: len(gathered)]):
                gathered[at].append(value)
            seen += 1
            if seen >= SAMPLE_ROWS:
                break
    return _Read(header, gathered, seen, ragged)


@tool
def csv_profile(file_path: str, runtime: ToolRuntime[ToolContext]) -> str:
    """Summarise a CSV: every column's type, how much is missing, how varied.

    Use before analysing a file you have not seen. Cheaper and more reliable
    than reading it, and the answer does not grow with the file: a file wider
    than one answer carries is profiled to its first columns, and the first line
    says how many columns it has and how many of them were read.

    `file_path` is the same virtual path the file tools take -- `/inputs/<name>` --
    rooted at this session, read through the session's backend.
    """
    read = _read(runtime, file_path)
    if not read.header:
        return f"{file_path}: empty"

    lines = [
        profile_column(name, cells).line()
        for name, cells in zip(_report_names(read.profiled), read.columns, strict=True)
    ]
    said = []
    if len(read.header) > len(read.columns):
        said.append(f"first {len(read.columns)} columns profiled")
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
    file contains and not what state it is in. A file wider than one answer
    carries stops at the same number of columns, and says so on a last line.

    `file_path` is the same virtual path the file tools take -- `/inputs/<name>` --
    rooted at this session, read through the session's backend.
    """
    header = _read(runtime, file_path).header
    if not header:
        return f"{file_path}: empty"
    # `fitted`, because this answer is one name a line and a name is uploaded text:
    # three columns came back as four names, one of them invented, and 10 MB of header
    # came back as a 10 MB answer.
    named = [fitted(name) for name in header[:MAX_COLUMNS]]
    if len(header) > MAX_COLUMNS:
        # On a line of its own, the way the two sql tools mark a cut, because this
        # answer is one name a line: handed 200 names of 5,242,880 with nothing said,
        # a reader cannot tell a narrow file from a file this tool stopped reading.
        named.append(f"-- first {MAX_COLUMNS} of {len(header)} columns")
    return "\n".join(named)
