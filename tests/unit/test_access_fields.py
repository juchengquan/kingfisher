"""Reading a selection that may say who each of its entries is for."""

from __future__ import annotations

import pytest

from kingfisher.domain.capabilities import ALL
from kingfisher.domain.fields import Reader
from kingfisher.kinds.agents.spec import AgentError

read = Reader(source="x.yaml", error=AgentError)


def test_a_list_is_a_selection_with_no_audiences():
    assert read.audienced(["a", "b"], absent=ALL, key="tools") == (("a", "b"), {})


def test_omitted_is_the_absent_value_with_no_audiences():
    assert read.audienced(None, absent=ALL, key="tools") == (ALL, {})


def test_omitted_respects_the_fields_own_absent_value():
    """`tools` inherits everything when omitted; `skills` grants none."""
    assert read.audienced(None, absent=None, key="skills") == (None, {})


def test_star_still_means_everything():
    assert read.audienced(["*"], absent=ALL, key="tools") == (ALL, {})


def test_an_empty_list_still_means_none():
    assert read.audienced([], absent=ALL, key="tools") == ((), {})


def test_entries_select_their_names_and_carry_their_audiences():
    selected, audiences = read.audienced(
        [
            {"name": "sql_query", "groups": ["A"]},
            {"name": "http_fetch", "groups": ["*"]},
        ],
        absent=ALL,
        key="tools",
    )
    assert selected == ("sql_query", "http_fetch")
    assert audiences == {"sql_query": ("A",), "http_fetch": ALL}


def test_the_field_level_mapping_is_refused_and_says_what_to_write():
    """The shape this field used to take, and the reason it stopped."""
    with pytest.raises(AgentError, match=r"name: sql_query, groups"):
        read.audienced({"sql_query": {"groups": ["A"]}}, absent=ALL, key="tools")


def test_an_entry_that_is_neither_a_name_nor_a_mapping_is_refused():
    """`- [A]` has no reading."""
    with pytest.raises(AgentError, match="neither a name nor a mapping"):
        read.audienced([["A"]], absent=ALL, key="tools")


def test_an_entry_that_states_no_audience_inherits_the_definitions():
    """What makes the mapping form usable: only the entries you actually restrict carry
    a `groups:` line, and the rest are selected and left to inherit.
    """
    selected, audiences = read.audienced(
        [{"name": "sql_query", "groups": ["A"]}, "http_fetch", {"name": "line_count"}],
        absent=ALL,
        key="tools",
    )
    assert selected == ("sql_query", "http_fetch", "line_count")
    assert audiences == {"sql_query": ("A",)}


def test_long_entries_that_restrict_nothing_are_just_names():
    """The degenerate case reads as what it is, rather than being refused."""
    assert read.audienced(
        [{"name": "a"}, {"name": "b"}], absent=ALL, key="tools"
    ) == (("a", "b"), {})


def test_a_mistyped_entry_key_is_refused_with_a_suggestion():
    """What the nested form buys that a bare list cannot: an entry has keys, so a typo
    in one is catchable.
    """
    with pytest.raises(AgentError, match="did you mean 'groups'"):
        read.audienced([{"name": "sql_query", "grops": ["A"]}], absent=ALL, key="tools")


def test_a_bare_string_audience_is_refused_rather_than_iterated():
    """`groups: A` would otherwise become the groups 'A' spelled one letter at a time,
    which is the mistake `selection` refuses one level up.
    """
    with pytest.raises(AgentError, match="a list of names"):
        read.audienced([{"name": "sql_query", "groups": "A"}], absent=ALL, key="tools")


def test_an_empty_entry_audience_is_refused():
    """`groups: []` would mean nobody, and the way to say "no restriction" is to leave
    the line out -- so an empty one is an unfinished edit.
    """
    with pytest.raises(AgentError, match="Leave the line out"):
        read.audienced([{"name": "sql_query", "groups": []}], absent=ALL, key="tools")


def test_a_star_mixed_with_names_is_refused():
    with pytest.raises(AgentError, match="cannot mean both"):
        read.audienced([{"name": "sql_query", "groups": ["*", "A"]}], absent=ALL, key="tools")


def test_an_entry_may_not_be_named_the_star():
    """The star is a property of the field, not of an entry."""
    with pytest.raises(AgentError, match="does not take"):
        read.audienced([{"name": "*", "groups": ["A"]}], absent=ALL, key="tools")


def test_a_name_written_twice_is_refused_rather_than_collapsed():
    """The reason this field stopped taking a mapping, asserted here."""
    with pytest.raises(AgentError, match="names 'sql_query' twice"):
        read.audienced(
            [
                {"name": "sql_query", "groups": ["A"]},
                {"name": "sql_query", "groups": ["B"]},
            ],
            absent=ALL,
            key="tools",
        )


def test_a_bare_name_and_the_same_name_written_long_are_still_twice():
    """The spellings mix in one list, so the check is on names and not shapes."""
    with pytest.raises(AgentError, match="names 'sql_query' twice"):
        read.audienced(
            ["sql_query", {"name": "sql_query", "groups": ["A"]}], absent=ALL, key="tools"
        )


def test_the_source_is_named_in_every_refusal():
    with pytest.raises(AgentError, match=r"x\.yaml"):
        read.audienced([{"name": "sql_query", "groups": []}], absent=ALL, key="tools")


# -- requiring several groups at once ---------------------------------------


def test_a_conjunction_is_one_entry_of_an_entry_audience():
    assert read.audienced(
        [{"name": "sql_query", "groups": ["admin", {"all_of": ["finance", "senior"]}]}],
        absent=ALL,
        key="tools",
    ) == (("sql_query",), {"sql_query": ("admin", frozenset({"finance", "senior"}))})


def test_a_requirement_that_is_not_a_list_is_refused():
    """`{all_of: {finance: ...}}` fell through the guard and was iterated for its
    keys, which reads as a requirement the author never wrote.

    The string form has a refusal of its own because it looks like a list of
    letters; everything else shares this one, and nothing named it.
    """
    for written in [{"finance": True}, 3, True]:
        with pytest.raises(AgentError, match="list of group names"):
            read.audienced(
                [{"name": "sql_query", "groups": [{"all_of": written}]}], absent=ALL, key="tools"
            )


def test_a_conjunction_of_one_is_that_one_name():
    """Not special-cased: a set of one is satisfied by holding one, which is what the
    bare name means.
    """
    _, audiences = read.audienced(
        [{"name": "sql_query", "groups": [{"all_of": ["finance"]}]}], absent=ALL, key="tools"
    )

    assert audiences["sql_query"] == (frozenset({"finance"}),)


def test_an_empty_conjunction_is_refused():
    """It would require nothing and so admit everyone, which is not what somebody
    writing `all_of` was reaching for.
    """
    with pytest.raises(AgentError, match="empty"):
        read.audienced([{"name": "sql_query", "groups": [{"all_of": []}]}], absent=ALL, key="tools")


def test_a_mistyped_key_inside_a_conjunction_is_refused_with_a_suggestion():
    with pytest.raises(AgentError, match="did you mean 'all_of'"):
        read.audienced(
            [{"name": "sql_query", "groups": [{"all_off": ["A", "B"]}]}], absent=ALL, key="tools"
        )


def test_a_conjunction_written_as_the_whole_audience_is_refused():
    """`groups: {all_of: [...]}` has no list to be one entry of, so it reads as the
    whole audience being a mapping -- and the refusal shows the brackets.
    """
    with pytest.raises(AgentError, match=r"\[\{all_of: \[A, B\]\}\]"):
        read.audienced(
            [{"name": "sql_query", "groups": {"all_of": ["A", "B"]}}], absent=ALL, key="tools"
        )


def test_everyone_cannot_be_part_of_a_requirement():
    """`*` is everyone, so requiring it alongside a group is either everyone or that
    group, and there is no way to tell which was meant.
    """
    with pytest.raises(AgentError, match="everyone"):
        read.audienced(
            [{"name": "sql_query", "groups": [{"all_of": ["*", "A"]}]}], absent=ALL, key="tools"
        )


def test_a_definitions_own_line_reads_a_conjunction_the_same_way():
    """One reader for both sites, so the two cannot drift about what the same list
    means.
    """
    assert read.groups(["admin", {"all_of": ["finance", "senior"]}]) == (
        "admin",
        frozenset({"finance", "senior"}),
    )


def test_a_definitions_own_line_still_takes_a_single_unbracketed_name():
    """The one thing the two sites do not agree on, kept rather than reconciled: every
    list field in these formats takes a lone name, and an entry's audience is already
    nested in a mapping where a bare string reads as an unfinished edit.
    """
    assert read.groups("analysts") == ("analysts",)
