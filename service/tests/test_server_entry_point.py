"""Running the server, and what it writes down while it runs.

The access log is the part with a decision in it. A session id is a bearer
credential and it is in the path of four of the five routes, so an ordinary
access log writes credentials to disk and to whatever collects logs from it.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient
from kingfisher_service import ServiceConfig, create_app
from kingfisher_service.__main__ import main, serve
from test_server import AsyncStub

from kingfisher import Kingfisher
from tests.conftest import StubCheckpointer


@pytest.fixture
def client(cfg):
    service = Kingfisher(cfg, graph=AsyncStub("ok"), threads=StubCheckpointer())
    with TestClient(create_app(service)) as http:
        http.kingfisher = service
        yield http


# -- the access log --------------------------------------------------------


def test_a_request_is_logged_once_with_its_route_and_status(client, caplog):
    with caplog.at_level(logging.INFO, logger="kingfisher_service"):
        client.post("/sessions", json={"agent": "only"})

    (line,) = [r.getMessage() for r in caplog.records if r.name == "kingfisher_service"]
    assert line.startswith("POST /sessions 201")
    assert line.endswith("ms")


def test_the_session_id_is_not_written_to_the_log(client, caplog):
    """The decision this module exists for. A session id is how a caller proves
    a session is theirs, so logging one puts a credential somewhere it is read
    by more people than the request was, and keeps it there."""
    session_id = client.post("/sessions", json={"agent": "only"}).json()["session_id"]

    with caplog.at_level(logging.INFO, logger="kingfisher_service"):
        client.get(f"/sessions/{session_id}")

    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert session_id not in logged
    assert "/sessions/{session_id}" in logged


def test_a_turn_logs_its_route_template_not_its_path(client, caplog):
    session_id = client.post("/sessions", json={"agent": "only"}).json()["session_id"]

    with caplog.at_level(logging.INFO, logger="kingfisher_service"):
        client.post(f"/sessions/{session_id}/turns", json={"task": "go"})

    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "/sessions/{session_id}/turns" in logged
    assert session_id not in logged


def test_a_request_that_matched_nothing_logs_no_path_at_all(client, caplog):
    """The case where falling back to the real path would be worst: a caller
    probing for routes controls exactly what gets written."""
    with caplog.at_level(logging.INFO, logger="kingfisher_service"):
        client.get("/sessions/secret-looking-thing/nope")

    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "secret-looking-thing" not in logged
    assert "<unmatched>" in logged


def test_a_refusal_is_logged_with_its_status(client, caplog):
    with caplog.at_level(logging.INFO, logger="kingfisher_service"):
        client.get("/sessions/" + "0" * 32)

    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "404" in logged


# -- the entry point -------------------------------------------------------


def test_serving_uses_the_configured_address(monkeypatch):
    """One place decides the address. An entry point that read the environment
    its own way would be a second way to configure the same thing."""
    import uvicorn

    seen = {}

    def fake_run(app, **kwargs):
        seen.update(kwargs)
        seen["app"] = app

    monkeypatch.setattr(uvicorn, "run", fake_run)

    serve(ServiceConfig(host="0.0.0.0", port=9123))  # noqa: S104

    assert (seen["host"], seen["port"]) == ("0.0.0.0", 9123)  # noqa: S104
    assert seen["app"].title == "kingfisher"


def test_the_entry_point_reads_the_environment(monkeypatch):
    import uvicorn

    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw))
    monkeypatch.setenv("KINGFISHER_SERVICE_PORT", "9124")

    assert main() == 0
    assert seen["port"] == 9124


def test_a_missing_extra_is_a_message_rather_than_a_traceback(monkeypatch, capsys):
    """The script is installed whether or not the extra is, because a command
    that exists and says what to install beats one that is silently absent."""
    import builtins

    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name == "uvicorn":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)

    assert main() == 1
    said = capsys.readouterr().err
    assert "uvicorn" in said, said
    assert "kingfisher_service.asgi:app" in said, (
        "the advice used to be 'install the extra', which was wrong for exactly "
        "the case that reaches here -- uvicorn is left out on purpose when "
        "something else serves the app"
    )


# -- the name uvicorn is pointed at ----------------------------------------


def test_there_is_an_application_for_a_server_to_point_at():
    """`uvicorn kingfisher_service.asgi:app`."""
    from kingfisher_service.asgi import app

    assert app.title == "kingfisher"


def test_the_obvious_target_is_a_module_which_is_why_asgi_exists():
    """`kingfisher_service:app` looks like the name to point at and is not: the
    package has a submodule called `app`, so that attribute is the module. A
    server pointed there serves something that is not an application, and no
    `__getattr__` can rescue it -- importing the submodule binds the name."""
    import types

    import kingfisher_service

    assert isinstance(kingfisher_service.app, types.ModuleType)


def test_the_docs_routes_are_logged_as_unmatched_too(client, caplog):
    """Only fastapi's own `APIRoute` records itself in the scope, so a starlette
    route answering 200 has no template to log. Imprecise and left that way: the
    alternative is falling back to the real path for *some* requests, and this
    rule is worth more without exceptions."""
    with caplog.at_level(logging.INFO, logger="kingfisher_service"):
        response = client.get("/openapi.json")

    assert response.status_code == 200
    assert "<unmatched>" in "\n".join(r.getMessage() for r in caplog.records)


def test_uvicorns_own_access_log_is_off(monkeypatch):
    """Otherwise the redaction above is decorative.

    uvicorn logs the *concrete* path by default, so a live server wrote
    `GET /sessions/5df2db83…` directly above our own
    `GET /sessions/{session_id}` -- the credential this module exists to keep
    out of logs, put there by the thing serving the app. Found by running it;
    no in-process test reaches uvicorn's logger.
    """
    import uvicorn

    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw))

    serve(ServiceConfig())

    assert seen["access_log"] is False


def test_the_entry_point_configures_its_own_logging_not_everyones(monkeypatch):
    """`basicConfig(level=INFO)` turns on every library at once. httpx then logs
    a line per outbound model call -- noise in a server, and the kind of default
    that eventually writes down something nobody meant to keep."""
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: None)

    main()

    assert logging.getLogger("kingfisher_service").level == logging.INFO
    assert logging.getLogger("kingfisher.origins").level == logging.INFO
    assert logging.getLogger().level == logging.WARNING


def test_turning_on_the_startup_line_does_not_turn_on_the_audit_trail(monkeypatch):
    """`kingfisher.audit` is unconfigured on purpose -- "nothing is written
    until a deployment attaches a handler, which is how 'may session ids be
    written here' stays a decision somebody makes rather than a default they
    inherit".

    The library's startup line is raised to INFO here, and a logger named
    `kingfisher` would be the audit one's parent -- so this server would have
    begun writing session ids in exchange for a line saying where the skills
    directory is. The sibling name is what stops that, and nothing else would
    have noticed.
    """
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: None)

    main()

    assert logging.getLogger("kingfisher.audit").getEffectiveLevel() == logging.WARNING


def test_the_app_builds_its_own_service_without_a_shared_thread_store(cfg, monkeypatch):
    """The lifespan branch, which nothing drove until this.

    `create_app()` given no instance builds one from the environment, and that
    is the only path a real deployment takes -- every other test here hands in a
    `Kingfisher` with a stub agent, so the branch the server actually runs was
    the one branch never exercised. It held one async SQLite database open for
    the life of the process, and kept doing so after the reason expired.

    `threads is None` is the whole assertion. It means each turn takes the
    per-session in-memory saver rather than sharing one database across every
    session in the process -- the contention `_async_checkpointer_for` describes
    the default as avoiding. What a later turn reads is `read_transcript`, so
    nothing durable rides on this.
    """
    from fastapi.testclient import TestClient
    from kingfisher_service import create_app

    # The fixture builds a `Config` directly and hands it a fake catalogue; this
    # path reads the environment, so the workspace needs the file a real one
    # has. The minimal catalogue is the one `load` prints when it is missing.
    (cfg.workspace / "models.yaml").write_text(
        "endpoints:\n"
        "  minimax:\n"
        "    api: anthropic\n"
        "    base_url: https://api.minimaxi.com/anthropic\n"
        "    key_env: MINIMAX_API_KEY\n"
        "\n"
        "default: MiniMax-M3\n"
        "\n"
        "models:\n"
        "  MiniMax-M3:\n"
        "    endpoint: minimax\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MINIMAX_API_KEY", "not-a-real-key")
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))

    with TestClient(create_app()) as http:
        built = http.app.state.kingfisher
        assert built is not None, "the lifespan built nothing"
        assert built.threads is None, (
            "the server wired a shared thread store; every session in this "
            "process would share one database"
        )
