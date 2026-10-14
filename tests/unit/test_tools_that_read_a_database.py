"""The three shipped tools that open a database, driven rather than imported.

Their coverage was "does it import" and "is the name in the set" -- so `sql_query`
promised CSV and emitted `",".join(...)` for a release, which a value holding a comma
turns into a different answer with nothing to say it changed. These tools are the
reference a deployment copies, and a reference nobody calls is a reference nobody has
read.
"""

from __future__ import annotations

import csv
import io
import sqlite3
from typing import Any

import pytest

from kingfisher.kinds.tools.catalogue import LocalToolRepository, tool_name

#: Values a `",".join` cannot carry: a comma makes a field boundary, a newline makes a
#: record boundary, and a bare quote makes the rest of the line a quoted string.
AWKWARD = [
    ("north,south", "line1\nline2"),
    ('a"b', "plain"),
    (None, "null region"),
]


@pytest.fixture
def tools(shipped):
    return {tool_name(t): t for t in LocalToolRepository(shipped / "tools").tools}


def _globals(tool: Any) -> dict[str, Any]:
    """The module a tool was defined in, whichever shape it was written in."""
    if hasattr(tool, "func"):
        return tool.func.__globals__
    return type(tool)._run.__globals__


@pytest.fixture
def database(tmp_path, tools, monkeypatch):
    """A database at a path of this test's own, with both modules pointed at it.

    `DATABASE` is a module constant on purpose -- it is the deployment's choice of
    what the tool may reach, and the tools guide says so -- which makes repointing it
    the only way in from here.
    """
    path = tmp_path / "example.db"
    with sqlite3.connect(path) as writing:
        writing.execute("CREATE TABLE sales (region TEXT, note TEXT)")
        writing.executemany("INSERT INTO sales VALUES (?, ?)", AWKWARD)
        writing.execute("CREATE TABLE regions (name TEXT)")
    writing.close()
    for name in ("sql_query", "sql_tables", "sql_explain"):
        monkeypatch.setitem(_globals(tools[name]), "DATABASE", path)
    return path


def _rows(said: str) -> list[list[str]]:
    """The answer read the way its docstring says it may be read."""
    return [row for row in csv.reader(io.StringIO(said, newline="")) if row]


def test_a_value_holding_a_comma_or_a_newline_survives_the_round_trip(tools, database):
    """Measured before this, with `",".join(...)`: three rows came back as four lines,
    a two-column header got a three-field row, and the newline inside a value ended
    the record there. A model re-parsing that gets different data and is told nothing.
    """
    said = tools["sql_query"].invoke({"statement": "SELECT region, note FROM sales"})

    assert _rows(said) == [
        ["region", "note"],
        ["north,south", "line1\nline2"],
        ['a"b', "plain"],
        # A NULL arrives as an empty field, which is what CSV has to say it with.
        ["", "null region"],
    ]


def test_the_header_names_the_columns_the_statement_selected(tools, database):
    """The control for the round trip above: an answer with no header would re-parse
    cleanly and be read with its first row of data taken for the column names.
    """
    said = tools["sql_query"].invoke({"statement": "SELECT note FROM sales LIMIT 1"})

    assert _rows(said)[0] == ["note"]


def test_the_truncation_note_is_outside_the_rows_and_marked(tools, database, monkeypatch):
    """The answer is CSV and this line is not a row, so it is marked `--` and the
    docstring says it may be there -- a reader parsing the rows has to know where they
    stop. Unmarked, the cap reads as a row of one field.
    """
    monkeypatch.setitem(_globals(tools["sql_query"]), "MAX_ROWS", 2)

    said = tools["sql_query"].invoke({"statement": "SELECT region FROM sales"})

    assert said.splitlines()[-1].startswith("-- truncated at 2 rows")
    # Joined before matching: the sentence wraps where the column ran out, so an `in`
    # against the source text sees a line break the model never does.
    reads = " ".join(tools["sql_query"].description.split())
    assert "says so in a last line starting `--`" in reads


def test_a_short_answer_carries_no_truncation_note(tools, database):
    """The other half: a note on every answer tells a model to narrow a query that
    already returned everything.
    """
    said = tools["sql_query"].invoke({"statement": "SELECT region FROM sales"})

    assert "truncated" not in said


def test_every_connection_a_call_opens_is_closed_again(tools, database, monkeypatch):
    """`with sqlite3.connect(...)` commits and does *not* close, so each of the three
    tools leaked a connection a call -- measured, the connection still executes after
    the block. Driven through the real `connect`, because the idiom is the bug: a
    reader seeing `with` believes it was handled.
    """
    opened: list[sqlite3.Connection] = []
    real = sqlite3.connect

    def spy(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        connection = real(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", spy)

    tools["sql_tables"].invoke({})
    tools["sql_query"].invoke({"statement": "SELECT region FROM sales"})
    tools["sql_explain"].invoke({"statement": "SELECT region FROM sales"})

    assert len(opened) == 3, "one of the three never opened one, so this proves nothing"
    for connection in opened:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


def test_the_tables_are_listed_one_per_line(tools, database):
    """What the docstring tells the model to call before writing a statement."""
    assert tools["sql_tables"].invoke({}).splitlines() == ["regions", "sales"]


def test_a_statement_that_is_not_a_select_is_refused_before_it_runs(tools, database):
    """The grant this tool exists to be narrower than `execute`: one database,
    read-only, one statement. A DELETE that reached the database would make
    `sql_query.py` a wider grant than `execute` rather than a narrower one.
    """
    said = tools["sql_query"].invoke({"statement": "DELETE FROM sales"})

    assert said == "refused: only SELECT statements are permitted"
    with sqlite3.connect(database) as reading:
        assert reading.execute("SELECT count(*) FROM sales").fetchone()[0] == len(AWKWARD)
    reading.close()


def test_a_select_that_the_database_refuses_comes_back_as_a_message(tools, database):
    """A bad query is something the model can fix on its next step, so it reads the
    reason rather than a failed call.
    """
    said = tools["sql_query"].invoke({"statement": "SELECT nope FROM sales"})

    assert said.startswith("sql error:")
    assert "nope" in said


def test_a_read_only_connection_refuses_a_write_even_if_one_gets_through(tools, database):
    """The guard above is a string test, and the connection is the half that holds
    when a statement starting `select` goes on to write -- which SQLite allows in a
    CTE. Asked of the connection the module opens, not of a connection this test made.
    """
    opened = _globals(tools["sql_query"])["_connect"]

    with opened() as connection, pytest.raises(sqlite3.OperationalError, match="readonly"):
        connection.execute("DELETE FROM sales")


@pytest.mark.parametrize("name", ["sql_query", "sql_tables", "sql_explain"])
def test_a_missing_database_is_said_rather_than_raised(name, tools, tmp_path, monkeypatch):
    """The shipped default points at `~/kingfisher-example.db`, which a fresh
    deployment does not have -- so this is the first thing a model will see, on all
    three, and a traceback is not an answer it can act on.
    """
    monkeypatch.setitem(_globals(tools[name]), "DATABASE", tmp_path / "nothing.db")
    asked = {} if name == "sql_tables" else {"statement": "SELECT 1"}

    said = tools[name].invoke(asked)

    assert said.startswith("no database at")


def test_the_plan_is_one_numbered_step_a_line(tools, database):
    """`sql_explain` is the class-shaped example, and nothing had ever called it: its
    coverage was the schema its arguments declare.
    """
    said = tools["sql_explain"].invoke({"statement": "SELECT region FROM sales"})

    assert said.startswith("1. ")
    assert "sales" in said.lower()


def test_the_verbose_plan_is_the_rows_as_they_came(tools, database):
    """The argument the class shape exists to describe, so it is also the one nothing
    held: a `verbose` that did nothing would read as working.
    """
    plain = tools["sql_explain"].invoke({"statement": "SELECT region FROM sales"})
    said = tools["sql_explain"].invoke(
        {"statement": "SELECT region FROM sales", "verbose": True}
    )

    assert said != plain
    assert not said.startswith("1. ")


def test_explaining_does_not_run_the_statement(tools, database):
    """What the argument's own description promises -- "an expensive statement is safe
    to pass here" -- and the reason a plan is read read-only.
    """
    said = tools["sql_explain"].invoke({"statement": "SELECT count(*) FROM sales"})

    assert str(len(AWKWARD)) not in said, "the answer carries a result, so it ran"
