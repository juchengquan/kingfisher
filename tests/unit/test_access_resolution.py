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
    spell,
)
from kingfisher.domain.capabilities import ALL


def vocabulary(**contains: tuple[str, ...]) -> Groups:
    """A flat A/B/C vocabulary, plus any containing groups the test names."""
    names = {one: (one,) for one in ("A", "B", "C")}
    for name, holds in contains.items():
        names[name] = (name, *holds)
    return Groups(names=names)


def requiring(**all_of: tuple[str, ...]) -> Groups:
    """The same vocabulary, plus compounds a caller must hold the parts of."""
    names = {one: (one,) for one in ("A", "B", "C")}
    names.update({name: (name,) for name in all_of})
    return Groups(names=names, compounds=dict(all_of))


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


# -- narrowing past the definition ------------------------------------------------------------


def narrowing(entry, *, groups, vocab=None):
    """What the vocabulary says one entry audience asks beyond its definition."""
    return (vocab or vocabulary()).narrowing_in(
        {"tools": {"sql_query": entry}}, groups=groups, where="x.yaml"
    )


def test_an_entry_outside_the_definitions_own_audience_is_reported():
    """Reported, not refused. It reaches whoever holds C *and* A-or-B, which is
    a second requirement somebody may well have meant -- and is also what a
    mistake looks like, which is why it is said out loud and not vetoed."""
    assert narrowing(("C",), groups=("A", "B")) == (("x.yaml: tool sql_query", "C"),)


def test_an_overlapping_entry_is_not_narrowing():
    assert narrowing(("A",), groups=("A", "B")) == ()


def test_nothing_narrows_when_the_definition_reaches_everyone():
    assert narrowing(("C",), groups=ALL) == ()


def test_a_star_entry_never_narrows():
    assert narrowing(ALL, groups=("A",)) == ()


def test_a_contained_entry_does_not_narrow():
    """Judged on meaning, not spelling. A caller holding `wide` holds `A` too,
    so an entry for `A` under a definition for `wide` asks nothing extra --
    and comparing raw names called it narrower."""
    assert narrowing(("A",), groups=("wide",), vocab=vocabulary(wide=("A",))) == ()


def test_a_compound_does_not_narrow_beside_either_part():
    """Either part, in either direction: the definition names the compound and
    the entry names a part, or the other way about."""
    vocab = vocabulary(**{"A+B": ()})
    both = Groups(names=vocab.names, compounds={"A+B": ("A", "B")})
    assert narrowing(("A",), groups=("A+B",), vocab=both) == ()
    assert narrowing(("A+B",), groups=("A",), vocab=both) == ()


def test_a_compound_sharing_no_part_narrows():
    both = Groups(names={n: (n,) for n in ("A", "B", "C", "A+B")}, compounds={"A+B": ("A", "B")})
    assert narrowing(("C",), groups=("A+B",), vocab=both) == (("x.yaml: tool sql_query", "C"),)


def test_the_second_requirement_a_narrowing_states_actually_works():
    """The whole reason the refusal went. An entry naming a group outside the
    definition's own line reaches exactly the callers holding one of each --
    which is "everyone who opens this, and is senior", and has always evaluated
    correctly. Only the veto stood in the way."""
    audiences = {"export": ("C",)}
    tools = ("export", "line_count")

    both = reaching(tools, audiences=audiences, default=("A", "B"), held=frozenset({"A", "C"}))
    one = reaching(tools, audiences=audiences, default=("A", "B"), held=frozenset({"A"}))

    assert both == ("export", "line_count")
    assert one == ("line_count",)


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


# -- requiring several groups at once ---------------------------------------


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


def test_holding_every_part_earns_the_named_compound():
    assert requiring(both=("A", "B")).expand(["A", "B"]) == frozenset({"A", "B", "both"})


def test_holding_one_part_earns_nothing():
    assert requiring(both=("A", "B")).expand(["A"]) == frozenset({"A"})


def test_contains_satisfies_a_requirement():
    """Expansion first, then requirements against what is held afterwards. An
    `admin` who reaches both parts is not weaker than the sum of what they
    reach -- which is the alternative, and it has no defence."""
    vocab = Groups(
        names={"A": ("A",), "B": ("B",), "both": ("both",), "admin": ("admin", "A", "B")},
        compounds={"both": ("A", "B")},
    )

    assert vocab.expand(["admin"]) == frozenset({"admin", "A", "B", "both"})


def test_a_compound_a_group_contains_comes_with_it():
    """Earning a compound earns whatever contains it, or a name written into a
    `contains` chain would reach less than the same name written by hand."""
    vocab = Groups(
        names={"A": ("A",), "B": ("B",), "both": ("both", "C"), "C": ("C",)},
        compounds={"both": ("A", "B")},
    )

    assert vocab.expand(["A", "B"]) == frozenset({"A", "B", "both", "C"})


def test_a_caller_may_not_present_a_compound():
    """It is what holding the parts adds up to, not something to claim --
    otherwise one assertion stands in for the two `all_of` exists to require."""
    with pytest.raises(AccessError, match="derived"):
        requiring(both=("A", "B")).expand(["both"])


def test_the_refusal_names_the_parts_to_present_instead():
    with pytest.raises(AccessError, match=r"all of \[A, B\]"):
        requiring(both=("A", "B")).expand(["both"])


def test_an_undeclared_name_inside_a_conjunction_is_refused():
    """A name typed from memory is no likelier to be right for having been
    written next to another one."""
    with pytest.raises(ValueError, match="'Q'"):
        vocabulary().refuse_undeclared(
            (frozenset({"A", "Q"}),), where="x.yaml: tools", error=ValueError
        )


def test_an_audience_is_written_the_way_the_listing_writes_it():
    assert spell(ALL) == ALL
    assert spell(("A", "B")) == "A, B"
    assert spell(("admin", frozenset({"B", "A"}))) == "admin, A+B"
