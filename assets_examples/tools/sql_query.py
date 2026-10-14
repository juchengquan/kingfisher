"""A tool that makes an existing capability *narrower*, not wider.

The second reason to write one, and the less obvious one. An agent with
`execute` could already open this database — but it could also open every other
file on the host, and nothing in the answer would say which it did.

Wiring the connection here rather than leaving it to a shell command means the
task's reach is stated in code: one database, read-only, one statement, bounded
rows. A request can then activate `sql_query` and *not* `execute`, which is a
smaller grant than any prompt asking the agent to please only query this file.

Two tools in one module, because they share the connection rule and would drift
if they lived apart.
"""

from __future__ import annotations

import csv
import io
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

from langchain_core.tools import tool

#: The one database this deployment exposes. Point it wherever yours lives --
#: an absolute host path, and still a host path after tool *arguments* stopped
#: being ones. That is the line: an argument comes from the model and is
#: resolved against the session, and a constant like this comes from whoever
#: deployed the tool and is not. A tool that must read something outside a
#: session says so here, where a person chose it.
DATABASE = Path("~/kingfisher-example.db").expanduser()

MAX_ROWS = 200


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    """Open read-only, so a SELECT typo cannot become a DELETE, and close after.

    `closing`, because `with` on a connection commits and does not close it -- so the
    bare idiom reads as handled and leaks one a call. A read needs no commit.
    """
    with closing(sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)) as connection:
        yield connection


@tool
def sql_tables() -> str:
    """List the tables available to query, one per line.

    Call this before `sql_query` when you do not already know the schema.
    """
    if not DATABASE.exists():
        return f"no database at {DATABASE}"
    with _connect() as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    return "\n".join(name for (name,) in rows) or "(no tables)"


@tool
def sql_query(statement: str) -> str:
    """Run one read-only SQL SELECT and return the rows as CSV, header first.

    Use for aggregate questions over the database — counts, sums, group-bys —
    rather than reading rows and adding them up yourself. Returns at most 200
    rows, and says so in a last line starting `--` when it had to stop there.
    Only SELECT is permitted.
    """
    if not statement.lstrip().lower().startswith("select"):
        return "refused: only SELECT statements are permitted"
    if not DATABASE.exists():
        return f"no database at {DATABASE}"

    try:
        with _connect() as connection:
            cursor = connection.execute(statement)
            headers = [column[0] for column in cursor.description or ()]
            rows = cursor.fetchmany(MAX_ROWS)
    except sqlite3.Error as exc:
        # A bad query is something the model can fix on the next step, and it
        # reads this the same whether returned or raised: the failure guard turns
        # a workspace tool's exception into a failed result. Returning costs the
        # status, which reports the call as a success.
        return f"sql error: {exc}"

    # Through `csv`, as `csv_filter` writes its file. Joining the values with commas
    # is the shorter line and answers a different question: a value holding a comma, a
    # quote or a newline re-parses as other data, and nothing in the answer says so.
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(headers)
    writer.writerows(["" if v is None else str(v) for v in row] for row in rows)
    said = out.getvalue()
    if len(rows) == MAX_ROWS:
        # Outside the CSV and marked, which the docstring promises, so a reader
        # parsing the rows knows to stop at it rather than read it as one.
        said += f"-- truncated at {MAX_ROWS} rows; add LIMIT or aggregate\n"
    return said.rstrip("\n")


TOOLS = [sql_tables, sql_query]
