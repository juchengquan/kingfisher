"""What the server writes down about what it did."""

from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient
from kingfisher_service import ServiceConfig, create_app
from test_server import AsyncStub, tokens

from kingfisher import Kingfisher
from tests.conftest import StubCheckpointer


def _claim(cfg, session_id):
    """One session's turn slot, inside the session it guards."""
    from kingfisher.infrastructure.workspace.sessions import claim_path

    return claim_path(cfg.workspace / "sessions" / session_id)


def lines(caplog):
    return [
        json.loads(r.getMessage())
        for r in caplog.records
        if r.name == "kingfisher.audit"
    ]


@pytest.fixture
def audited(cfg, caplog):
    """A client whose audit log is being read, with content off by default."""

    def build(**settings):
        service = Kingfisher(
            cfg, graph=AsyncStub("the answer", tokens=tokens(3)), threads=StubCheckpointer()
        )
        app = create_app(service, ServiceConfig(**settings))
        http = TestClient(app)
        http.kingfisher = service
        return http

    caplog.set_level(logging.INFO, logger="kingfisher.audit")
    return build


# -- the half nothing else records -----------------------------------------


def test_a_refusal_is_recorded(audited, caplog):
    """Measured before this existed: a refused request wrote nothing at all, anywhere."""
    http = audited()

    http.post("/sessions/" + "0" * 32 + "/turns", json={"task": "go"})

    (line,) = lines(caplog)
    assert line["event"] == "refused"
    assert line["reason"] == "unknown_session"
    assert line["status"] == 404
    assert line["session_id"] == "0" * 32


def test_the_reason_matches_what_the_caller_was_told(audited, caplog):
    """One code, both sides."""
    http = audited()
    session = http.post("/sessions", json={"agent": "only"}).json()["session_id"]
    _claim(http.kingfisher.cfg, session).mkdir(parents=True, exist_ok=True)

    response = http.post(f"/sessions/{session}/turns", json={"task": "go"})

    (line,) = lines(caplog)
    assert response.json()["error"] == line["reason"] == "session_busy"
    assert response.status_code == line["status"] == 409


REFUSALS = [
    ("unknown session", "/sessions/" + "0" * 32 + "/turns", {"task": "go"}),
    ("empty task", "/turns", {"task": "   "}),
]


@pytest.mark.parametrize("case", REFUSALS, ids=[row[0] for row in REFUSALS])
def test_the_audit_line_agrees_with_what_the_caller_was_told(audited, caplog, case):
    """The contract, and for more than one refusal.

    A live run caught the two drifting: the audit resolved status and code through
    the kingfisher error table alone, so an `HTTPException` carrying its own 422 was
    recorded as a 500 called "error" while the caller correctly got 422
    "invalid_request". A log that disagrees with the response is worse than no log,
    because it is believed. One function answers both now.
    """
    _, path, payload = case
    http = audited()
    caplog.clear()

    response = http.post(path, json=payload)

    (line,) = lines(caplog)
    assert line["status"] == response.status_code
    assert line["reason"] == response.json()["error"]


def test_a_body_fastapi_rejects_outright_is_not_audited(audited, caplog):
    """The boundary, stated rather than discovered."""
    http = audited()
    caplog.clear()

    response = http.post("/turns", json={"task": "go", "capabilities": {"tolls": []}})

    assert response.status_code == 422
    assert lines(caplog) == []


def test_a_deployment_error_is_audited_as_the_500_it_is(cfg, caplog):
    """Not in the error map, on purpose: no store wired is a deployment that has not
    decided where files come from.
    """
    caplog.set_level(logging.INFO, logger="kingfisher.audit")
    service = Kingfisher(cfg, graph=AsyncStub("ok"), threads=StubCheckpointer())
    caplog.clear()

    with TestClient(create_app(service), raise_server_exceptions=False) as http:
        response = http.post("/turns", json={"task": "go", "data_refs": ["x"]})

    (line,) = lines(caplog)
    assert response.status_code == line["status"] == 500
    assert line["reason"] == "error"
    assert line["detail"] == "MissingStoreError"


# -- turns -----------------------------------------------------------------


def test_a_turn_is_recorded_once_when_it_ends(audited, caplog):
    http = audited()
    session = http.post("/sessions", json={"agent": "only"}).json()["session_id"]
    caplog.clear()

    http.post(f"/sessions/{session}/turns", json={"task": "go"})

    (line,) = lines(caplog)
    assert line["event"] == "turn"
    assert line["outcome"] == "ok"
    assert line["session_id"] == session
    assert line["turn_id"] == "t001"
    assert isinstance(line["duration_ms"], float)


def test_a_one_shot_turn_records_the_session_it_was_given(audited, caplog):
    """`POST /turns` names no session, so the request has none to record -- but the turn
    is given one, and a line nobody can tie to a session is most of the value gone.
    """
    http = audited()

    http.post("/turns", json={"task": "go"})

    (line,) = lines(caplog)
    assert line["session_id"]
    assert http.kingfisher.session(line["session_id"]) is not None


def test_a_turn_whose_client_walked_away_says_so(cfg, caplog):
    """The outcome an operator can reconstruct from nowhere else."""
    import asyncio

    from test_server import hang_up_after

    caplog.set_level(logging.INFO, logger="kingfisher.audit")
    service = Kingfisher(
        cfg,
        graph=AsyncStub("done", tokens=tokens(200), pause=0.01),
        threads=StubCheckpointer(),
    )
    app = create_app(service)
    session = service.start_session()
    caplog.clear()

    asyncio.run(
        hang_up_after(app, f"/sessions/{session}/turns", {"task": "go"}, 3, lambda: True)
    )

    (line,) = lines(caplog)
    assert line["outcome"] == "stopped"
    assert line["session_id"] == session


# -- content is a decision somebody makes ----------------------------------


def test_the_task_and_answer_are_absent_by_default(audited, caplog):
    """What may be kept, and for how long, is about a deployment's obligations rather
    than about kingfisher.
    """
    http = audited()
    caplog.clear()

    http.post("/turns", json={"task": "something private"})

    (line,) = lines(caplog)
    assert "task" not in line
    assert "answer" not in line


def test_content_is_recorded_when_it_is_asked_for(audited, caplog):
    http = audited(audit_content=True)
    caplog.clear()

    http.post("/turns", json={"task": "something private"})

    (line,) = lines(caplog)
    assert line["task"] == "something private"
    assert line["answer"] == "the answer"


def test_content_is_off_unless_the_environment_says_otherwise():
    assert ServiceConfig().audit_content is False
    assert ServiceConfig.from_env({}).audit_content is False
    assert ServiceConfig.from_env({"KINGFISHER_SERVICE_AUDIT_CONTENT": "true"}).audit_content


# -- where it goes ---------------------------------------------------------


def test_the_audit_log_is_its_own_logger_with_no_handler():
    """Separate from `kingfisher_service` on purpose."""
    audit = logging.getLogger("kingfisher.audit")

    assert audit.name != "kingfisher_service"
    assert audit.handlers == []


def test_nothing_is_written_when_nobody_is_listening(cfg, caplog):
    """A deployment that never wires a handler pays a level check per turn and writes
    nothing.
    """
    service = Kingfisher(cfg, graph=AsyncStub("ok"), threads=StubCheckpointer())
    logging.getLogger("kingfisher.audit").setLevel(logging.WARNING)
    try:
        with TestClient(create_app(service)) as http, caplog.at_level(logging.INFO):
            http.post("/turns", json={"task": "go"})
        assert lines(caplog) == []
    finally:
        logging.getLogger("kingfisher.audit").setLevel(logging.NOTSET)
