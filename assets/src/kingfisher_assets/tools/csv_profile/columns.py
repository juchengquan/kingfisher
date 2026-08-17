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
