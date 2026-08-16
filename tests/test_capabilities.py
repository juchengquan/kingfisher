"""The Capabilities value object."""

from __future__ import annotations

import pytest

from kingfisher.domain.capabilities import (
    ALL,
    UNRESTRICTED,
    Capabilities,
    CapabilityError,
    all_but,
    approved_middleware,
    withheld,
)


def test_the_two_ends_are_all_and_none():
    """`ALL` and `None` are opposite ends, and the default is one of them --
    per field, because `subagents` starts at the other."""
    assert Capabilities().tools == ALL
    assert Capabilities().subagents is None  # a delegate is wired when asked for
    assert Capabilities().is_unrestricted

    none_at_all = Capabilities(tools=None)
    assert none_at_all.tools is None
    assert not none_at_all.is_unrestricted

    empty = Capabilities(tools=())
    assert empty.tools == ()  # still a whitelist, just an empty one


def test_names_are_de_duplicated_but_keep_their_order():
    caps = Capabilities(tools=("read_file", "glob", "read_file"))
    assert caps.tools == ("read_file", "glob")


def test_intersect_never_widens():
    """A caller cannot escalate by asking for more than it was granted."""
    granted = Capabilities(tools=("read_file", "glob"))
    asked = Capabilities(tools=("read_file", "execute"))

    narrowed = granted.intersect(asked).tools
    assert narrowed == ("read_file",)
    # execute was requested and is simply absent, rather than an error
    assert narrowed is not None and "execute" not in narrowed


def test_unrestricted_on_either_side_defers_to_the_other():
    """`ALL` is the identity of narrowing, so the other side wins."""
    granted = Capabilities(tools=("read_file",))

    assert UNRESTRICTED.intersect(granted).tools == ("read_file",)
    assert granted.intersect(UNRESTRICTED).tools == ("read_file",)
    assert UNRESTRICTED.intersect(UNRESTRICTED).tools == ALL


def test_intersect_of_an_empty_grant_yields_nothing():
    """A caller granted no tools gets none, whatever it asks for."""
    assert Capabilities(tools=()).intersect(Capabilities(tools=("execute",))).tools == ()


def test_intersect_handles_each_dimension_independently():
    granted = Capabilities(tools=("read_file",), skills=("tabular-qa",), subagents=ALL)
    asked = Capabilities(tools=("read_file", "execute"), subagents=("reviewer",))

    narrowed = granted.intersect(asked)
    assert narrowed.tools == ("read_file",)
    assert narrowed.skills == ("tabular-qa",)  # the request named nothing, so ALL
    assert narrowed.subagents == ("reviewer",)  # the grant named nothing, so ALL


def test_unknown_reports_what_the_workspace_cannot_offer():
    """So a request fails loudly rather than running with quietly less."""
    caps = Capabilities(tools=("read_file", "teleport"), skills=("nope",))

    missing = caps.unknown(tools=["read_file"], skills=["tabular-qa"], subagents=[])
    assert set(missing) == {"tool:teleport", "skill:nope"}


def test_unknown_ignores_dimensions_with_no_opinion():
    assert Capabilities().unknown(tools=[], skills=[], subagents=[]) == ()


def test_capabilities_are_hashable_and_comparable():
    """Value object semantics: two identical grants are the same grant.

    The list is deliberately off-contract -- the declared field type is a
    tuple, and this pins the normalisation that lets a service hand us a
    freshly deserialised JSON array anyway.
    """
    assert Capabilities(tools=("a",)) == Capabilities(tools=["a"])  # ty: ignore[invalid-argument-type]
    assert len({Capabilities(tools=("a",)), Capabilities(tools=("a",))}) == 1


def test_memory_is_a_switch_not_a_selection():
    """It is one file, mounted or not -- there are no names to choose between.
    `None` still means "no opinion", so the default stays free."""
    assert Capabilities().memory is None
    assert Capabilities().is_unrestricted
    assert not Capabilities(memory=False).is_unrestricted
    assert not Capabilities(memory=True).is_unrestricted


def test_a_refusal_of_memory_wins_from_either_side():
    """`False` is the only value that subtracts, which is what makes this
    narrowing rather than negotiation."""
    granted = Capabilities(memory=False)
    assert granted.intersect(Capabilities(memory=True)).memory is False
    assert Capabilities(memory=True).intersect(granted).memory is False

    # And no opinion still defers to the side that has one.
    assert Capabilities().intersect(Capabilities(memory=True)).memory is True
    assert Capabilities(memory=True).intersect(Capabilities()).memory is True
    assert Capabilities().intersect(Capabilities()).memory is None


# -- middleware a definition may have -------------------------------------
#
# The rule used to live in `infrastructure.delegation`, mixed in with the code
# that instantiates the objects. Only the instantiation needed to be there:
# `Capabilities.middleware` and `SubagentSpec.middleware` are both name lists,
# so deciding *which names* is expressible here, and these tests reach it
# without a config, an agent, or deepagents.


def test_declaring_no_middleware_approves_nothing():
    assert approved_middleware(None, registered=(), granted=None, subject="x") == ()
    assert approved_middleware((), registered=("audit",), granted=None, subject="x") == ()


def test_registered_and_granted_names_go_through():
    approved = approved_middleware(
        ("audit",), registered=("audit", "ratelimit"), granted=("audit",), subject="x"
    )

    assert approved == ("audit",)


def test_a_name_nothing_registered_is_a_mistake_in_the_definition():
    """It names something that does not exist, so it cannot be honoured and
    must not be ignored."""
    with pytest.raises(CapabilityError, match="unregistered middleware: ghost"):
        approved_middleware(("ghost",), registered=("audit",), granted=None, subject="subagent 'r'")


def test_a_registered_name_that_was_not_granted_is_refused():
    """Not the "caller was narrower" case that quietly drops a skill: running
    with silently less middleware than the definition asked for could mean
    running without the audit hook it was written to have."""
    with pytest.raises(CapabilityError, match="may not use: ratelimit"):
        approved_middleware(
            ("ratelimit",),
            registered=("audit", "ratelimit"),
            granted=("audit",),
            subject="subagent 'r'",
        )


def test_granting_everything_lets_a_declaration_through():
    """`ALL` is unrestricted, exactly as everywhere else in this module."""
    approved = approved_middleware(
        ("audit",), registered=("audit",), granted=ALL, subject="x"
    )

    assert approved == ("audit",)


def test_granting_nothing_refuses_a_declaration():
    """`None` is the other end, and it permits nothing at all."""
    with pytest.raises(CapabilityError, match="may not use: audit"):
        approved_middleware(("audit",), registered=("audit",), granted=None, subject="x")


def test_an_empty_grant_permits_nothing():
    """`()` and `None` agree here; `ALL` is what differs."""
    with pytest.raises(CapabilityError, match="may not use: audit"):
        approved_middleware(("audit",), registered=("audit",), granted=(), subject="x")


def test_the_refusal_names_the_subject_it_was_asked_about():
    """The message reaches an operator who has to find the definition."""
    with pytest.raises(CapabilityError, match=r"subagent 'reviewer' names unregistered"):
        approved_middleware(
            ("ghost",), registered=(), granted=None, subject="subagent 'reviewer'"
        )


# -- what a grant leaves out ----------------------------------------------
#
# A grant is a whitelist, so it can only mean *less* than the workspace holds,
# and it said so nowhere. Measured before this existed: a caller wrote
# "everything except execute" as the other nine names, someone later added a
# tool, and it was refused with nothing said.


def test_an_unrestricted_grant_withholds_nothing():
    """`ALL` cannot go stale: it names nothing to go stale."""
    assert withheld(ALL, offered=("a", "b")) == ()


def test_a_grant_of_nothing_withholds_everything():
    assert withheld(None, offered=("a", "b")) == ("a", "b")


def test_it_names_what_the_workspace_has_and_the_grant_does_not():
    assert withheld(("a",), offered=("a", "b", "c")) == ("b", "c")


def test_a_grant_of_everything_withholds_nothing():
    assert withheld(("a", "b"), offered=("a", "b")) == ()


def test_an_empty_grant_withholds_all_of_it():
    """`()` is the opposite of `None` here, as everywhere else in this module."""
    assert withheld((), offered=("a", "b")) == ("a", "b")


def test_a_grant_naming_something_gone_reports_only_what_is_there():
    """The mirror of `unknown`, and deliberately silent about that case: a name
    that does not exist is refused at build, which is a louder answer."""
    assert withheld(("a", "vanished"), offered=("a", "b")) == ("b",)


def test_a_grant_written_today_goes_stale_when_a_tool_arrives():
    """The case that motivated this. The grant does not change; what it means
    does."""
    offered_today = ("execute", "ls", "read_file")
    grant = tuple(n for n in offered_today if n != "execute")  # "everything but the shell"

    assert withheld(grant, offered=offered_today) == ("execute",)

    offered_tomorrow = (*offered_today, "publish_report")
    assert withheld(grant, offered=offered_tomorrow) == ("execute", "publish_report")


# -- writing a grant by subtraction ---------------------------------------
#
# A whitelist cannot say "not the shell", only the other eleven names. This
# resolves the subtraction against what is offered *now*, so what gets stored
# and enforced is still a whitelist -- a stored subtraction would be a deny-list
# and would let tomorrow's new tool through by default.


def test_it_is_everything_offered_but_the_named():
    assert all_but(("b",), offered=("a", "b", "c")) == ("a", "c")


def test_excluding_nothing_grants_the_lot():
    assert all_but((), offered=("a", "b")) == ("a", "b")


def test_excluding_everything_grants_none_of_it():
    """`()` again, arrived at from the other direction."""
    assert all_but(("a", "b"), offered=("a", "b")) == ()


def test_a_name_that_excludes_nothing_is_refused():
    """`--without-tools exec` is a typo that would otherwise grant everything
    quietly, which is the mirror of what `Capabilities.unknown` refuses."""
    with pytest.raises(CapabilityError, match="cannot exclude unknown name"):
        all_but(("exec",), offered=("execute", "ls"))


def test_the_refusal_names_what_is_actually_on_offer():
    with pytest.raises(CapabilityError, match=r"offers \('execute', 'ls'\)"):
        all_but(("exec",), offered=("ls", "execute"))


def test_it_is_the_same_difference_withheld_computes():
    """One rule, two directions: `withheld` turns a grant into what it left out,
    `all_but` turns what to leave out into a grant."""
    offered = ("a", "b", "c")

    assert all_but(("b",), offered=offered) == withheld(("b",), offered=offered)


def test_the_result_is_an_ordinary_grant_that_narrowing_still_clamps():
    """What it produces is a whitelist like any other, so a deployment's grant
    still caps it -- subtraction is a way to write one, not a way past one."""
    asked = Capabilities(tools=all_but(("execute",), offered=("execute", "ls", "read_file")))
    granted = Capabilities(tools=("ls",))

    assert granted.intersect(asked).tools == ("ls",)
