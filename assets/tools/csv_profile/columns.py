"""What a column is, decided once.

A helper, and an ordinary module rather than an underscored one: the loader
does not scan inside a package, so nothing here has to hide from it. It is a
helper because `__init__.py` does not export it.

The rules are deliberately dull and stated rather than inferred from a library.
A profile that guessed differently from run to run would be worse than one that
guesses plainly and says so.
"""

from __future__ import annotations

from dataclasses import dataclass

#: What counts as absent. Compared case-folded and stripped, because a CSV
#: written by hand has all of these in it and they all mean the same thing.
BLANKS = frozenset({"", "na", "n/a", "null", "none", "-", "nan"})


#: What a column name may not carry into an answer that is one line per column, and
#: what it is shown as instead. A header cell is uploaded data, and both tools here
#: answer a line at a time: a name holding a newline made a second line that read as a
#: column of its own. Measured -- a three-column file whose header cell held
#: `region\nsalary: number, 5000 filled, 0 missing (0%), 4998 distinct` reported four
#: columns, the fourth a complete and plausible row nobody wrote. `csv_columns`, whose
#: whole contract is "the column names, one per line", returned four names for three
#: columns.
#:
#: Escaped rather than stripped, so the name still says what the file holds and the
#: reader can see why it looks odd.
UNSHOWABLE = {"\n": "\\n", "\r": "\\r"}


def shown(name: str) -> str:
    """One column name as an answer of one line per column may carry it."""
    for raw, escaped in UNSHOWABLE.items():
        name = name.replace(raw, escaped)
    return name


#: What one name may add to an answer that is one line per column, and the rest of it is
#: said as a count. A header cell is uploaded data and the CSV reader parses one up to
#: 131,072 characters: measured, a file of 76 columns named that long answered 9,959,656
#: characters out of a tool whose docstring says the answer does not grow with the file.
#: Cut rather than refused, unlike a query's row that will not fit -- nothing a reader
#: can ask for makes a column's name shorter.
MAX_NAME_CHARS = 100


def fitted(name: str) -> str:
    """One column name as an answer of one line per column carries it.

    Cut after the escaping, so the number it names is what the answer would have
    carried rather than what the file holds: a name of 50,000 newlines is written as
    100,000 characters.
    """
    name = shown(name)
    if len(name) <= MAX_NAME_CHARS:
        return name
    return f"{name[:MAX_NAME_CHARS]}...(+{len(name) - MAX_NAME_CHARS} characters)"


def is_blank(value: str) -> bool:
    """True for a cell nobody filled in."""
    return value.strip().casefold() in BLANKS


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class Column:
    """One column, as a profile sees it."""

    #: Already `fitted` by its caller, not here: a repeated name carries its column
    #: number, and a cut taken after that was appended takes the number off.
    name: str
    filled: int
    missing: int
    distinct: int
    kind: str

    @property
    def total(self) -> int:
        return self.filled + self.missing

    def line(self) -> str:
        """One row of the report, which is what both tools ultimately return."""
        share = (self.missing / self.total * 100) if self.total else 0.0
        return (
            f"{self.name}: {self.kind}, {self.filled} filled, "
            f"{self.missing} missing ({share:.0f}%), {self.distinct} distinct"
        )


def profile_column(name: str, values: list[str]) -> Column:
    """Summarise one column's cells.

    `kind` is the narrowest description that fits every filled cell: a column
    of digits with one stray word in it is text, because calling it numeric
    would be the kind of almost-true that sends someone down a wrong path.
    """
    filled = [v for v in values if not is_blank(v)]
    kind = "empty"
    if filled:
        kind = "number" if all(_is_number(v) for v in filled) else "text"
    return Column(
        name=name,
        filled=len(filled),
        missing=len(values) - len(filled),
        distinct=len({v.strip() for v in filled}),
        kind=kind,
    )
