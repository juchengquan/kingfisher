"""Four states on the wire, three in the lattice, and the one that has no name."""

from __future__ import annotations

import dataclasses

import pytest
from kingfisher_service.capabilities import CapabilitiesBody
from kingfisher_service.turns import TurnBody, turn_for

from kingfisher import Capabilities, Kingfisher
from tests.conftest import StubCheckpointer
from tests.unit.test_run import StubAgent

AXES = [field.name for field in dataclasses.fields(Capabilities)]
WIDE = [name for name in AXES if getattr(Capabilities(), name) == "*"]
NARROW = [name for name in AXES if getattr(Capabilities(), name) is None]


def asked(**body) -> Capabilities:
    return CapabilitiesBody(**body).selected()


# -- the four states -------------------------------------------------------


def test_an_absent_axis_takes_the_deployments_default():
    """The state with no spelling."""
    wrong = [axis for axis in AXES if getattr(asked(), axis) != getattr(Capabilities(), axis)]
    assert not wrong, f"{wrong} do not take the deployment's default when the wire omits them"


def test_a_null_axis_asks_for_nothing_on_it():
    """The state pydantic would have swallowed."""
    wrong = [axis for axis in AXES if getattr(asked(**{axis: None}), axis) is not None]
    assert not wrong, f"{wrong} do not read `null` as asking for nothing"


def test_a_star_axis_asks_for_everything_the_workspace_offers():
    wrong = [axis for axis in AXES if getattr(asked(**{axis: "*"}), axis) != "*"]
    assert not wrong, f"{wrong} do not read `\"*\"` as everything the workspace offers"


def test_a_list_axis_becomes_a_tuple_of_exactly_those_names():
    """Converted here rather than relying on `Capabilities.__post_init__`, whose
    leniency about lists is documented as a backstop: "a caller holding a list should
    convert at its own edge".
    """
    wrong = [axis for axis in AXES if getattr(asked(**{axis: ["a", "b"]}), axis) != ("a", "b")]
    assert not wrong, f"{wrong} do not convert a list to a tuple of exactly those names"


def test_null_and_absent_differ_on_every_wide_axis():
    """The whole reason this module exists."""
    same = [
        axis for axis in WIDE
        if getattr(asked(**{axis: None}), axis) == getattr(asked(), axis)
    ]
    assert not same, (
        f"{same} read `null` and absent alike, and they default to `\"*\"` — a caller "
        "asking for nothing on one of these would be handed everything"
    )


def test_null_and_absent_agree_on_the_narrow_axes():
    """Not a contradiction -- these default to nothing, so asking for nothing and saying
    nothing land in the same place.
    """
    differ = [
        axis for axis in NARROW
        if getattr(asked(**{axis: None}), axis) != getattr(asked(), axis)
    ]
    assert not differ, f"{differ} default to nothing, so `null` and absent must agree"


def test_the_three_axis_lists_are_not_empty():
    """Seven rules above walk these, and a rule walking nothing passes.

    `AXES` comes off the dataclass and `WIDE`/`NARROW` are filtered from it by what
    each axis defaults to, so a default changing shape is what would empty one --
    quietly, now that these are loops rather than one case per axis.
    """
    assert AXES, "no axes read off `Capabilities`"
    assert WIDE, "no axis defaults to `*`"
    assert NARROW, "no axis defaults to nothing"
    assert set(WIDE) | set(NARROW) == set(AXES), (
        f"{sorted(set(AXES) - set(WIDE) - set(NARROW))} default to neither `*` nor "
        "nothing, so the two rules below cover less than every axis"
    )


def test_an_empty_object_is_not_an_opinion_about_anything():
    """`{}` names no axis, so it is the same as omitting the field."""
    assert asked() == Capabilities()
    every_axis_null = CapabilitiesBody.model_validate(dict.fromkeys(AXES))
    assert every_axis_null.selected() != Capabilities()


# -- the wire model and the lattice must not drift -------------------------


def test_every_axis_of_the_lattice_is_on_the_wire():
    """A new axis on `Capabilities` that nobody added here would be a capability no
    caller could ever ask about, silently.
    """
    assert set(CapabilitiesBody.model_fields) == set(AXES)


def test_the_wire_model_declares_the_lattices_own_default():
    """The declared defaults are what the generated schema shows a client, so a wrong
    one is a lie in the docs rather than a bug in the behaviour -- the quieter
    failure of the two, and the reason it is checked.
    """
    wrong = [
        axis for axis in AXES
        if CapabilitiesBody.model_fields[axis].default != getattr(Capabilities(), axis)
    ]
    assert not wrong, f"{wrong} declare a default the lattice does not have"


def test_an_unknown_axis_is_refused_rather_than_ignored():
    """Answering 200 to a request to restrict something is the worst way to learn the
    field was misspelled.
    """
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
    """What bounds the cost of getting any of this wrong."""
    deployment = Capabilities(tools=("read_file",))
    service = Kingfisher(
        cfg, graph=StubAgent("ok"), threads=StubCheckpointer(), grants=deployment
    )
    reaching = turn_for(TurnBody(task="go", capabilities={"tools": ["read_file", "shell"]}))

    allowed = service.grants.intersect(reaching.capabilities)

    assert allowed.tools == ("read_file",)


def test_absent_follows_the_lattice_even_if_the_model_declares_otherwise():
    """What `model_fields_set` actually buys, and it is not obvious."""
    from kingfisher_service.capabilities import Axis

    class Drifted(CapabilitiesBody):
        tools: Axis = None  # the lattice says "*"

    assert Drifted().selected().tools == "*"
    assert Drifted(tools=None).selected().tools is None
