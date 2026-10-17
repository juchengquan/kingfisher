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
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

from langchain_core.tools import tool

from kingfisher import ToolSpec

#: The one database this deployment exposes. Point it wherever yours lives --
#: an absolute host path, and still a host path after tool *arguments* stopped
#: being ones. That is the line: an argument comes from the model and is
#: resolved against the session, and a constant like this comes from whoever
#: deployed the tool and is not. A tool that must read something outside a
#: session says so here, where a person chose it.
DATABASE = Path("~/kingfisher-example.db").expanduser()

MAX_ROWS = 200

#: How much the whole answer may carry, and how long one value may be. 200 rows is a
#: bound on rows and says nothing about their width: measured, `SELECT b.a FROM big b,
#: t LIMIT 200` against a 20 MB database obeyed the row cap and came back as a
#: 4,000,000,200-character answer -- 30 seconds and 3.3 GB of resident memory, and
#: then the model's whole context. This is roughly what 200 rows of a wide aggregate
#: can honestly reach, and twenty thousand times less than that.
MAX_ANSWER_CHARS = 200_000

#: How long one statement may spend inside SQLite. A tool runs on the turn's thread
#: and `KINGFISHER_TURN_TIMEOUT_S` is read between stream chunks, so nothing else is
#: going to stop it: measured, `SELECT count(*) FROM t a, t b, t c WHERE a.a < b.a ||
#: c.a` over a 1,000-row table takes 49 seconds, and a fourth table is a thousand
#: times that -- about 14 hours of a turn nobody can interrupt.
MAX_SECONDS = 10.0

#: SQLite's own virtual-machine instructions between two checks of the bound above.
#: Measured at 100, 1,000, 10,000 and 100,000 on a million-row join: 0.04 seconds
#: every time, and 0.04 without the handler at all. So this is as tight as it is
#: cheap to make it.
PROGRESS_STEPS = 1_000


class _Bound:
    """The clock one statement runs against, and whether it was this that stopped it.

    SQLite calls a progress handler from inside its own loop, which is the only thing
    here that can stop work already under way -- a Python thread cannot be interrupted
    mid-call, which is why `docs/guides/tools.md` says a tool's own bound is all there
    is. Returning non-zero aborts the statement with `OperationalError: interrupted`,
    and nothing in that exception says who asked, so the handler records it.
    """

    def __init__(self, seconds: float) -> None:
        self._until = time.monotonic() + seconds
        self.expired = False

    def __call__(self) -> int:
        if time.monotonic() < self._until:
            return 0
        self.expired = True
        return 1


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    """Open read-only, so a SELECT typo cannot become a DELETE, and close after.

    `closing`, because `with` on a connection commits and does not close it -- so the
    bare idiom reads as handled and leaks one a call. A read needs no commit.
    """
    with closing(sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)) as connection:
        yield connection


def _too_wide(headers: list[str], row: tuple[object, ...]) -> str | None:
    """The refusal for a value no answer here could carry, or `None`.

    The length is read before the conversion, because `str()` on a 20 MB value is
    where the memory goes -- and a value that cannot fit the answer at all is worth
    naming rather than silently stopping the rows at it, since the model can ask for
    the same column narrower.
    """
    for name, value in zip(headers, row, strict=False):
        if isinstance(value, str | bytes) and len(value) > MAX_ANSWER_CHARS:
            return (
                f"refused: {name} holds {len(value)} characters in one value, past the "
                f"{MAX_ANSWER_CHARS} this tool answers with -- select "
                f"substr({name}, 1, 1000), or length({name})"
            )
    return None


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


def sql_query(statement: str) -> str:
    """Run one read-only SQL SELECT and return the rows as CSV, header first.

    Use for aggregate questions over the database — counts, sums, group-bys —
    rather than reading rows and adding them up yourself. Returns at most 200
    rows and 200000 characters, and says so in a last line starting `--` when it
    had to stop there. One statement runs for at most 10 seconds, so narrow or
    aggregate a query that needs longer. Only SELECT is permitted.
    """
    if not statement.lstrip().lower().startswith("select"):
        return "refused: only SELECT statements are permitted"
    if not DATABASE.exists():
        return f"no database at {DATABASE}"

    # Through `csv`, as `csv_filter` writes its file. Joining the values with commas
    # is the shorter line and answers a different question: a value holding a comma, a
    # quote or a newline re-parses as other data, and nothing in the answer says so.
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    bound = _Bound(MAX_SECONDS)
    note = ""
    try:
        with _connect() as connection:
            # Armed here and not in `_connect`, so that `sql_tables` beside this does
            # not pay for a clock it cannot need: its statement is written here and
            # reads one fixed table, and this one comes from the model.
            connection.set_progress_handler(bound, PROGRESS_STEPS)
            cursor = connection.execute(statement)
            headers = [column[0] for column in cursor.description or ()]
            writer.writerow(headers)
            # A row at a time rather than `fetchmany(MAX_ROWS)`, because the second
            # bound is on what the answer carries and `fetchmany` has every row in
            # this process before anything could measure one.
            for kept, row in enumerate(cursor, start=1):
                if (wide := _too_wide(headers, row)) is not None:
                    return wide
                writer.writerow(["" if value is None else str(value) for value in row])
                # Both notes sit outside the CSV and are marked, which the docstring
                # promises, so a reader parsing the rows knows to stop at one rather
                # than read it as a row of one field.
                if kept >= MAX_ROWS:
                    note = f"-- truncated at {MAX_ROWS} rows; add LIMIT or aggregate\n"
                    break
                if out.tell() >= MAX_ANSWER_CHARS:
                    note = (
                        f"-- truncated at {kept} rows, past the {MAX_ANSWER_CHARS} "
                        f"characters this tool answers with; select fewer columns, "
                        f"shorten them with substr, or aggregate\n"
                    )
                    break
    except sqlite3.Error as exc:
        # A bad query is something the model can fix on the next step, and it
        # reads this the same whether returned or raised: the failure guard turns
        # a workspace tool's exception into a failed result. Returning costs the
        # status, which reports the call as a success.
        if bound.expired:
            # Said before the exception, which is `interrupted` and names neither the
            # bound nor what to do about it.
            return (
                f"sql error: stopped after {MAX_SECONDS:g}s, which is as long as one "
                f"statement may run here -- narrow it, or aggregate"
            )
        return f"sql error: {exc}"
    return (out.getvalue() + note).rstrip("\n")


# The database is `sales_db`, so the tool's own `ToolSpec` says so rather than every
# agent that grants it: an agent that forgot the line would hand the database to whoever
# opened it. An agent's own entry can still narrow this further; nothing it writes can
# widen it.
TOOLS = [sql_tables, ToolSpec(sql_query, source_ids=["sales_db"])]
