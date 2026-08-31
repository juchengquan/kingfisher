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


def test_an_entry_that_says_nothing_is_refused():
    with pytest.raises(AgentError, match="says nothing"):
        read.audienced({"sql_query": {}}, absent=ALL, key="tools")


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
    with pytest.raises(AgentError, match="name the groups or drop it"):
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
