"""The Capabilities value object."""

from __future__ import annotations

from kingfisher.domain.capabilities import UNRESTRICTED, Capabilities


def test_unset_means_everything_and_empty_means_none():
    """The distinction the whole default rests on: omitting a field is not the
    same as asking for none of it."""
    assert Capabilities().tools is None
    assert Capabilities().is_unrestricted

    none_at_all = Capabilities(tools=())
    assert none_at_all.tools == ()
    assert not none_at_all.is_unrestricted


def test_names_are_de_duplicated_but_keep_their_order():
    caps = Capabilities(tools=["read_file", "glob", "read_file"])
    assert caps.tools == ("read_file", "glob")


def test_intersect_never_widens():
    """A caller cannot escalate by asking for more than it was granted."""
    granted = Capabilities(tools=("read_file", "glob"))
    asked = Capabilities(tools=("read_file", "execute"))

    assert granted.intersect(asked).tools == ("read_file",)
    # execute was requested and is simply absent, rather than an error
    assert "execute" not in granted.intersect(asked).tools


def test_unrestricted_on_either_side_defers_to_the_other():
    """Unrestricted means 'no opinion', not 'everything wins'."""
    granted = Capabilities(tools=("read_file",))

    assert UNRESTRICTED.intersect(granted).tools == ("read_file",)
    assert granted.intersect(UNRESTRICTED).tools == ("read_file",)
    assert UNRESTRICTED.intersect(UNRESTRICTED).is_unrestricted


def test_intersect_of_an_empty_grant_yields_nothing():
    """A caller granted no tools gets none, whatever it asks for."""
    assert Capabilities(tools=()).intersect(Capabilities(tools=("execute",))).tools == ()


def test_intersect_handles_each_dimension_independently():
    granted = Capabilities(tools=("read_file",), skills=("tabular-qa",))
    asked = Capabilities(tools=("read_file", "execute"), subagents=("reviewer",))

    narrowed = granted.intersect(asked)
    assert narrowed.tools == ("read_file",)
    assert narrowed.skills == ("tabular-qa",)  # asked had no opinion
    assert narrowed.subagents == ("reviewer",)  # granted had no opinion


def test_unknown_reports_what_the_workspace_cannot_offer():
    """So a request fails loudly rather than running with quietly less."""
    caps = Capabilities(tools=("read_file", "teleport"), skills=("nope",))

    missing = caps.unknown(tools=["read_file"], skills=["tabular-qa"], subagents=[])
    assert set(missing) == {"tool:teleport", "skill:nope"}


def test_unknown_ignores_dimensions_with_no_opinion():
    assert Capabilities().unknown(tools=[], skills=[], subagents=[]) == ()


def test_capabilities_are_hashable_and_comparable():
    """Value object semantics: two identical grants are the same grant."""
    assert Capabilities(tools=("a",)) == Capabilities(tools=["a"])
    assert len({Capabilities(tools=("a",)), Capabilities(tools=("a",))}) == 1
