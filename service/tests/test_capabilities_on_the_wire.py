"""Four states on the wire, three in the lattice, and the one that has no name.

`Selection` is `"*"`, a list, or `None`. JSON adds *absent*, which means "this
deployment's default" -- and on five of the eight axes that is the opposite end
of the lattice from `null`. Pydantic collapses the two unless something records
what the request actually carried, so this module exercises all four on every
axis rather than trusting that reading.

The direction of the mistake is not uniform, which is why it cannot be checked
by eye: `builtin_tools`, `tools`, `skills`, `middleware` and `endpoints` default
to `"*"`, so collapsing absent into null fails closed; `subagents`, `models` and
`memory` default to `None`, so the same collapse hands the default back to a
caller who explicitly asked for nothing.
"""

from __future__ import annotations

import dataclasses

import pytest
from kingfisher_service.capabilities import CapabilitiesBody
from kingfisher_service.turns import TurnBody, turn_for

from kingfisher import Capabilities, Kingfisher
from tests.conftest import StubCheckpointer
from tests.test_run import StubAgent

AXES = [field.name for field in dataclasses.fields(Capabilities)]
WIDE = [name for name in AXES if getattr(Capabilities(), name) == "*"]
NARROW = [name for name in AXES if getattr(Capabilities(), name) is None]


def asked(**body) -> Capabilities:
    return CapabilitiesBody(**body).selected()


# -- the four states -------------------------------------------------------


@pytest.mark.parametrize("axis", AXES)
def test_an_absent_axis_takes_the_deployments_default(axis):
    """The state with no spelling. Omitting an axis is not an opinion about it,
    so whatever the lattice defaults to is what applies."""
    assert getattr(asked(), axis) == getattr(Capabilities(), axis)


@pytest.mark.parametrize("axis", AXES)
def test_a_null_axis_asks_for_nothing_on_it(axis):
    """The state pydantic would have swallowed. On the five wide axes this is
    the opposite of absent, and a collapse there hands back everything the
    deployment allows to a caller who asked for none of it."""
    assert getattr(asked(**{axis: None}), axis) is None


@pytest.mark.parametrize("axis", AXES)
def test_a_star_axis_asks_for_everything_the_workspace_offers(axis):
    assert getattr(asked(**{axis: "*"}), axis) == "*"


@pytest.mark.parametrize("axis", AXES)
def test_a_list_axis_becomes_a_tuple_of_exactly_those_names(axis):
    """Converted here rather than relying on `Capabilities.__post_init__`, whose
    leniency about lists is documented as a backstop: "a caller holding a list
    should convert at its own edge". This is that edge."""
    assert getattr(asked(**{axis: ["a", "b"]}), axis) == ("a", "b")


@pytest.mark.parametrize("axis", WIDE)
def test_null_and_absent_differ_on_every_wide_axis(axis):
    """The whole reason this module exists, stated once per axis it applies to."""
    assert getattr(asked(**{axis: None}), axis) != getattr(asked(), axis)


@pytest.mark.parametrize("axis", NARROW)
def test_null_and_absent_agree_on_the_narrow_axes(axis):
    """Not a contradiction -- these default to nothing, so asking for nothing
    and saying nothing land in the same place. Worth pinning because it is the
    reason a collapse looks harmless if you only try one axis."""
    assert getattr(asked(**{axis: None}), axis) == getattr(asked(), axis)


def test_an_empty_object_is_not_an_opinion_about_anything():
    """`{}` names no axis, so it is the same as omitting the field. Distinct
    from an object that names every axis as null, which asks for nothing."""
    assert asked() == Capabilities()
    every_axis_null = CapabilitiesBody.model_validate(dict.fromkeys(AXES))
    assert every_axis_null.selected() != Capabilities()


# -- the wire model and the lattice must not drift -------------------------


def test_every_axis_of_the_lattice_is_on_the_wire():
    """A new axis on `Capabilities` that nobody added here would be a capability
    no caller could ever ask about, silently."""
    assert set(CapabilitiesBody.model_fields) == set(AXES)


@pytest.mark.parametrize("axis", AXES)
def test_the_wire_model_declares_the_lattices_own_default(axis):
    """The declared defaults are what the generated schema shows a client, so a
    wrong one is a lie in the docs rather than a bug in the behaviour -- the
    quieter failure of the two, and the reason it is checked."""
    assert CapabilitiesBody.model_fields[axis].default == getattr(Capabilities(), axis)


def test_an_unknown_axis_is_refused_rather_than_ignored():
    """Answering 200 to a request to restrict something is the worst way to
    learn the field was misspelled."""
    with pytest.raises(ValueError, match="tolls"):
        CapabilitiesBody.model_validate({"tolls": ["read_file"]})


# -- what reaches the library ----------------------------------------------


def test_a_request_carries_what_was_asked_for(cfg):
    body = TurnBody(task="go", capabilities={"tools": ["http_fetch"], "skills": None})

    request = turn_for(body)

    assert request.capabilities.tools == ("http_fetch",)
    assert request.capabilities.skills is None
    assert request.capabilities.builtin_tools == "*"


def test_a_request_without_capabilities_carries_the_default(cfg):
    assert turn_for(TurnBody(task="go")).capabilities == Capabilities()


def test_a_caller_can_only_narrow_within_what_the_deployment_granted(cfg):
    """What bounds the cost of getting any of this wrong. The clamp is
    `grants.intersect(request.capabilities)`, so the worst a mistake here does is
    give a caller more than *they* asked for -- never more than the deployment
    allowed."""
    deployment = Capabilities(tools=("read_file",))
    service = Kingfisher(
        cfg, graph=StubAgent("ok"), threads=StubCheckpointer(), grants=deployment
    )
    reaching = turn_for(TurnBody(task="go", capabilities={"tools": ["read_file", "shell"]}))

    allowed = service.grants.intersect(reaching.capabilities)

    assert allowed.tools == ("read_file",)


def test_absent_follows_the_lattice_even_if_the_model_declares_otherwise():
    """What `model_fields_set` actually buys, and it is not obvious.

    The declared defaults above match the lattice, so passing every axis every
    time would produce the same answer -- a mutation that does exactly that
    changes nothing. What `model_fields_set` buys is that the two are allowed to
    drift *without the behaviour drifting*: a wrong declared default becomes a
    lie in the generated schema rather than a capability quietly granted or
    withheld. This is that claim, with a deliberately wrong model.
    """
    from kingfisher_service.capabilities import Axis

    class Drifted(CapabilitiesBody):
        tools: Axis = None  # the lattice says "*"

    assert Drifted().selected().tools == "*"
    assert Drifted(tools=None).selected().tools is None
