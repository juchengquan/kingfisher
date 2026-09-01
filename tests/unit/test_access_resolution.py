"""The group vocabulary, and the rule the definitions apply to it.

Pure: no file, no workspace, no agent. Audiences themselves live in the
definitions and are tested with the formats that carry them -- what is asserted
here is the vocabulary a definition's audience is checked against, and the
overlap rule every one of them applies.
"""

from __future__ import annotations

import pytest

from kingfisher.domain.access import (
    UNSCOPED,
    AccessError,
    Groups,
    reaches,
    reaching,
)
from kingfisher.domain.capabilities import ALL


def vocabulary(**contains: tuple[str, ...]) -> Groups:
    """A flat A/B/C vocabulary, plus any containing groups the test names."""
    names = {one: (one,) for one in ("A", "B", "C")}
    for name, holds in contains.items():
        names[name] = (name, *holds)
    return Groups(names=names)


# -- who reaches what -------------------------------------------------------


def test_any_one_group_is_enough():
    """The list is an OR, not an AND: a longer list means more people."""
    assert reaches(("A", "B"), frozenset({"B", "C"}))


def test_no_overlap_reaches_nothing():
    assert not reaches(("A", "B"), frozenset({"C"}))


def test_a_star_audience_reaches_everyone():
    assert reaches(ALL, frozenset({"C"}))


def test_a_caller_holding_nothing_reaches_nothing():
    assert not reaches(("A",), frozenset())


# -- narrowing a selection --------------------------------------------------


def test_an_entry_with_no_audience_falls_back_to_the_definitions():
    """What makes a plain list under a policied definition mean 'these, at my
    audience' -- so every file written before audiences keeps its meaning."""
    assert reaching(
        ("sql_query",), audiences={}, default=("A",), held=frozenset({"A"})
    ) == ("sql_query",)
    assert reaching(("sql_query",), audiences={}, default=("A",), held=frozenset({"B"})) == ()


def test_an_entry_with_its_own_audience_uses_it():
    audiences = {"sql_query": ("A",), "http_fetch": ("A", "B", "C")}
    assert reaching(
        ("sql_query", "http_fetch"),
        audiences=audiences,
        default=("A", "B", "C"),
        held=frozenset({"C"}),
    ) == ("http_fetch",)


def test_all_passes_through_untouched():
    """`ALL` means everything available, bounded by the definition's own
    audience rather than by any entry."""
    assert reaching(ALL, audiences={}, default=("A",), held=frozenset({"A"})) == ALL


def test_none_passes_through_untouched():
    assert reaching(None, audiences={}, default=("A",), held=frozenset({"A"})) is None


# -- dead policy ------------------------------------------------------------


def dead(entry, *, groups, vocab=None):
    """Ask the vocabulary whether one entry audience can ever reach anyone."""
    (vocab or vocabulary()).refuse_dead(
        {"tools": {"sql_query": entry}}, groups=groups, where="x.yaml", error=ValueError
    )


def test_an_entry_outside_the_definitions_own_audience_is_refused():
    """Nobody reaching this definition is ever in C, so the line can never
    grant anything -- a mistake, not a narrowing."""
    with pytest.raises(ValueError, match="never reaches anyone"):
        dead(("C",), groups=("A", "B"))


def test_an_overlapping_entry_is_allowed():
    dead(("A",), groups=("A", "B"))


def test_nothing_is_dead_when_the_definition_reaches_everyone():
    dead(("C",), groups=ALL)


def test_a_star_entry_is_never_dead():
    dead(ALL, groups=("A",))


def test_a_contained_entry_is_alive():
    """The bug that moving this check off `parse` fixed. A caller holding `wide`
    holds `A` too, so an entry for `A` under a definition for `wide` reaches
    exactly them -- and comparing raw names called that dead."""
    dead(("A",), groups=("wide",), vocab=vocabulary(wide=("A",)))


def test_a_compound_is_alive_beside_either_part():
    """Either part, in either direction: the definition names the compound and
    the entry names a part, or the other way about."""
    vocab = vocabulary(**{"A+B": ()})
    both = Groups(names=vocab.names, compounds={"A+B": ("A", "B")})
    dead(("A",), groups=("A+B",), vocab=both)
    dead(("A+B",), groups=("A",), vocab=both)


def test_a_compound_sharing_no_part_is_still_dead():
    both = Groups(names={n: (n,) for n in ("A", "B", "C", "A+B")}, compounds={"A+B": ("A", "B")})
    with pytest.raises(ValueError, match="never reaches anyone"):
        dead(("C",), groups=("A+B",), vocab=both)


# -- the vocabulary ---------------------------------------------------------


def test_a_group_expands_to_itself():
    assert vocabulary().expand(["A"]) == frozenset({"A"})


def test_a_containing_group_reaches_what_it_contains():
    """The reason `contains` exists: `admin` need not appear on a single line."""
    assert vocabulary(admin=("A", "B")).expand(["admin"]) == frozenset({"admin", "A", "B"})


def test_no_groups_at_all_expands_to_nothing():
    assert vocabulary().expand([]) == frozenset()


def test_an_unknown_group_is_refused_rather_than_ignored():
    """A typo would otherwise reach nothing, which looks exactly like a caller
    who was denied."""
    with pytest.raises(AccessError, match="unknown group"):
        vocabulary().expand(["Q"])


def test_a_definition_naming_an_undeclared_group_is_refused():
    """The other end of the closed vocabulary: a mistyped audience would
    otherwise invent a group nobody is in, and the only symptom would be
    something quietly reachable by no one."""
    with pytest.raises(ValueError, match="undeclared group"):
        vocabulary().refuse_undeclared(("Q",), where="x.yaml: tools", error=ValueError)


def test_a_star_names_no_group_so_is_never_undeclared():
    vocabulary().refuse_undeclared(ALL, where="x.yaml: tools", error=ValueError)


def test_unscoped_is_a_sentinel_and_not_a_group_name():
    assert UNSCOPED is not None
    assert not isinstance(UNSCOPED, str | tuple | list)
