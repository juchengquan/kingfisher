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

import sqlite3
from pathlib import Path

from langchain_core.tools import tool

#: The one database this deployment exposes. Point it wherever yours lives --
#: an absolute host path, because a tool runs in the kingfisher process and
#: does not see the agent's `/data` routing.
DATABASE = Path("~/kingfisher-example.db").expanduser()

MAX_ROWS = 200


def _connect() -> sqlite3.Connection:
    """Open read-only, so a SELECT typo cannot become a DELETE."""
    return sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)


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
    """Run one read-only SQL SELECT and return the rows as CSV.

    Use for aggregate questions over the database — counts, sums, group-bys —
    rather than reading rows and adding them up yourself. Returns at most 200
    rows. Only SELECT is permitted.
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
        # A bad query is something the model can fix on the next step; a
        # traceback is something that ends the turn.
        return f"sql error: {exc}"

    lines = [",".join(headers)]
    lines += [",".join("" if v is None else str(v) for v in row) for row in rows]
    if len(rows) == MAX_ROWS:
        lines.append(f"-- truncated at {MAX_ROWS} rows; add LIMIT or aggregate")
    return "\n".join(lines)


TOOLS = [sql_tables, sql_query]
