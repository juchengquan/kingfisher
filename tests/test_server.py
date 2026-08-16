"""The HTTP surface over sessions.

Every test here drives a real app over real HTTP through `TestClient`, around a
`Kingfisher` built with a stub agent. That is the whole reason `create_app`
takes an instance: the substitution point is the one the rest of this suite
already uses, so nothing here needs to patch construction.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from kingfisher import Kingfisher
from kingfisher.server import ServerConfig, create_app
from tests.conftest import StubCheckpointer
from tests.test_run import StubAgent


@pytest.fixture
def client(cfg):
    service = Kingfisher(cfg, agent=StubAgent("ok"), threads=StubCheckpointer())
    with TestClient(create_app(service)) as http:
        http.kingfisher = service
        yield http


# -- sessions --------------------------------------------------------------


def test_opening_a_session_returns_an_id_the_library_can_see(client):
    """The two halves agreeing is the point: an id minted over HTTP is a
    session, not a token the server invented and holds somewhere."""
    response = client.post("/sessions")

    assert response.status_code == 201
    session_id = response.json()["session_id"]
    assert client.kingfisher.session(session_id) is not None


def test_a_request_cannot_choose_a_session_id(client):
    """T2, at the edge. A supplied id may resume but never create, so there is
    no field for one here -- a service forwarding an id from its own caller
    would otherwise let that caller choose, or guess, somebody else's."""
    response = client.post("/sessions", json={"session_id": "chosen"})

    assert response.status_code == 201
    assert response.json()["session_id"] != "chosen"
    assert client.kingfisher.session("chosen") is None


def test_reading_a_session_reports_its_id_and_when_it_was_used(client):
    session_id = client.post("/sessions").json()["session_id"]

    body = client.get(f"/sessions/{session_id}").json()

    assert body["session_id"] == session_id
    assert isinstance(body["last_used"], float)


def test_what_comes_back_names_no_path(client):
    """A caller handed a directory would start reading files out of it, and the
    layout would become a contract nobody wrote down."""
    session_id = client.post("/sessions").json()["session_id"]

    body = client.get(f"/sessions/{session_id}").json()

    assert set(body) == {"session_id", "last_used"}


def test_reading_a_session_that_does_not_exist_is_a_404(client):
    assert client.get("/sessions/" + "0" * 32).status_code == 404


def test_reading_a_session_does_not_disturb_it(client, cfg):
    """No claim taken, and the idle clock retention reads is not refreshed --
    otherwise a service checking an id would keep sessions alive by asking
    about them."""
    import os
    import time

    session_id = client.post("/sessions").json()["session_id"]
    directory = cfg.workspace / "sessions" / session_id
    stale = time.time() - 10_000
    os.utime(directory, (stale, stale))

    assert client.get(f"/sessions/{session_id}").status_code == 200

    assert directory.stat().st_mtime == pytest.approx(stale, abs=1)
    assert not (cfg.state_dir / "claims" / session_id).exists()


def test_deleting_a_session_removes_it(client):
    session_id = client.post("/sessions").json()["session_id"]

    assert client.delete(f"/sessions/{session_id}").status_code == 204

    assert client.get(f"/sessions/{session_id}").status_code == 404
    assert client.kingfisher.session(session_id) is None


def test_deleting_a_session_that_does_not_exist_is_a_404(client):
    """`delete_session` answers `None` both for "no such session" and for
    "removed it", so existence is checked first -- the library's return cannot
    tell a 404 from a 204."""
    assert client.delete("/sessions/" + "0" * 32).status_code == 404


def test_there_is_no_way_to_list_sessions(client):
    """A session id is a bearer credential, so a collection endpoint hands out
    every credential on the box. Whatever knows whose sessions are whose calls
    `sessions()` in-process."""
    client.post("/sessions")

    assert client.get("/sessions").status_code in (404, 405)


# -- what the server is configured with ------------------------------------


def test_the_server_binds_loopback_unless_told_otherwise():
    """Not a placeholder. This server does not know who is calling, so a
    default of `0.0.0.0` publishes an unauthenticated API the moment anyone
    runs it."""
    assert ServerConfig().host == "127.0.0.1"


def test_server_settings_come_from_their_own_prefix():
    """A prefix of its own, so reading a deployment's environment says which
    half of the split each setting belongs to."""
    settings = ServerConfig.from_env(
        {"KINGFISHER_SERVER_HOST": "0.0.0.0", "KINGFISHER_SERVER_PORT": "9001"}  # noqa: S104
    )

    assert (settings.host, settings.port) == ("0.0.0.0", 9001)  # noqa: S104


def test_a_body_over_the_limit_is_refused_without_being_read(client, cfg):
    """`task` is unbounded text. The limit is not tidiness -- it is the
    difference between a bad request and a process holding a gigabyte of it."""
    service = Kingfisher(cfg, agent=StubAgent("ok"), threads=StubCheckpointer())
    app = create_app(service, ServerConfig(max_body_bytes=64))

    with TestClient(app) as http:
        response = http.post("/sessions", content=b"x" * 128)

    assert response.status_code == 413
    assert response.json()["error"] == "body_too_large"


def test_the_app_serves_the_instance_it_was_given(cfg):
    """The substitution point. An app that built its own would push these tests
    toward patching `create_deep_agent`, which this repo forbids."""
    service = Kingfisher(cfg, agent=StubAgent("ok"), threads=StubCheckpointer())
    app = create_app(service)

    with TestClient(app) as http:
        session_id = http.post("/sessions").json()["session_id"]

    assert app.state.kingfisher is service
    assert service.session(session_id) is not None


def test_a_filesystem_endpoint_does_not_stall_every_other_request(cfg, monkeypatch):
    """Why the session handlers are `def` and not `async def`.

    `session` is a directory listing -- 0.24ms for fifty sessions, 22ms for five
    thousand -- and on the loop that is time during which nothing else
    progresses. fastapi runs a sync endpoint on a worker thread, so three
    overlap in about the time of one. The margin is deliberately wide: the
    claim is "not serialised", not a number.
    """
    import asyncio
    import time

    import httpx

    service = Kingfisher(cfg, agent=StubAgent("ok"), threads=StubCheckpointer())
    session_id = service.start_session()
    delay = 0.15
    real = Kingfisher.session

    def slow(self, wanted):
        time.sleep(delay)
        return real(self, wanted)

    monkeypatch.setattr(Kingfisher, "session", slow)
    app = create_app(service)

    async def three_at_once():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://server") as http:
            started = time.perf_counter()
            replies = await asyncio.gather(
                *(http.get(f"/sessions/{session_id}") for _ in range(3))
            )
            return time.perf_counter() - started, replies

    elapsed, replies = asyncio.run(three_at_once())

    assert all(reply.status_code == 200 for reply in replies)
    assert elapsed < delay * 3, f"{elapsed:.2f}s for three — serialised, not overlapped"
