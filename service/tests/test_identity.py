"""Who is calling: the shipped header reader, and the two startup refusals.

Nothing here runs a turn. What is under test is the seam between a request and
a group list -- the one place this server decides what identity means, and the
one place a deployment can get it wrong in a way that would be silent.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
import yaml
from fastapi.testclient import TestClient
from kingfisher_service.app import create_app
from kingfisher_service.identity import MissingGroupsError, from_header
from starlette.datastructures import Headers

from kingfisher import Kingfisher
from kingfisher.domain.access import parse
from tests.conftest import an_agent

HEADER = "X-Kf-Groups"


class _Request:
    """Just the headers, which is all a source is given of a request."""

    def __init__(self, **headers: str) -> None:
        self.headers = Headers({k.replace("_", "-"): v for k, v in headers.items()})


@pytest.fixture
def policied(cfg):
    """A deployment with a vocabulary and one agent."""
    an_agent(cfg, "assistant", groups="[A]")
    return replace(cfg, access=parse(yaml.safe_load("groups: [A, B]\n"), source="groups.yaml"))


# -- the shipped reader -----------------------------------------------------


def test_a_header_is_read_as_the_groups_it_names():
    assert from_header(HEADER)(_Request(x_kf_groups="A,B")) == ("A", "B")


def test_whitespace_around_names_is_ignored():
    assert from_header(HEADER)(_Request(x_kf_groups=" A , B ")) == ("A", "B")


def test_one_name_needs_no_comma():
    assert from_header(HEADER)(_Request(x_kf_groups="A")) == ("A",)


def test_the_header_name_is_matched_case_insensitively():
    """HTTP says field names are case-insensitive, and a gateway will not spell
    it the way the argument did."""
    assert from_header("x-kf-groups")(_Request(X_Kf_Groups="A")) == ("A",)


def test_an_absent_header_refuses_naming_it():
    """A gateway that should set it and did not is broken, and that must not
    look like a caller who reaches nothing -- read as 'no groups' the two are
    identical, for every user at once, and the fixes are in different places."""
    with pytest.raises(MissingGroupsError, match=HEADER):
        from_header(HEADER)(_Request())


def test_an_empty_header_refuses_the_same_way():
    with pytest.raises(MissingGroupsError, match=HEADER):
        from_header(HEADER)(_Request(x_kf_groups="   "))


def test_a_header_of_only_separators_refuses_too():
    with pytest.raises(MissingGroupsError, match=HEADER):
        from_header(HEADER)(_Request(x_kf_groups=" , , "))


def test_the_refusal_says_the_header_must_be_stripped_inbound():
    """The whole security of this arrangement is a thing this code cannot check,
    so the one place it is mentioned is where somebody is already reading."""
    with pytest.raises(MissingGroupsError, match="strip it from inbound"):
        from_header(HEADER)(_Request())


def test_the_reader_names_no_default_header():
    """`from_header()` must not be callable bare: a default header name would
    make trusting one the thing that happens when nobody decides."""
    with pytest.raises(TypeError):
        from_header()  # type: ignore[call-arg]


# -- the two startup refusals -----------------------------------------------


def test_a_vocabulary_with_no_source_refuses_to_start(policied):
    """Without this it is not merely unsupported: every route 500s, which is a
    deployment up and serving nothing."""
    with pytest.raises(RuntimeError, match="groups_from"):
        create_app(kingfisher=Kingfisher(policied))


def test_a_source_with_no_vocabulary_refuses_to_start(cfg):
    """The converse, and the more dangerous one: a deployment somebody believes
    is locked down and is not."""
    with pytest.raises(RuntimeError, match="no access policy"):
        create_app(kingfisher=Kingfisher(cfg), groups_from=from_header(HEADER))


def test_a_vocabulary_with_a_source_starts(policied):
    assert create_app(kingfisher=Kingfisher(policied), groups_from=from_header(HEADER))


def test_neither_starts_exactly_as_it_did(cfg):
    """Every deployment that predates this must be untouched."""
    with TestClient(create_app(kingfisher=Kingfisher(cfg))) as client:
        assert client.get("/sessions/nope").status_code == 404


def test_the_refusal_names_both_halves(policied):
    """A message that said only 'misconfigured' would leave the reader to work
    out which of the two files to open."""
    with pytest.raises(RuntimeError) as raised:
        create_app(kingfisher=Kingfisher(policied))

    said = str(raised.value)
    assert "groups_from" in said
    assert "create_app" in said
