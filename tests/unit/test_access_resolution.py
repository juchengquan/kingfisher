"""What a caller holds, the vocabulary the definitions add up to, and the rule they
apply to both."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kingfisher.core.access.capabilities import ALL
from kingfisher.core.access.checks import collected, held_by
from kingfisher.core.access.reach import reaches, reaching
from kingfisher.core.access.source_ids import (
    UNSCOPED,
    AccessError,
    Datazone,
    Vocabulary,
    jointly,
    listed,
    narrowing_in,
    spell,
    unlisted,
)


def vocabulary() -> Vocabulary:
    """What an A/B/C deployment's locks add up to."""
    return Vocabulary(source_ids={"A": ("agent a",), "B": ("agent b",), "C": ("agent c",)})


def spec(source_ids=ALL, skill_audiences=None, datazone=None) -> SimpleNamespace:
    """A definition by the fields every kind carrying a lock has."""
    return SimpleNamespace(
        source_ids=source_ids, skill_audiences=skill_audiences or {}, datazone=datazone
    )


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


def narrowing(entry, *, source_ids):
    """What one skills entry asks beyond its definition."""
    return narrowing_in({"audit": entry}, source_ids=source_ids, where="x.yaml")


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


# -- what a caller holds -----------------------------------------------------


def test_a_source_id_is_held_as_itself_and_nothing_more():
    """A name covers nothing: a caller sending `A` holds `A`, whatever else is locked."""
    assert held_by(vocabulary(), ["A"]) == frozenset({"A"})


def test_no_source_ids_at_all_holds_nothing():
    assert held_by(vocabulary(), []) == frozenset()


def test_a_name_no_definition_uses_is_held_and_opens_nothing():
    """Refused, it made a call pass or fail on which locks existed that week; held, it
    reaches only what is unlocked."""
    held = held_by(vocabulary(), ["Q"])

    assert held is not None
    assert held == frozenset({"Q"})
    assert not reaches(("A", "B", "C"), held)
    assert reaches(ALL, held)


def test_a_datazone_no_definition_requires_is_held_and_opens_nothing():
    """The datazone half of the same: an unused zone reaches only what names none."""
    held = held_by(vocabulary(), ["A", Datazone("nowhere")])

    assert held is not None
    assert held == frozenset({"A", "datazone=nowhere"})
    assert not reaches(("datazone=emea",), held)


def test_a_source_id_spelt_as_a_datazone_is_refused():
    """Held among the names it would put a caller in a datazone nobody passed, past the
    rule that a caller is in one -- the control beside it is the zone passed as one."""
    with pytest.raises(AccessError, match="how a datazone is held"):
        held_by(vocabulary(), ["A", "datazone=finance"])
    assert held_by(vocabulary(), ["A", Datazone("finance")]) == frozenset(
        {"A", "datazone=finance"}
    )


@pytest.mark.parametrize(
    "named", [["A"], [Datazone("emea")]], ids=["a source id", "a datazone"]
)
def test_naming_a_caller_where_nothing_is_locked_is_refused(named):
    """Nothing narrows, so the answer would be the unrestricted one read as the caller's."""
    with pytest.raises(AccessError, match="no definition here carries a lock"):
        held_by(None, named)


def test_naming_nobody_where_nothing_is_locked_is_no_caller():
    """The control beside the refusal: an empty sequence and `UNSCOPED` are no caller to
    be wrong about."""
    assert held_by(None, []) is None
    assert held_by(None, UNSCOPED) is None


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


# -- the vocabulary the definitions add up to -----------------------------------------


def test_definitions_that_lock_nothing_add_up_to_no_vocabulary():
    """`None` is what turns checking off, so a walk returning an empty record instead
    would switch it on for a workspace with no lock in it."""
    assert collected(("agent", {"open": spec()}), ("tool", {})) is None


def test_every_name_a_lock_writes_is_collected_with_who_writes_it():
    """Inside a set and on a skills entry too, each definition named once however many of
    its lines use a name, so the listing's row for a name is the files to open."""
    found = collected(
        ("agent", {"analyst": spec(source_ids=("A", frozenset({"B", "C"})))}),
        ("subagent", {"auditor": spec(skill_audiences={"audit": ("A", "D")}, datazone="emea")}),
        ("tool", {"sql_query": spec(source_ids=("A",))}),
    )

    assert found == Vocabulary(
        source_ids={
            "A": ("agent analyst", "subagent auditor", "tool sql_query"),
            "B": ("agent analyst",),
            "C": ("agent analyst",),
            "D": ("subagent auditor",),
        },
        datazones={"emea": ("subagent auditor",)},
    )


@pytest.mark.parametrize(
    "said",
    [spec(skill_audiences={"audit": ("A",)}), spec(datazone="emea")],
    ids=["a skills entry", "a datazone"],
)
def test_one_lock_anywhere_is_enough_to_switch_checking_on(said):
    """A skills entry's audience and a datazone are locks as surely as a definition's
    own line: collected as nothing, a deployment using only those would run unchecked."""
    assert collected(("subagent", {"only": said})) is not None


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
