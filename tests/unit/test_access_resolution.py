"""Turning a caller's groups into the grant one turn runs under.

Pure: no file, no workspace, no agent. What is asserted here is the rule --
overlap grants, absence denies, and a group that contains others reaches
whatever they reach.
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from kingfisher.domain.access import UNSCOPED, Access, AccessError, Audience
from kingfisher.domain.capabilities import ALL, Capabilities


def policy(**entries: Mapping[str, Audience]) -> Access:
    """An `Access` over a flat A/B/C vocabulary, plus anything the entries name.

    A, B and C are always declared, including where no asset lists them: a
    caller in a group that reaches nothing is a case worth asserting, and the
    vocabulary being closed means such a group still has to exist. Inferring it
    from the entries alone would make "reaches nothing" indistinguishable from
    "is not a group", which is the distinction `expand` refuses over.
    """
    named = {
        g
        for audience in entries.values()
        for a in audience.values()
        if isinstance(a, tuple)
        for g in a
    }
    return Access(
        groups={name: (name,) for name in sorted({"A", "B", "C"} | named)},
        entries=dict(entries),
    )


def test_a_caller_reaches_an_asset_their_group_is_listed_on():
    access = policy(tools={"sql_query": ("A", "B")})
    assert access.resolve(["A"]).tools == ("sql_query",)


def test_any_one_group_is_enough():
    """Decision 8: the list is an OR, not an AND."""
    access = policy(tools={"sql_query": ("A", "B")})
    assert access.resolve(["B", "C"]).tools == ("sql_query",)


def test_a_caller_with_no_listed_group_reaches_nothing():
    access = policy(tools={"sql_query": ("A", "B")})
    assert access.resolve(["C"]).tools == ()


def test_an_unlisted_asset_reaches_nobody():
    """Decision 9, asserted the only way it can be: a caller holding *every*
    group still gets nothing but what the file names. `line_count` exists in
    the workspace and has no entry, so there is no group that would produce it.
    """
    access = policy(tools={"sql_query": ("A",)})
    assert access.resolve(["A", "B", "C"]).tools == ("sql_query",)


def test_a_star_audience_reaches_everyone():
    access = policy(tools={"http_fetch": ALL})
    assert access.resolve(["C"]).tools == ("http_fetch",)


def test_no_groups_at_all_reaches_nothing():
    """`for_groups([])` is a caller who holds nothing, and holds nothing here."""
    access = policy(tools={"http_fetch": ("A",)})
    assert access.resolve([]).tools == ()


def test_a_containing_group_reaches_what_it_contains():
    """Decision 10, and the reason it exists: `admin` is on no asset."""
    access = Access(
        groups={"A": ("A",), "B": ("B",), "admin": ("admin", "A", "B")},
        entries={"tools": {"sql_query": ("A",), "http_fetch": ("B",)}},
    )
    assert access.resolve(["admin"]).tools == ("sql_query", "http_fetch")


def test_an_unknown_group_is_refused_rather_than_ignored():
    """The vocabulary is closed, so a typo is a mistake and not an empty grant."""
    access = policy(tools={"sql_query": ("A",)})
    with pytest.raises(AccessError, match="unknown group"):
        access.resolve(["Q"])


def test_uncontrolled_axes_stay_wide_open():
    """Only three kinds are controlled; the rest must be the identity for
    `intersect`, or resolving would silently revoke what the deployment granted.
    """
    resolved = policy(tools={"sql_query": ("A",)}).resolve(["A"])
    assert resolved.builtin_tools == ALL
    assert resolved.skills == ALL
    assert resolved.middleware == ALL
    assert resolved.endpoints == ALL
    assert resolved.models == ALL
    assert resolved.memory is None


def test_resolving_can_only_narrow_the_deployments_grant():
    """The composition the service performs, asserted as a property."""
    deployment = Capabilities(tools=("sql_query",))
    access = policy(tools={"sql_query": ("A",), "http_fetch": ("A",)})
    assert deployment.intersect(access.resolve(["A"])).tools == ("sql_query",)


def test_unscoped_is_a_sentinel_and_not_a_group_name():
    """It must not be mistakable for a list of groups."""
    assert UNSCOPED is not None
    assert not isinstance(UNSCOPED, str | tuple | list)


def test_subagents_narrow_on_their_own_axis():
    """The second controlled kind, and the one your compounding case runs on."""
    access = policy(
        subagents={"reviewer": ("A", "B", "C"), "extractor": ("A",)},
        tools={"http_fetch": ("B",)},
    )
    resolved = access.resolve(["B"])
    assert resolved.subagents == ("reviewer",)
    assert resolved.tools == ("http_fetch",)
