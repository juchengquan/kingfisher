"""What tools exist to be granted, and the rules over that.

The rule these cover had two implementations -- one in `agent` for a request,
one in `delegation` for a subagent -- with an identical loop over the same
five-tuple and different wording. `delegation` said so itself: "`_refuse_unknown_tools`
says the same thing to a request, for the same reason."
"""

from __future__ import annotations

import pytest

from kingfisher.domain.capabilities import ALL, CapabilityError, ceiling
from kingfisher.tools.spec import Found, Offering

WORKSPACE = Offering(
    builtin=("read_file", "execute"),
    workspace=("http_fetch", "csv_columns"),
    sources={"http_fetch": "http_fetch.py", "csv_columns": "csv_profile/"},
)


# -- one refusal, two subjects ---------------------------------------------


def test_a_name_on_the_wrong_axis_is_not_called_unknown():
    """The mistake the split creates. `read_file` plainly exists, and telling
    someone it is unknown sends them hunting for a bug in kingfisher."""
    with pytest.raises(CapabilityError) as raised:
        WORKSPACE.refuse_unknown(ALL, ("read_file",), subject="this request")

    message = str(raised.value)
    assert "unknown" not in message
    assert "builtin_tools" in message


@pytest.mark.parametrize(
    "subject", ["this request", "subagent 'reviewer'"], ids=["request", "subagent"]
)
def test_both_callers_get_the_same_two_refusals(subject):
    """One implementation, and the caller names itself -- the shape
    `refuse_ungranted_models` already uses a file away."""
    with pytest.raises(CapabilityError, match="unknown tool"):
        WORKSPACE.refuse_unknown(ALL, ("nope",), subject=subject)

    with pytest.raises(CapabilityError, match="builtin_tools"):
        WORKSPACE.refuse_unknown(ALL, ("execute",), subject=subject)


@pytest.mark.parametrize("asked", [("nope",), ("execute",)], ids=["unknown", "misplaced"])
def test_the_refusal_names_the_subject_that_made_it(asked):
    """Two lines in one log, from one rule, and a reader can tell which is which.

    Both refusals, not one: the subject was dropped from the misplaced branch by
    a mutation and the unknown-branch test did not notice, which is what a
    single-path test buys you.
    """
    with pytest.raises(CapabilityError, match="subagent 'reviewer'"):
        WORKSPACE.refuse_unknown(ALL, asked, subject="subagent 'reviewer'")

    with pytest.raises(CapabilityError, match="this request"):
        WORKSPACE.refuse_unknown(ALL, asked, subject="this request")


def test_an_unknown_name_is_told_where_the_real_ones_live():
    """Nesting exists so a person can find a file again, and a bare list of
    names is exactly what sends them grepping for it.

    Without a package's trailing slash, which this asserted until a `tools:`
    entry could carry a path. The listing and a definition now spell a source
    the same way, so what is printed here is what gets pasted there -- and
    `csv_profile/` would be a near-miss someone has to notice.
    """
    with pytest.raises(CapabilityError) as raised:
        WORKSPACE.refuse_unknown(ALL, ("csv_colums",), subject="this request")

    message = str(raised.value)
    assert "csv_columns" in message
    assert "(csv_profile)" in message


def test_naming_nothing_cannot_name_something_wrong():
    """`ALL` and `None` are the two ends of the lattice; neither picks a name,
    so neither can pick a wrong one."""
    WORKSPACE.refuse_unknown(ALL, ALL, subject="this request")
    WORKSPACE.refuse_unknown(None, None, subject="this request")


# -- what a request may call -----------------------------------------------


def test_narrowing_one_axis_does_not_cost_the_other():
    """The whole point of the split. `tools=("http_fetch",)` no longer costs a
    caller `read_file`."""
    permitted = WORKSPACE.permitted(ALL, ("http_fetch",))

    assert set(permitted or ()) == {"read_file", "execute", "http_fetch"}


def test_narrowing_neither_axis_means_no_allowlist_at_all():
    """Not an empty one, and the difference is what `ToolAllowlist` enforces."""
    assert WORKSPACE.permitted(ALL, ALL) is None
    assert WORKSPACE.permitted(ALL, ()) == ("read_file", "execute")


def test_a_grant_cannot_reach_past_what_is_offered():
    assert WORKSPACE.permitted((), ("http_fetch", "not_here")) == ("http_fetch",)


# -- what a delegate may call ----------------------------------------------


def test_a_delegate_is_narrowed_by_the_grant_not_by_the_workspace():
    """The mistake this nearly shipped as an `Offering` method.

    A delegate is bounded by what the *request was granted*, not by what the
    workspace offers -- and the two differ exactly when a request narrowed
    something, which is the case the ceiling exists for. Narrowing against the
    offering would hand a delegate back the tool its caller withheld.
    """
    allowed = ceiling(
        ALL,
        ALL,
        granted_builtin=("read_file",),
        granted_tools=(),
        subject="subagent 'x'",
    )

    assert allowed == ("read_file",)
    assert "execute" not in (allowed or ())


def test_a_delegate_nobody_narrowed_gets_no_allowlist():
    """`ALL` here where `permitted` answers `None`: a delegate's selection is
    narrowed again downstream, a request's goes to a middleware."""
    assert ceiling(ALL, ALL, granted_builtin=ALL, granted_tools=ALL, subject="x") == ALL


def test_one_axis_left_unresolved_is_refused_rather_than_guessed():
    """`ALL` is the string `"*"`. Unpacked into the union it contributes a tool
    *named* `*` and drops the axis it stood for."""
    with pytest.raises(ValueError, match="one tool axis resolved"):
        ceiling(ALL, ("a",), granted_builtin=ALL, granted_tools=("a",), subject="x")


# -- building one ----------------------------------------------------------


def test_an_offering_is_built_from_what_the_repository_found():
    class FakeTool:
        name = "http_fetch"

    offering = Offering.of(
        [Found(tool=FakeTool(), source="net/http_fetch.py")], builtin=("read_file",)
    )

    assert offering.workspace == ("http_fetch",)
    assert offering.sources == {"http_fetch": "net/http_fetch.py"}
    assert offering.builtin == ("read_file",)
