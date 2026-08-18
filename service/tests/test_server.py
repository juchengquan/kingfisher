"""The HTTP surface over sessions.

Every test here drives a real app over real HTTP through `TestClient`, around a
`Kingfisher` built with a stub agent. That is the whole reason `create_app`
takes an instance: the substitution point is the one the rest of this suite
already uses, so nothing here needs to patch construction.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient
from kingfisher_service import ServiceConfig, create_app

from kingfisher import Kingfisher, Request
from tests.conftest import StubCheckpointer
from tests.test_run import StubAgent


@pytest.fixture
def client(cfg):
    service = Kingfisher(cfg, graph=StubAgent("ok"), threads=StubCheckpointer())
    with TestClient(create_app(service)) as http:
        http.kingfisher = service
        yield http


# -- sessions --------------------------------------------------------------


def test_opening_a_session_returns_an_id_the_library_can_see(client):
    """The two halves agreeing is the point: an id minted over HTTP is a
    session, not a token the server invented and holds somewhere."""
    response = client.post("/sessions", json={"agent": "only"})

    assert response.status_code == 201
    session_id = response.json()["session_id"]
    assert client.kingfisher.session(session_id) is not None


def test_a_request_cannot_choose_a_session_id(client):
    """T2, at the edge. A supplied id may resume but never create, so there is
    no field for one here -- a service forwarding an id from its own caller
    would otherwise let that caller choose, or guess, somebody else's.

    Refused rather than ignored, which is stronger than it used to be: this body
    forbids what it does not define, so a caller who believed the field worked
    is told it does not, instead of getting a 201 and a different id than the one
    they think they hold.
    """
    response = client.post("/sessions", json={"agent": "only", "session_id": "chosen"})

    assert response.status_code == 422
    assert client.kingfisher.session("chosen") is None


def test_reading_a_session_reports_its_id_and_when_it_was_used(client):
    session_id = client.post("/sessions", json={"agent": "only"}).json()["session_id"]

    body = client.get(f"/sessions/{session_id}").json()

    assert body["session_id"] == session_id
    assert isinstance(body["last_used"], float)


def test_what_comes_back_names_no_path(client):
    """A caller handed a directory would start reading files out of it, and the
    layout would become a contract nobody wrote down."""
    session_id = client.post("/sessions", json={"agent": "only"}).json()["session_id"]

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

    session_id = client.post("/sessions", json={"agent": "only"}).json()["session_id"]
    directory = cfg.workspace / "sessions" / session_id
    stale = time.time() - 10_000
    os.utime(directory, (stale, stale))

    assert client.get(f"/sessions/{session_id}").status_code == 200

    assert directory.stat().st_mtime == pytest.approx(stale, abs=1)
    assert not (cfg.state_dir / "claims" / session_id).exists()


def test_deleting_a_session_removes_it(client):
    session_id = client.post("/sessions", json={"agent": "only"}).json()["session_id"]

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
    client.post("/sessions", json={"agent": "only"})

    assert client.get("/sessions").status_code in (404, 405)


# -- what the server is configured with ------------------------------------


def test_the_server_binds_loopback_unless_told_otherwise():
    """Not a placeholder. This server does not know who is calling, so a
    default of `0.0.0.0` publishes an unauthenticated API the moment anyone
    runs it."""
    assert ServiceConfig().host == "127.0.0.1"


def test_server_settings_come_from_their_own_prefix():
    """A prefix of its own, so reading a deployment's environment says which
    half of the split each setting belongs to."""
    settings = ServiceConfig.from_env(
        {"KINGFISHER_SERVICE_HOST": "0.0.0.0", "KINGFISHER_SERVICE_PORT": "9001"}  # noqa: S104
    )

    assert (settings.host, settings.port) == ("0.0.0.0", 9001)  # noqa: S104


def test_a_body_over_the_limit_is_refused_without_being_read(client, cfg):
    """`task` is unbounded text. The limit is not tidiness -- it is the
    difference between a bad request and a process holding a gigabyte of it."""
    service = Kingfisher(cfg, graph=StubAgent("ok"), threads=StubCheckpointer())
    app = create_app(service, ServiceConfig(max_body_bytes=64))

    with TestClient(app) as http:
        response = http.post("/sessions", content=b"x" * 128)

    assert response.status_code == 413
    assert response.json()["error"] == "body_too_large"


def test_the_app_serves_the_instance_it_was_given(cfg):
    """The substitution point. An app that built its own would push these tests
    toward patching `create_deep_agent`, which this repo forbids."""
    service = Kingfisher(cfg, graph=StubAgent("ok"), threads=StubCheckpointer())
    app = create_app(service)

    with TestClient(app) as http:
        session_id = http.post("/sessions", json={"agent": "only"}).json()["session_id"]

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

    service = Kingfisher(cfg, graph=StubAgent("ok"), threads=StubCheckpointer())
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


# -- turns -----------------------------------------------------------------


class AsyncStub(StubAgent):
    """A graph that answers on `astream`, optionally slowly."""

    def __init__(self, answer, *, tokens=None, pause=0.0):
        super().__init__(answer, tokens=tokens)
        self.pause = pause

    async def astream(self, state, config, stream_mode=None, subgraphs=False):
        import asyncio

        for chunk in self.stream(state, config, stream_mode):
            await asyncio.sleep(self.pause)
            yield chunk


def tokens(count):
    from langchain_core.messages import AIMessageChunk

    return [(AIMessageChunk(content=f"t{n}"), {}) for n in range(count)]


def frames(text):
    """Parse an SSE body into (event, data) pairs, ignoring comments."""
    import json

    out = []
    for block in text.split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        name = data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = json.loads(line.removeprefix("data: "))
        out.append((name, data))
    return out


def serving(cfg, agent, **settings):
    service = Kingfisher(cfg, graph=agent, threads=StubCheckpointer())
    return service, create_app(service, ServiceConfig(**settings))


def test_a_turn_streams_its_events_as_named_sse(cfg):
    """The kind is the event name, so a consumer subscribes to what it wants
    rather than parsing every body to find out what it got."""
    service, app = serving(cfg, AsyncStub("done", tokens=tokens(3)))
    session_id = service.start_session()

    with TestClient(app) as http:
        body = http.post(f"/sessions/{session_id}/turns", json={"task": "go"}).text

    names = [name for name, _ in frames(body)]
    assert names[0] == "run_start"
    assert names[-1] == "finished"
    assert names.count("token") == 3


def test_every_event_name_is_one_the_package_declares(cfg):
    """The wire contract and the package's vocabulary are the same list."""
    from kingfisher.domain.result import KINDS

    service, app = serving(cfg, AsyncStub("done", tokens=tokens(2)))
    session_id = service.start_session()

    with TestClient(app) as http:
        body = http.post(f"/sessions/{session_id}/turns", json={"task": "go"}).text

    assert {name for name, _ in frames(body)} <= set(KINDS)


def test_the_finished_event_carries_the_answer_and_no_host_path(cfg):
    """`run_dir` and `log_path` are the host's filesystem layout. A remote
    caller cannot read them and should not be told them; `virtual_dir` is the
    machine-independent name for the same directory."""
    service, app = serving(cfg, AsyncStub("the answer"))
    session_id = service.start_session()

    with TestClient(app) as http:
        body = http.post(f"/sessions/{session_id}/turns", json={"task": "go"}).text

    result = dict(frames(body))["finished"]["result"]
    assert result["answer"] == "the answer"
    assert result["session_id"] == session_id
    assert result["virtual_dir"] == f"/runs/{result['turn_id']}"
    assert "run_dir" not in result
    assert "log_path" not in result


def test_a_token_frame_carries_text_and_nothing_else(cfg):
    """Defaults are omitted rather than sent as nulls. Tokens are the bulk of a
    turn's bytes, and seven null fields each is a cost paid thousands of times
    for a uniformity nobody consumes."""
    service, app = serving(cfg, AsyncStub("done", tokens=tokens(1)))
    session_id = service.start_session()

    with TestClient(app) as http:
        body = http.post(f"/sessions/{session_id}/turns", json={"task": "go"}).text

    token = next(data for name, data in frames(body) if name == "token")
    assert set(token) == {"text"}


# -- refusals happen before the response starts ----------------------------


def test_an_unknown_session_is_a_404_and_not_a_stream(cfg):
    """The rule the whole endpoint is arranged around. `astream` runs `_prepare`
    before yielding, so a refusal is still a status code at that moment -- and
    handing the generator to `StreamingResponse` unopened would put 200 on the
    wire and bury it in the body."""
    _, app = serving(cfg, AsyncStub("done"))

    with TestClient(app) as http:
        response = http.post("/sessions/" + "0" * 32 + "/turns", json={"task": "go"})

    assert response.status_code == 404
    assert response.json()["error"] == "unknown_session"
    assert "text/event-stream" not in response.headers.get("content-type", "")


def test_a_second_turn_on_a_busy_session_is_a_409(cfg):
    """Refused rather than queued: a queue hides a wait as long as whatever the
    other turn is doing, and a caller who did not know they were racing learns
    nothing from it."""
    service, app = serving(cfg, AsyncStub("done"))
    session_id = service.start_session()
    (cfg.state_dir / "claims" / session_id).mkdir(parents=True, exist_ok=True)

    with TestClient(app) as http:
        response = http.post(f"/sessions/{session_id}/turns", json={"task": "go"})

    assert response.status_code == 409
    assert response.json()["error"] == "session_busy"


def test_an_empty_task_is_refused_by_validation(cfg):
    """422 from the model rather than 500 from `Request.__post_init__`, which
    raises a bare `ValueError` that no error map should be catching."""
    _, app = serving(cfg, AsyncStub("done"))

    with TestClient(app) as http:
        assert http.post("/turns", json={"task": "  "}).status_code in (400, 422)


def test_a_refused_turn_leaves_no_claim_behind(cfg):
    """The stream is closed on the refusal path too. A claim taken and not
    given back would make the session look busy to retention as well as to the
    next caller."""
    service, app = serving(cfg, AsyncStub("done"))
    session_id = service.start_session()

    with TestClient(app) as http:
        http.post(f"/sessions/{session_id}/turns", json={"task": "go"})

    assert not (cfg.state_dir / "claims" / session_id).exists()


# -- the one-shot ----------------------------------------------------------


def test_a_one_shot_turn_mints_a_session_and_names_it(cfg):
    """Omitting the session is something the library can do and the path form
    cannot express. The id comes back on `finished`, so a caller who decides to
    continue can."""
    service, app = serving(cfg, AsyncStub("done"))

    with TestClient(app) as http:
        body = http.post("/turns", json={"task": "go"}).text

    result = dict(frames(body))["finished"]["result"]
    assert service.session(result["session_id"]) is not None


def test_a_one_shot_turn_does_not_take_a_session_id(cfg):
    """A supplied id may resume but never create. If the body could name one,
    a service forwarding its caller's input would let that caller pick -- or
    guess -- somebody else's session."""
    service, app = serving(cfg, AsyncStub("done"))

    with TestClient(app) as http:
        body = http.post("/turns", json={"task": "go", "session_id": "chosen"}).text

    result = dict(frames(body))["finished"]["result"]
    assert result["session_id"] != "chosen"
    assert service.session("chosen") is None


def test_the_api_does_not_accept_a_turn_id(cfg):
    """It would read as an idempotency key. The library's `turn_id` reuses the
    directory and then runs the turn again in full, so a client retrying a
    dropped request would double both the conversation and the bill."""
    _, app = serving(cfg, AsyncStub("done"))

    with TestClient(app) as http:
        body = http.post("/turns", json={"task": "go", "turn_id": "mine"}).text

    assert dict(frames(body))["finished"]["result"]["turn_id"] != "mine"


# -- hanging up ------------------------------------------------------------


def test_a_quiet_stream_sends_a_heartbeat(cfg):
    """Two jobs, and the second is the one that matters. Proxies drop idle
    connections -- that is the obvious one. But a hangup is only noticed when
    the server next tries to send, so this is what bounds how long a departed
    client keeps paying for model calls during a quiet tool call.

    An SSE comment, so every client ignores it by spec and it never becomes a
    kind consumers have to know about.
    """
    service, app = serving(
        cfg, AsyncStub("done", tokens=tokens(2), pause=0.05), heartbeat_s=0.01
    )
    session_id = service.start_session()

    with TestClient(app) as http:
        body = http.post(f"/sessions/{session_id}/turns", json={"task": "go"}).text

    assert ": ping" in body
    assert [name for name, _ in frames(body)][-1] == "finished"


def test_a_heartbeat_does_not_restart_the_work_it_is_waiting_on(cfg):
    """The pending `__anext__` is kept across pings rather than re-issued.
    Restarting it would abandon a model call in flight every interval, which is
    the opposite of what a keepalive is for -- and would show up as tokens
    going missing."""
    service, app = serving(
        cfg, AsyncStub("done", tokens=tokens(5), pause=0.03), heartbeat_s=0.01
    )
    session_id = service.start_session()

    with TestClient(app) as http:
        body = http.post(f"/sessions/{session_id}/turns", json={"task": "go"}).text

    assert [name for name, _ in frames(body)].count("token") == 5


async def hang_up_after(app, path, payload, chunks, when):
    """Drive the ASGI app directly and disconnect mid-stream.

    Not through a client, because httpx's `ASGITransport` buffers the whole
    response before yielding a line -- measured at 2.3s to the first line of a
    2s stream -- so no HTTP-level test can observe a turn while it is running.
    Speaking ASGI is also the honest level: this is exactly the `http.disconnect`
    a real server delivers.
    """
    import json

    body = json.dumps(payload).encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "client": ("127.0.0.1", 1234),
        "server": ("server", 80),
        "headers": [
            (b"host", b"server"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
    }
    gone = asyncio.Event()
    state: dict[str, Any] = {"asked": False, "seen": 0, "observed": None}

    async def receive():
        if not state["asked"]:
            state["asked"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            state["seen"] += 1
            if state["seen"] == chunks:
                state["observed"] = when()
                gone.set()

    await app(scope, receive, send)
    return state


def test_hanging_up_stops_the_turn_and_gives_the_claim_back(cfg):
    """The decision the whole design rests on, and it needs no library change.

    A client that walks away stops the work rather than leaving the session
    locked until the staleness window expires. It is also why there is no cancel
    endpoint: disconnect already is one.

    Getting here found a real defect. The generator's `finally` called `aclose()`
    while a `__anext__` was still in flight, which raises "asynchronous generator
    is already running" -- so the claim was never given back. Cancelling the
    pending task is the way in, and awaiting that cancellation is what makes the
    stop have happened rather than be scheduled.
    """
    service, app = serving(cfg, AsyncStub("done", tokens=tokens(200), pause=0.01))
    session_id = service.start_session()
    claim = cfg.state_dir / "claims" / session_id

    state = asyncio.run(
        hang_up_after(
            app, f"/sessions/{session_id}/turns", {"task": "go"}, 3, claim.exists
        )
    )

    assert state["observed"], "the claim should be held while the turn is running"
    assert state["seen"] < 200, "the turn should have stopped, not run to the end"
    assert not claim.exists(), "hanging up should give the claim back"
    assert service.run(Request("again", session_id=session_id)).turn_id


def test_an_event_with_nothing_to_say_sends_an_empty_body(cfg):
    """Defaults are omitted rather than sent as nulls. Tokens are the bulk of a
    turn's bytes, and seven null fields each is a cost paid thousands of times
    per turn for a uniformity nobody consumes.

    Tested on the function rather than through a stream, because every kind a
    real run emits happens to carry `text` -- so a stream cannot tell "omitted"
    from "present and non-empty".
    """
    from kingfisher_service.payloads import event_payload

    from kingfisher import RunEvent

    assert event_payload(RunEvent(kind="run_start")) == {}
    assert event_payload(RunEvent(kind="token", text="hi")) == {"text": "hi"}
    assert event_payload(RunEvent(kind="token", text="hi", channel="answer")) == {"text": "hi"}


def test_a_delegate_is_named_so_its_prose_can_be_told_apart(cfg):
    """Without it a delegate's tokens and the caller's arrive on one channel and
    the type cannot separate them -- both are chunks."""
    from kingfisher_service.payloads import event_payload

    from kingfisher import RunEvent

    assert event_payload(RunEvent(kind="token", text="x", agent="reviewer")) == {
        "text": "x",
        "agent": "reviewer",
    }


# -- every refusal looks the same ------------------------------------------
#
# There were four shapes: the turn path's, fastapi's `{"detail": ...}` from
# `HTTPException`, fastapi's list-of-objects from request validation, and a
# hand-written string in the body-size middleware. A client that must recognise
# four shapes to find out what went wrong will parse one and break on the rest.


REFUSALS = [
    ("unknown session", "GET", "/sessions/" + "0" * 32, None, 404, "unknown_session"),
    ("delete unknown session", "DELETE", "/sessions/" + "0" * 32, None, 404, "unknown_session"),
    ("turn on unknown session", "POST", "/sessions/" + "0" * 32 + "/turns",
     {"task": "go"}, 404, "unknown_session"),
    ("empty task", "POST", "/turns", {"task": "   "}, 422, "invalid_request"),
    ("missing task", "POST", "/turns", {}, 422, "invalid_request"),
    ("no such route", "GET", "/nope", None, 404, "not_found"),
    ("wrong method", "GET", "/turns", None, 405, "method_not_allowed"),
]


@pytest.mark.parametrize("case", REFUSALS, ids=[row[0] for row in REFUSALS])
def test_every_refusal_has_the_same_shape(client, case):
    """One shape, whoever refused: kingfisher, fastapi's router, or validation.

    `error` is the contract and `message` is prose. A client branches on the
    first and shows the second; parsing the message is how a client breaks when
    the wording improves.
    """
    _, method, path, payload, expected_status, expected_code = case

    response = client.request(method, path, json=payload)

    assert response.status_code == expected_status
    body = response.json()
    assert body["error"] == expected_code
    assert isinstance(body["message"], str)
    assert body["message"]
    assert "detail" not in body or isinstance(body["detail"], list)


def test_a_busy_session_refuses_in_the_same_shape(client, cfg):
    """The turn path raises rather than building a response, so it arrives at
    the same handler as everything else."""
    session_id = client.post("/sessions", json={"agent": "only"}).json()["session_id"]
    (cfg.state_dir / "claims" / session_id).mkdir(parents=True, exist_ok=True)

    response = client.post(f"/sessions/{session_id}/turns", json={"task": "go"})

    assert response.status_code == 409
    assert response.json()["error"] == "session_busy"
    assert response.json()["message"]


def test_an_oversize_body_refuses_in_the_same_shape(cfg):
    """The middleware used to hand-write its JSON, which made it the fourth
    shape. It carries `limit` as an extra rather than every refusal carrying a
    field that is usually null."""
    service = Kingfisher(cfg, graph=StubAgent("ok"), threads=StubCheckpointer())
    app = create_app(service, ServiceConfig(max_body_bytes=64))

    with TestClient(app) as http:
        body = http.post("/sessions", content=b"x" * 128).json()

    assert body["error"] == "body_too_large"
    assert body["message"]
    assert body["limit"] == 64


def test_two_refusals_with_one_status_still_say_which_is_which(client):
    """`unknown_session` and a mistyped URL are both 404 and need different
    fixes. A status alone is not something a client can branch on."""
    unknown = client.get("/sessions/" + "0" * 32).json()["error"]
    mistyped = client.get("/nope").json()["error"]

    assert unknown == "unknown_session"
    assert mistyped == "not_found"
    assert unknown != mistyped


def test_a_bug_is_not_dressed_up_as_a_refusal(cfg, monkeypatch):
    """An unmapped exception must not acquire an `error` code on the way out.
    A 500 that looks like a refusal is one a client retries forever.

    Asserted on the response rather than on the raise. Starlette re-raises after
    a server-error handler runs, so `pytest.raises` passes whether or not the
    handler dressed the bug up first -- which it did, when this was written that
    way and a mutation broadening the registration went unnoticed.
    """
    boom = "something is wrong here"

    def explode(self, session_id):
        raise RuntimeError(boom)

    monkeypatch.setattr(Kingfisher, "session", explode)
    service = Kingfisher(cfg, graph=StubAgent("ok"), threads=StubCheckpointer())

    with TestClient(create_app(service), raise_server_exceptions=False) as http:
        response = http.get("/sessions/anything")

    assert response.status_code == 500
    assert "error" not in response.text or boom not in response.text


# -- capabilities over the wire --------------------------------------------


def test_a_capabilities_object_travels_with_the_turn(cfg):
    """That the nested object parses and reaches the library. Which axes end up
    where is exercised in `test_capabilities_on_the_wire`, against `turn_for`,
    because a *narrowing* request cannot complete here -- see below."""
    service, app = serving(cfg, AsyncStub("done"))
    session_id = service.start_session()

    with TestClient(app) as http:
        response = http.post(
            f"/sessions/{session_id}/turns",
            json={"task": "go", "capabilities": {"builtin_tools": "*", "skills": "*"}},
        )

    assert response.status_code == 200
    assert dict(frames(response.text))["finished"]["result"]["session_id"] == session_id


def test_narrowing_against_an_injected_agent_is_a_deployment_error(cfg):
    """Not a caller-facing refusal, and deliberately not in the error map.

    `Kingfisher(graph=...)` returns that graph as-is, so restrictions the
    request asks for were never applied to it -- the library refuses rather than
    pretending. A deployment that wants per-request capabilities must let
    kingfisher build the agent. It is a 500 because it is the deployment that is
    wrong, not the caller, and the caller can do nothing about it.
    """
    service, app = serving(cfg, AsyncStub("done"))
    session_id = service.start_session()

    with TestClient(app, raise_server_exceptions=False) as http:
        response = http.post(
            f"/sessions/{session_id}/turns",
            json={"task": "go", "capabilities": {"tools": ["http_fetch"]}},
        )

    assert response.status_code == 500
    assert "error" not in response.text


def test_an_unknown_capability_axis_is_a_422_in_the_usual_shape(cfg):
    """Misspelling an axis must not be a 200. Answering success to a request to
    restrict something is the worst way to learn the field was ignored."""
    service, app = serving(cfg, AsyncStub("done"))
    session_id = service.start_session()

    with TestClient(app) as http:
        response = http.post(
            f"/sessions/{session_id}/turns",
            json={"task": "go", "capabilities": {"tolls": ["http_fetch"]}},
        )

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"
    assert "tolls" in str(response.json()["detail"])


# -- files by reference ----------------------------------------------------


def test_a_turn_can_bring_files_by_reference(cfg, tmp_path):
    """The remote form of `--data`. A caller with no host paths names an id and
    the deployment's store turns it into content."""
    from kingfisher import LocalFileStore

    store = tmp_path / "store"
    store.mkdir()
    (store / "sales.csv").write_bytes(b"a,b\n1,2\n")
    service = Kingfisher(
        cfg,
        graph=AsyncStub("done"),
        threads=StubCheckpointer(),
        files=LocalFileStore(store),
    )
    app = create_app(service)
    session_id = service.start_session()

    with TestClient(app) as http:
        response = http.post(
            f"/sessions/{session_id}/turns",
            json={"task": "go", "data_refs": ["sales.csv"]},
        )

    assert response.status_code == 200
    landed = cfg.workspace / "sessions" / session_id / "data" / "sales.csv"
    assert landed.read_bytes() == b"a,b\n1,2\n"


def test_a_reference_that_climbs_out_is_refused_in_the_usual_shape(cfg, tmp_path):
    """A ref is whatever a caller wrote, so this is the request most worth
    getting right. Distinct from `unknown_reference`: one is a typo, this one
    reads as an attempt."""
    from kingfisher import LocalFileStore

    store = tmp_path / "store"
    store.mkdir()
    (tmp_path / "secret").write_bytes(b"not yours")
    service = Kingfisher(
        cfg,
        graph=AsyncStub("done"),
        threads=StubCheckpointer(),
        files=LocalFileStore(store),
    )
    session_id = service.start_session()

    with TestClient(create_app(service)) as http:
        response = http.post(
            f"/sessions/{session_id}/turns",
            json={"task": "go", "data_refs": ["../secret"]},
        )

    assert response.status_code == 400
    assert response.json()["error"] == "unsafe_reference"


def test_a_reference_nobody_has_is_a_400_not_a_500(cfg, tmp_path):
    from kingfisher import LocalFileStore

    store = tmp_path / "store"
    store.mkdir()
    service = Kingfisher(
        cfg,
        graph=AsyncStub("done"),
        threads=StubCheckpointer(),
        files=LocalFileStore(store),
    )
    session_id = service.start_session()

    with TestClient(create_app(service)) as http:
        response = http.post(
            f"/sessions/{session_id}/turns",
            json={"task": "go", "input_refs": ["nope.txt"]},
        )

    assert response.status_code == 400
    assert response.json()["error"] == "unknown_reference"


def test_references_without_a_wired_store_are_the_deployments_problem(cfg):
    """A 500, and deliberately not in the error map: the deployment has not said
    where files come from, and nothing the caller sends can fix that."""
    service, app = serving(cfg, AsyncStub("done"))
    session_id = service.start_session()

    with TestClient(app, raise_server_exceptions=False) as http:
        response = http.post(
            f"/sessions/{session_id}/turns",
            json={"task": "go", "data_refs": ["anything"]},
        )

    assert response.status_code == 500


def test_opening_a_session_requires_an_agent(client):
    """No default and no implicit one. The agent decides where every prompt in
    the session goes and what it costs, so a body without one is a 422 rather
    than a guess."""
    assert client.post("/sessions", json={}).status_code == 422


def test_opening_a_session_says_what_it_resolved(client):
    """The one moment a caller can be told what they got without running a turn.

    The agent is resolved and pinned right here, so reporting it costs nothing
    and answers the question a caller would otherwise have to infer from a
    turn's behaviour.
    """
    body = client.post("/sessions", json={"agent": "only"}).json()

    assert body["agent"]["name"] == "only"
    assert "description" in body["agent"]


def test_an_unknown_agent_is_refused_before_a_session_exists(client):
    """Nothing is created for a request that cannot be served. A session left
    behind by a refused open is one more thing for retention to reap, and one
    more id a caller holds and cannot use."""
    response = client.post("/sessions", json={"agent": "nobody"})

    assert response.status_code >= 400
    assert client.kingfisher.sessions() == ()
