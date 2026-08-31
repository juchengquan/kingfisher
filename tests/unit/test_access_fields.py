"""Reading a selection that may say who each of its entries is for.

The second spelling is a strict extension of the first: every file written
before audiences existed reads identically through this, which is what lets a
`groups:` line be added above one without touching the rest.
"""

from __future__ import annotations

import pytest

from kingfisher.domain.agent import AgentError
from kingfisher.domain.capabilities import ALL
from kingfisher.domain.fields import Reader

read = Reader(source="x.yaml", error=AgentError)


def test_a_list_is_a_selection_with_no_audiences():
    assert read.audienced(["a", "b"], absent=ALL, key="tools") == (("a", "b"), {})


def test_omitted_is_the_absent_value_with_no_audiences():
    assert read.audienced(None, absent=ALL, key="tools") == (ALL, {})


def test_omitted_respects_the_fields_own_absent_value():
    """`tools` inherits everything when omitted; `skills` grants none. The pair
    form must not flatten that difference."""
    assert read.audienced(None, absent=None, key="skills") == (None, {})


def test_star_still_means_everything():
    assert read.audienced(["*"], absent=ALL, key="tools") == (ALL, {})


def test_an_empty_list_still_means_none():
    assert read.audienced([], absent=ALL, key="tools") == ((), {})


def test_a_mapping_selects_its_keys_and_carries_its_values():
    selected, audiences = read.audienced(
        {"sql_query": {"groups": ["A"]}, "http_fetch": {"groups": ["*"]}},
        absent=ALL,
        key="tools",
    )
    assert selected == ("sql_query", "http_fetch")
    assert audiences == {"sql_query": ("A",), "http_fetch": ALL}


def test_an_empty_mapping_is_refused():
    """`tools: {}` reads as nothing, which is spelled `[]` -- and is far more
    likely to be an unfinished edit than a decision."""
    with pytest.raises(AgentError, match=r"write \[\]"):
        read.audienced({}, absent=ALL, key="tools")


def test_a_bare_list_entry_is_refused_by_name():
    """The shorthand this format deliberately does not have. Two spellings of
    one thing is what it keeps deleting, and the refusal shows the one to
    write rather than leaving a reader to guess at it."""
    with pytest.raises(AgentError, match=r"written `groups: \[A\]`"):
        read.audienced({"sql_query": ["A"]}, absent=ALL, key="tools")


def test_an_entry_that_states_no_audience_inherits_the_definitions():
    """What makes the mapping form usable: only the entries you actually
    restrict carry a `groups:` line, and the rest are selected and left to
    inherit. Restricting one tool must not mean writing an audience for every
    other tool beside it."""
    selected, audiences = read.audienced(
        {"sql_query": {"groups": ["A"]}, "http_fetch": None, "line_count": {}},
        absent=ALL,
        key="tools",
    )
    assert selected == ("sql_query", "http_fetch", "line_count")
    assert audiences == {"sql_query": ("A",)}


def test_a_mapping_where_nothing_is_restricted_is_just_a_list():
    """The degenerate case reads as what it is, rather than being refused."""
    assert read.audienced({"a": None, "b": None}, absent=ALL, key="tools") == (
        ("a", "b"),
        {},
    )


def test_a_mistyped_entry_key_is_refused_with_a_suggestion():
    """What the nested form buys that a bare list cannot: an entry has keys, so
    a typo in one is catchable."""
    with pytest.raises(AgentError, match="did you mean 'groups'"):
        read.audienced({"sql_query": {"grops": ["A"]}}, absent=ALL, key="tools")


def test_a_bare_string_audience_is_refused_rather_than_iterated():
    """`groups: A` would otherwise become the groups 'A' spelled one letter at
    a time, which is the mistake `selection` refuses one level up."""
    with pytest.raises(AgentError, match="a list of names"):
        read.audienced({"sql_query": {"groups": "A"}}, absent=ALL, key="tools")


def test_an_empty_entry_audience_is_refused():
    """`groups: []` would mean nobody, and the way to say "no restriction" is
    to leave the line out -- so an empty one is an unfinished edit."""
    with pytest.raises(AgentError, match="Leave the line out"):
        read.audienced({"sql_query": {"groups": []}}, absent=ALL, key="tools")


def test_a_star_mixed_with_names_is_refused():
    with pytest.raises(AgentError, match="cannot mean both"):
        read.audienced({"sql_query": {"groups": ["*", "A"]}}, absent=ALL, key="tools")


def test_a_mapping_may_not_name_the_star_as_an_entry():
    """The star is a property of the field, not of an entry."""
    with pytest.raises(AgentError, match="not a name"):
        read.audienced({"*": {"groups": ["A"]}}, absent=ALL, key="tools")


def test_the_source_is_named_in_every_refusal():
    with pytest.raises(AgentError, match=r"x\.yaml"):
        read.audienced({"sql_query": {"groups": []}}, absent=ALL, key="tools")
