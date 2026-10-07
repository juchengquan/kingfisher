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

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from kingfisher import ToolContext

from .columns import profile_column

#: Rows read before stopping. A profile is a shape, not a census: the type and
#: the missing-value pattern of a column settle long before the file ends, and
#: reading 2 million rows to say "number, 3% missing" costs the deployment time
#: the agent then waits on.
SAMPLE_ROWS = 5_000


def _fetched(runtime: ToolRuntime[ToolContext], file_path: str) -> bytes:
    """The file's bytes, through the session's backend -- wherever that keeps them."""
    (got,) = runtime.context.backend.download_files([file_path])
    if got.error or got.content is None:
        msg = f"{file_path}: {got.error or 'no content'}"
        raise FileNotFoundError(msg)
    return got.content


def _read(
    runtime: ToolRuntime[ToolContext], file_path: str
) -> tuple[list[str], dict[str, list[str]], int]:
    """Header, columns and how many rows were actually looked at."""
    text = _fetched(runtime, file_path).decode("utf-8-sig")
    with io.StringIO(text, newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            return [], {}, 0

        gathered: dict[str, list[str]] = {name: [] for name in header}
        seen = 0
        for row in reader:
            for name, value in zip(header, row, strict=False):
                gathered[name].append(value)
            seen += 1
            if seen >= SAMPLE_ROWS:
                break
    return header, gathered, seen


@tool
def csv_profile(file_path: str, runtime: ToolRuntime[ToolContext]) -> str:
    """Summarise a CSV: every column's type, how much is missing, how varied.

    Use before analysing a file you have not seen. Cheaper and more reliable
    than reading it, and the answer does not grow with the file.

    `file_path` is the same virtual path the file tools take -- `/inputs/<name>` --
    rooted at this session, read through the session's backend.
    """
    header, gathered, seen = _read(runtime, file_path)
    if not header:
        return f"{file_path}: empty"

    lines = [profile_column(name, gathered[name]).line() for name in header]
    note = f" (first {seen} rows)" if seen >= SAMPLE_ROWS else ""
    return f"{len(header)} columns, {seen} rows{note}\n" + "\n".join(lines)


@tool
def csv_columns(file_path: str, runtime: ToolRuntime[ToolContext]) -> str:
    """Just the column names of a CSV, one per line.

    The cheap half of `csv_profile`, for when the question is only what the
    file contains and not what state it is in.

    `file_path` is the same virtual path the file tools take -- `/inputs/<name>` --
    rooted at this session, read through the session's backend.
    """
    header, _, _ = _read(runtime, file_path)
    return "\n".join(header) if header else f"{file_path}: empty"
