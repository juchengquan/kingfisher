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
import time
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from langchain_core.tools import BaseTool, tool

from kingfisher.core.definitions.tools.catalogue import LocalToolRepository, tool_name

#: Values a `",".join` cannot carry: a comma makes a field boundary, a newline makes a
#: record boundary, and a bare quote makes the rest of the line a quoted string.
AWKWARD = [
    ("north,south", "line1\nline2"),
    ('a"b', "plain"),
    (None, "null region"),
]


@pytest.fixture
def tools(shipped):
    # A plain function is made a tool as the graph makes one, so all three are invoked alike.
    return {
        tool_name(t): t if isinstance(t, BaseTool) else tool(t)
        for t in LocalToolRepository(shipped / "tools").tools
    }


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


#: A three-way join, which is work SQLite cannot shortcut: it has to visit every
#: combination to answer. Measured over a 1,000-row table it takes 49 seconds, and a
#: fourth table is a thousand times that -- about 14 hours. 400 rows is about three
#: seconds, so the bound these rules patch is a small fraction of real work.
SLOW = "SELECT count(*) FROM many a, many b, many c WHERE a.n < b.n || c.n"


@pytest.fixture
def many_rows(database):
    """Enough rows in the database above that `SLOW` is not over before it starts."""
    with sqlite3.connect(database) as writing:
        writing.execute("CREATE TABLE many (n TEXT)")
        writing.executemany("INSERT INTO many VALUES (?)", [("x" * 50,)] * 400)
    writing.close()
    return database


@pytest.fixture
def one_wide_value(database):
    """A single value longer than the answer cap the rules below patch down to."""
    with sqlite3.connect(database) as writing:
        writing.execute("CREATE TABLE wide (note TEXT)")
        writing.execute("INSERT INTO wide VALUES (?)", ("y" * 500,))
    writing.close()
    return database


def test_a_statement_past_the_time_it_may_run_is_stopped_and_told_so(
    tools, many_rows, monkeypatch
):
    """A tool runs on the turn's thread and `KINGFISHER_TURN_TIMEOUT_S` is read between
    stream chunks, so nothing outside this statement was going to stop it -- one
    mistyped join wedges the deployment for hours. SQLite calls a progress handler from
    inside its own loop, which is the only thing here that can stop work already under
    way, and the elapsed time is asserted because a handler that was never armed reads
    exactly like one that was.
    """
    monkeypatch.setitem(_globals(tools["sql_query"]), "MAX_SECONDS", 0.05)

    started = time.monotonic()
    said = tools["sql_query"].invoke({"statement": SLOW})
    took = time.monotonic() - started

    assert took < 1.0, f"it ran {took:.1f}s, so the bound is not what ended it"
    assert said.startswith("sql error: stopped after 0.05s")
    assert "narrow it" in said
    # SQLite's own word for an aborted statement is `interrupted`, which names neither
    # the bound nor anything to do about it, and is the same word for any other abort.
    assert "interrupted" not in said


def test_a_statement_inside_that_time_still_answers(tools, many_rows):
    """The other half. Every rule above is a control for a bound that refused
    everything, but not for one armed with a deadline already past -- which would
    refuse the first statement of every call and pass them all.
    """
    said = tools["sql_query"].invoke({"statement": "SELECT count(*) FROM many"})

    assert _rows(said) == [["count(*)"], ["400"]]


def test_one_value_wider_than_the_whole_answer_is_refused_by_its_column(
    tools, one_wide_value, monkeypatch
):
    """`MAX_ROWS` bounds rows and says nothing about their width. Measured, 200 rows of
    one 20 MB value obeyed the row cap and came back as a 4,000,000,200-character
    answer -- 30 seconds and 3.3 GB of resident memory, from a 20 MB database, and then
    the model's whole context. Refused by name rather than cut short, because the model
    can ask for the same column narrower and this is where it learns how.
    """
    monkeypatch.setitem(_globals(tools["sql_query"]), "MAX_ANSWER_CHARS", 100)

    said = tools["sql_query"].invoke({"statement": "SELECT note FROM wide"})

    assert said.startswith("refused: note holds 500 characters in one value")
    assert "substr(note, 1, 1000)" in said


def test_the_rows_stop_at_the_characters_the_answer_carries(tools, database, monkeypatch):
    """The second half of the same number: 200 rows each just inside the per-value
    limit is still 200 times it. Marked `--` outside the rows like the row cap beside
    it, and at least one row always comes back -- a header alone answers nothing.
    """
    monkeypatch.setitem(_globals(tools["sql_query"]), "MAX_ANSWER_CHARS", 30)

    said = tools["sql_query"].invoke({"statement": "SELECT region, note FROM sales"})

    lines = said.splitlines()
    assert lines[-1].startswith("-- truncated at 1 rows, past the 30 characters")
    assert len(_rows("\n".join(lines[:-1]))) == 2, "a header and the one row that fitted"


def test_the_docstring_names_every_bound_the_tool_applies(tools):
    """The model plans its next statement by these three numbers and reads them here
    and nowhere else, so a bound that moved without the sentence would leave it asking
    for an answer this tool will not give. Whitespace-normalised: the sentences wrap
    where the column ran out, and an `in` against the source sees a line break the
    model never does.
    """
    globals_ = _globals(tools["sql_query"])
    reads = " ".join(tools["sql_query"].description.split())

    assert f"at most {globals_['MAX_ROWS']} rows" in reads
    assert f"{globals_['MAX_ANSWER_CHARS']} characters" in reads
    assert f"at most {globals_['MAX_SECONDS']:g} seconds" in reads


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


def test_a_plan_naming_something_spelt_at_length_is_cut_and_marked(tools, database):
    """Rows are not characters, which the guide states under that heading and
    `sql_query` next door has a cap and a comment for. Measured here: a two-million
    character table alias obeyed the row cap and came back as a 2,000,008-character
    answer in a hundredth of a second, and `verbose=True` a 500,020-character one. A
    plan names whatever the statement names, and the statement is the model's.
    """
    held = _globals(tools["sql_explain"])
    wide = 'SELECT region FROM sales AS "' + "z" * 2_000_000 + '"'

    for verbose in (False, True):
        said = tools["sql_explain"].invoke({"statement": wide, "verbose": verbose})

        assert len(said) <= held["MAX_ANSWER_CHARS"], f"verbose={verbose}: {len(said)}"
        assert said.splitlines()[-1].startswith(f"-- cut at {held['MAX_ANSWER_CHARS']}")


def test_a_plan_that_fits_carries_no_cut_and_no_mark(tools, database):
    """The other half: a note on every plan tells a model something was dropped from
    an answer that is whole, and the mark is what a reader uses to tell the steps from
    the note.
    """
    said = tools["sql_explain"].invoke({"statement": "SELECT region FROM sales"})

    assert "cut at" not in said
    assert said.startswith("1. ")


def test_the_plan_stops_at_the_rows_it_says_it_does(tools, database, monkeypatch):
    """`MAX_ROWS` was the guard nothing reached: raising it to a billion left the whole
    suite green, so the one bound this tool had was held by its own comment.

    Patched down, so the fixture is a three-way join rather than a plan of two hundred
    steps -- and the shipped number is then held by the rule below, because a rule that
    patches the cap cannot see the cap change.
    """
    monkeypatch.setitem(_globals(tools["sql_explain"]), "MAX_ROWS", 1)

    said = tools["sql_explain"].invoke(
        {"statement": "SELECT region FROM sales a, sales b, sales c"}
    )

    assert len(said.splitlines()) == 1, said


def test_the_row_cap_is_a_bound_rather_than_a_budget(tools):
    """Its own comment says it is "a guard against a pathological one rather than a
    budget worth tuning", and a guard set to a billion is not one. A floor under the
    claim rather than a pin on the number, since the rule above patches it to drive it.
    """
    held = _globals(tools["sql_explain"])["MAX_ROWS"]

    assert 10 <= held <= 1_000, held


def test_the_plan_is_read_on_a_connection_that_refuses_a_write(tools, database):
    """Its neighbour's identical guard is driven and this one was not: swapping the
    read-only URI for a plain `connect` left the suite green. A plan is a read, and the
    connection is what makes that true rather than assumed.
    """
    held = _globals(tools["sql_explain"])
    opened = f"file:{held['DATABASE']}?mode=ro"

    with closing(sqlite3.connect(opened, uri=True)) as db, pytest.raises(
        sqlite3.OperationalError, match="readonly"
    ):
        db.execute("DELETE FROM sales")

    # And the module opens exactly that, rather than this test opening its own: a rule
    # asserting on a connection of its own making passes whatever the tool does.
    source = Path(held["__file__"]).read_text(encoding="utf-8")
    assert '?mode=ro", uri=True' in source


def test_explaining_does_not_run_the_statement(tools, database):
    """What the argument's own description promises -- "an expensive statement is safe
    to pass here" -- and the reason a plan is read read-only.
    """
    said = tools["sql_explain"].invoke({"statement": "SELECT count(*) FROM sales"})

    assert str(len(AWKWARD)) not in said, "the answer carries a result, so it ran"
