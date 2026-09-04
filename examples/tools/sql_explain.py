"""The third shape a tool may be written in: a class, for the schema it declares.

`http_fetch` is a `BaseTool` made by `@tool`, and `line_count` is a plain
function. This is the one you reach for when the *arguments* deserve describing
-- the decorator infers a schema from the annotations, and a class lets you write
one.

What that buys is per-argument description. A model deciding what to pass as
`verbose` has only the name to go on under the other two shapes; here it carries
a sentence saying when to set it, and its default is stated rather than implied. That is the same
argument the docstring makes for the tool as a whole, one level down.

**`TOOLS` holds an instance, not the class.** `SqlExplain()` rather than
`SqlExplain`, and the near miss is refused by name rather than offered -- a class
is callable, so nothing else would notice until the model called it and got a
tool object back instead of an answer.

Beside `sql_query` deliberately: explaining a statement is what you do *before*
running one you are unsure about, and both resolve the same database the same
read-only way. They are separate modules because this one is here to show a
shape, and a reader looking for the shape should not have to find it inside a
file about something else.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

#: The same database `sql_query` opens, and the same reasoning about why a
#: constant may be a host path when an argument may not.
DATABASE = Path("~/kingfisher-example.db").expanduser()

#: A plan is short. This is a guard against a pathological one rather than a
#: budget worth tuning.
MAX_ROWS = 200


class ExplainInput(BaseModel):
    """What `sql_explain` takes, with each argument described for the model."""

    statement: str = Field(
        description=(
            "The SELECT statement to explain. Not executed -- only its query "
            "plan is read, so an expensive statement is safe to pass here."
        )
    )
    verbose: bool = Field(
        default=False,
        description=(
            "Return the raw plan rows rather than one line per step. Use when a "
            "summary has already told you the plan is worth reading closely."
        ),
    )


class SqlExplain(BaseTool):
    """Show how the database would run a statement, without running it.

    Use before a query you expect to be slow, or when one has already come back
    slower than you expected and you want to know whether it is scanning a
    whole table.
    """

    name: str = "sql_explain"
    # Written out rather than left to the docstring. A class-shaped tool may
    # take its description from either, and saying it here keeps the sentence
    # the model reads next to the schema it goes with.
    description: str = (
        "Show how the database would run a SELECT statement, without running "
        "it. Use before a query you expect to be slow, or to find out whether "
        "one is scanning a whole table."
    )
    args_schema: type[BaseModel] = ExplainInput

    def _run(self, statement: str, verbose: bool = False) -> str:
        if not statement.lstrip().lower().startswith("select"):
            return "refused: only SELECT statements can be explained here"

        if not DATABASE.exists():
            return f"no database at {DATABASE}"

        try:
            # Read-only, like its neighbour: a plan is a read, and a URI
            # connection is what makes that true rather than assumed.
            with sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True) as db:
                rows = db.execute(f"EXPLAIN QUERY PLAN {statement}").fetchmany(MAX_ROWS)
        except sqlite3.Error as exc:
            # A returned string rather than a raise. The failure guard would
            # turn an exception into a tool result anyway; saying it here lets
            # the message name the statement rather than the traceback.
            return f"could not explain that statement: {exc}"

        if not rows:
            return "the database returned no plan for that statement"
        if verbose:
            return "\n".join(str(row) for row in rows)
        # The detail column is the readable one; the three before it are the
        # node ids a plan is a tree of, and a model reading a summary wants the
        # steps rather than the tree.
        return "\n".join(f"{n + 1}. {row[-1]}" for n, row in enumerate(rows))


#: An *instance*. `SqlExplain` alone is refused, naming this line.
TOOLS = [SqlExplain()]
