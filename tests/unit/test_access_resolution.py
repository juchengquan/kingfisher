"""The source-id vocabulary, and the rule the definitions apply to it."""

from __future__ import annotations

import pytest

from kingfisher.core.access.capabilities import ALL
from kingfisher.core.access.reach import reaches, reaching
from kingfisher.core.access.source_ids import (
    UNSCOPED,
    AccessError,
    SourceIds,
    jointly,
    listed,
    spell,
    unlisted,
)


def vocabulary() -> SourceIds:
    """A flat A/B/C vocabulary."""
    return SourceIds(names=frozenset({"A", "B", "C"}))


# -- who reaches what -------------------------------------------------------


def test_any_one_source_id_is_enough():
    """The list is an OR, not an AND: a longer list means more callers."""
    assert reaches(("A", "B"), frozenset({"B", "C"}))


def test_no_overlap_reaches_nothing():
    assert not reaches(("A", "B"), frozenset({"C"}))


def test_a_star_audience_reaches_everyone():
    assert reaches(ALL, frozenset({"C"}))


def test_a_caller_holding_nothing_reaches_nothing():
    assert not reaches(("A",), frozenset())


# -- narrowing a selection --------------------------------------------------


def test_an_entry_with_no_audience_falls_back_to_the_definitions():
    """What makes a plain list under a policied definition mean 'these, at my audience'
    -- so every file written before audiences keeps its meaning.
    """
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
    """`ALL` means everything available, bounded by the definition's own audience rather
    than by any entry.
    """
    assert reaching(ALL, audiences={}, default=("A",), held=frozenset({"A"})) == ALL


def test_none_passes_through_untouched():
    assert reaching(None, audiences={}, default=("A",), held=frozenset({"A"})) is None


# -- narrowing past the definition ------------------------------------------------------------


def narrowing(entry, *, source_ids, vocab=None):
    """What the vocabulary says one skills entry asks beyond its definition."""
    return (vocab or vocabulary()).narrowing_in(
        {"audit": entry}, source_ids=source_ids, where="x.yaml"
    )


def test_an_entry_outside_the_definitions_own_audience_is_reported():
    """Reported, not refused."""
    assert narrowing(("C",), source_ids=("A", "B")) == (("x.yaml: skill audit", "C"),)


def test_an_overlapping_entry_is_not_narrowing():
    assert narrowing(("A",), source_ids=("A", "B")) == ()


def test_nothing_narrows_when_the_definition_reaches_everyone():
    assert narrowing(("C",), source_ids=ALL) == ()


def test_a_star_entry_never_narrows():
    assert narrowing(ALL, source_ids=("A",)) == ()


def test_an_inline_set_does_not_narrow_beside_either_part():
    """Either part, in either direction: the definition writes the set and the entry
    names a part, or the other way about.
    """
    both = (frozenset({"A", "B"}),)
    assert narrowing(("A",), source_ids=both) == ()
    assert narrowing(both, source_ids=("A",)) == ()


def test_an_inline_set_sharing_no_part_narrows():
    assert narrowing(("C",), source_ids=(frozenset({"A", "B"}),)) == (
        ("x.yaml: skill audit", "C"),
    )


def test_the_second_requirement_a_narrowing_states_actually_works():
    """The whole reason the refusal went."""
    audiences = {"export": ("C",)}
    tools = ("export", "line_count")

    both = reaching(tools, audiences=audiences, default=("A", "B"), held=frozenset({"A", "C"}))
    one = reaching(tools, audiences=audiences, default=("A", "B"), held=frozenset({"A"}))

    assert both == ("export", "line_count")
    assert one == ("line_count",)


# -- the vocabulary ---------------------------------------------------------


def test_a_source_id_is_held_as_itself_and_nothing_more():
    """A name covers nothing: a caller sending `A` holds `A`, whatever else is declared."""
    assert vocabulary().known(["A"]) == frozenset({"A"})


def test_no_source_ids_at_all_holds_nothing():
    assert vocabulary().known([]) == frozenset()


def test_an_unknown_source_id_is_refused_rather_than_ignored():
    """A typo would otherwise reach nothing, which looks exactly like a caller who was
    denied.
    """
    with pytest.raises(AccessError, match="unknown source id"):
        vocabulary().known(["Q"])


def test_a_definition_naming_an_undeclared_source_id_is_refused():
    """The other end of the closed vocabulary: a mistyped audience would otherwise
    invent a source id nobody holds, and the only symptom would be something quietly
    reachable by no one.
    """
    complaint = vocabulary().undeclared(("Q",), where="x.yaml: tools")

    assert complaint is not None
    assert "undeclared source id" in complaint


def test_a_star_names_no_source_id_so_is_never_undeclared():
    assert vocabulary().undeclared(ALL, where="x.yaml: tools") is None


def test_unscoped_is_a_sentinel_and_not_a_source_id_name():
    assert UNSCOPED is not None
    assert not isinstance(UNSCOPED, str | tuple | list)


# -- requiring several source ids at once ---------------------------------------


def test_an_inline_conjunction_needs_every_part():
    both = (frozenset({"A", "B"}),)

    assert reaches(both, frozenset({"A", "B"}))
    assert reaches(both, frozenset({"A", "B", "C"}))
    assert not reaches(both, frozenset({"A"}))
    assert not reaches(both, frozenset())


def test_a_conjunction_is_one_entry_of_an_or():
    """The list still means OR. `admin` alone is enough, and so is A+B."""
    audience = ("admin", frozenset({"A", "B"}))

    assert reaches(audience, frozenset({"admin"}))
    assert reaches(audience, frozenset({"A", "B"}))
    assert not reaches(audience, frozenset({"A"}))


def test_an_undeclared_name_inside_a_conjunction_is_refused():
    """A name typed from memory is no likelier to be right for having been written next
    to another one.
    """
    complaint = vocabulary().undeclared((frozenset({"A", "Q"}),), where="x.yaml: tools")

    assert complaint is not None
    assert "'Q'" in complaint


def test_an_audience_is_written_the_way_the_listing_writes_it():
    assert spell(ALL) == ALL
    assert spell(("A", "B")) == "A, B"
    assert spell(("admin", frozenset({"B", "A"}))) == "admin, {A, B}"


# -- who reaches all of several audiences at once --------------------------------

# One case per decision `jointly` makes. Each was green with that decision removed:
# the only fixture driving it wrote plain single names, where set-dedup collapses every
# answer it can give, so four separate mutations changed nothing anybody could see.


def test_reaching_one_audience_is_that_audience():
    """A way of one name is unwrapped, so a row holds `["A"]` and not `[["A"]]`."""
    assert jointly([("A",)]) == ("A",)


def test_a_set_stays_a_set():
    assert jointly([(frozenset({"A", "B"}),)]) == (frozenset({"A", "B"}),)


def test_reaching_nothing_in_particular_is_everyone():
    """An empty join is `ALL` rather than a way holding nothing, which reaches nobody."""
    assert jointly([]) == ALL
    assert jointly([ALL, ALL]) == ALL


def test_everyone_drops_out_of_a_join():
    """`ALL` is the string `"*"`, so a join that iterated it instead of skipping it would
    require the character as though it were a source id.
    """
    assert jointly([ALL, ("A",)]) == ("A",)


def test_a_way_no_other_needs_less_than_is_dropped():
    """A+B is not worth listing beside A: anyone who has both has A. Kept, the row
    offers a caller a longer route to what they already reach.
    """
    assert jointly([("A",), ("A", frozenset({"A", "B"}))]) == ("A",)


def test_two_ways_that_need_different_things_are_both_kept():
    assert jointly([("A", "B")]) == ("A", "B")


# -- and the row it is written to ------------------------------------------------


def test_a_set_survives_a_row_and_comes_back_able_to_be_joined():
    """`unlisted` has to give back the frozenset `listed` flattened, not the tuple it
    wrote: `jointly` reads anything that is not a frozenset as one source id's name, so
    handed a tuple it compares a tuple with a string and the turn dies where no
    `except` is watching.
    """
    audience = ("A", frozenset({"B", "C"}))

    row = listed(audience)

    assert row == ("A", ("B", "C")), "a row has to be JSON, so the set is a sorted list"
    assert unlisted(list(row)) == audience
    assert jointly([unlisted(list(row)), ("A",)]) == ("A",)
