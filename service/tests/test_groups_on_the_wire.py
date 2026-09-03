"""The five routes under a vocabulary: who reaches what, and what they are told.

The through-line every assertion here checks: **the caller gets a code, the log
gets the reason.** A session out of reach answers exactly what a wrong id
answers, and a deployment whose identity provider has drifted from its
vocabulary says so without naming a single group.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
import yaml
from fastapi.testclient import TestClient
from kingfisher_service.app import create_app
from kingfisher_service.identity import from_header

from kingfisher import Kingfisher
from kingfisher.domain.access import parse

HEADER = "X-Kf-Groups"
AS_A = {HEADER: "A"}
AS_C = {HEADER: "C"}

VOCABULARY = "groups: [A, B, C]\n"

#: Reachable by A only, and holding a delegate that is narrower still.
NARROW = """name: narrow
description: An agent for A.
groups: [A]
system_prompt: |
  You do the task.
"""

#: Reachable by everyone, so both callers can open it -- which is what lets the
#: narrowing assertions be about the *report* rather than about the refusal.
SHARED = """name: shared
description: An agent for everyone.
subagents:
  - name: reviewer
    groups: [A]
system_prompt: |
  You do the task.
"""

REVIEWER = """name: reviewer
description: A delegate.
groups: [A]
system_prompt: |
  You check things.
"""


@pytest.fixture
def policied(cfg):
    agents = cfg.catalogue_roots["agents"]
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "narrow.yaml").write_text(NARROW, encoding="utf-8")
    (agents / "shared.yaml").write_text(SHARED, encoding="utf-8")
    delegates = cfg.catalogue_roots["subagents"]
    delegates.mkdir(parents=True, exist_ok=True)
    (delegates / "reviewer.yaml").write_text(REVIEWER, encoding="utf-8")
    return replace(cfg, access=parse(yaml.safe_load(VOCABULARY), source="groups.yaml"))


@pytest.fixture
def client(policied):
    app = create_app(kingfisher=Kingfisher(policied), groups_from=from_header(HEADER))
    return TestClient(app, raise_server_exceptions=False)


def opened(client, agent: str, headers: dict[str, str]) -> str:
    got = client.post("/sessions", json={"agent": agent}, headers=headers)
    assert got.status_code == 201, got.text
    return got.json()["session_id"]


# -- opening a session ------------------------------------------------------


def test_opening_a_session_on_an_unreachable_agent_is_refused(client):
    got = client.post("/sessions", json={"agent": "narrow"}, headers=AS_C)

    assert got.status_code == 403
    assert got.json()["error"] == "not_granted"


def test_that_refusal_does_not_name_the_agent_as_existing(client):
    """It is the library's `no agent named ...` -- the same words a typo gets,
    and the listing in it names only what this caller can open."""
    got = client.post("/sessions", json={"agent": "narrow"}, headers=AS_C)

    assert "narrow" not in got.json()["message"].split("offers")[1]


def test_opening_a_reachable_agent_works(client):
    assert client.post("/sessions", json={"agent": "narrow"}, headers=AS_A).status_code == 201


def test_the_open_response_narrows_what_it_reports(client):
    """The one moment a caller is told what they got must be true for *them*.
    `shared` declares `reviewer`, which only A reaches."""
    for_a = client.post("/sessions", json={"agent": "shared"}, headers=AS_A).json()
    for_c = client.post("/sessions", json={"agent": "shared"}, headers=AS_C).json()

    assert for_a["agent"]["subagents"] == ["reviewer"]
    assert for_c["agent"]["subagents"] == []


def test_the_open_response_echoes_the_groups_it_resolved_as(client):
    """A caller behind a gateway usually cannot see what identity was asserted
    for them; this is the one place to find out."""
    assert client.post("/sessions", json={"agent": "shared"}, headers=AS_C).json()[
        "groups"
    ] == ["C"]


# -- reading and deleting ---------------------------------------------------


def test_a_session_out_of_reach_reads_as_missing(client):
    """The assertion that matters: the same status *and* the same code a wrong
    id gets, so holding a real one teaches nothing."""
    session_id = opened(client, "narrow", AS_A)

    mine = client.get(f"/sessions/{session_id}", headers=AS_A)
    theirs = client.get(f"/sessions/{session_id}", headers=AS_C)
    nonsense = client.get("/sessions/deadbeefdeadbeef", headers=AS_C)

    assert mine.status_code == 200
    assert theirs.status_code == nonsense.status_code == 404
    assert theirs.json()["error"] == nonsense.json()["error"] == "unknown_session"


def test_deleting_a_session_out_of_reach_reads_as_missing(client):
    session_id = opened(client, "narrow", AS_A)

    assert client.delete(f"/sessions/{session_id}", headers=AS_C).status_code == 404
    # And it is still there for the caller who may have it.
    assert client.get(f"/sessions/{session_id}", headers=AS_A).status_code == 200


def test_deleting_a_session_in_reach_works(client):
    session_id = opened(client, "narrow", AS_A)

    assert client.delete(f"/sessions/{session_id}", headers=AS_A).status_code == 204
    assert client.get(f"/sessions/{session_id}", headers=AS_A).status_code == 404


# -- a deployment that cannot resolve a caller ------------------------------


def test_an_undeclared_group_is_a_misconfiguration(client):
    """The only way an AccessError reaches a live request once startup refuses
    the two mismatches: the identity provider and the vocabulary have drifted."""
    got = client.post("/sessions", json={"agent": "shared"}, headers={HEADER: "Q"})

    assert got.status_code == 500
    assert got.json()["error"] == "misconfigured"


def test_the_body_does_not_name_the_vocabulary(client):
    """The library's message names every group this deployment defines. It must
    not reach a caller -- that is the enumeration filtering exists to prevent."""
    got = client.post("/sessions", json={"agent": "shared"}, headers={HEADER: "Q"})

    for name in ("A", "B", "C"):
        assert name not in got.json()["message"]


def test_a_missing_header_is_the_same_misconfiguration(client):
    """A caller must not be able to tell a stripped header from an unknown
    group: both are the deployment's to fix and neither is theirs."""
    got = client.post("/sessions", json={"agent": "shared"})

    assert got.status_code == 500
    assert got.json()["error"] == "misconfigured"


def test_every_route_resolves_the_caller(client):
    """No route may be reachable without saying who is calling -- one forgotten
    is a hole the others cannot cover."""
    session_id = opened(client, "shared", AS_A)
    unscoped = (
        client.post("/sessions", json={"agent": "shared"}),
        client.get(f"/sessions/{session_id}"),
        client.delete(f"/sessions/{session_id}"),
        client.post(f"/sessions/{session_id}/turns", json={"task": "hi"}),
        client.post("/turns", json={"task": "hi", "agent": "shared"}),
    )

    assert [got.status_code for got in unscoped] == [500] * 5
