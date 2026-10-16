"""Keep the rows of a CSV where one column holds one value, and save them as a file.

**An export, which is a file by purpose.** The rows it keeps are what the caller
wants handed back, and returned as the result they would be read into the model's
context row by row and then written out again by hand. Saved, they cost one line:
where the file went and how many rows it holds -- the convention
`docs/guides/tools.md` sets out under *A tool that creates a file*, and `bar_chart`
beside this one follows it too.
"""

from __future__ import annotations

import csv
import io
from pathlib import PurePosixPath
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from kingfisher import ToolContext

KB = 1024


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


@tool
def csv_filter(
    file_path: str,
    column: str,
    value: str,
    runtime: ToolRuntime[ToolContext],
    save_as: str = "/outputs/filtered.csv",
) -> str:
    """Save the rows of a CSV where one column equals a value, as a new CSV with the
    same header. Use to hand on part of a file -- one region's orders, one day's
    errors -- without reading the file into your answer.

    `file_path` is the same virtual path the file tools take, such as
    `/inputs/orders.csv`, and its first row names the columns. `column` is one of
    those names, and a row is kept when that cell equals `value` exactly, once
    spaces at either end of both are ignored.

    `save_as` is where the result is saved, a full virtual path such as
    `/scratchpad/north.csv`; leave it out for `/outputs/filtered.csv`.
    """
    text = _fetched(runtime, file_path).decode("utf-8-sig")
    rows = _parsed(file_path, csv.reader(io.StringIO(text, newline="")))
    header, *body = rows or [[]]
    names = [name.strip() for name in header]
    if column.strip() not in names:
        msg = f"{file_path}: no column {column!r}; the columns are {', '.join(names) or 'none'}"
        raise ValueError(msg)
    at = names.index(column.strip())
    wanted = value.strip()
    # A short row has no cell at `at`, which is not a match -- and not an error,
    # since a CSV written by hand often drops trailing empty cells.
    kept = [row for row in body if len(row) > at and row[at].strip() == wanted]

    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows([header, *kept])
    data = out.getvalue().encode("utf-8")

    (sent,) = runtime.context.backend.upload_files([(save_as, data)])
    if sent.error:
        msg = f"not saved to {save_as}: {sent.error}"
        raise OSError(msg)

    name = PurePosixPath(save_as).name
    note = f"{len(kept)} of {len(body)} rows, where {column.strip()} is {wanted!r}"
    return f"Saved {name} (CSV, {_size(len(data))}) to {save_as} — {note}"


TOOLS = [csv_filter]
