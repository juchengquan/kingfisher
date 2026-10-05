"""Where a deployment's sessions are: each one's backend, and the questions about all."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pytest

from kingfisher import SESSION_BACKENDS_CONTRACT, DefaultBackends, Kingfisher, default_backends
from kingfisher.infrastructure.harness.backend import backend_at
from kingfisher.layout import CLAIM
from kingfisher.presentation.cli.__main__ import main
from tests.conftest import StubCheckpointer
from tests.unit.test_run import StubAgent
from tests.unit.test_session_files import Elsewhere


def test_the_default_keeps_the_contract(cfg):
    for check in SESSION_BACKENDS_CONTRACT:
        check(lambda: (cfg, default_backends))


def test_the_contract_is_not_quietly_empty():
    """A hand-maintained tuple, and a rule walking an empty one passes having checked
    nothing.
    """
    assert len(SESSION_BACKENDS_CONTRACT) >= 5


class Shared(DefaultBackends):
    """Every session handed one directory: each works, and each reads the others."""

    def open(self, cfg, session_id, /, *, catalogue=None, runner=None):
        return super().open(cfg, "everyone", catalogue=catalogue, runner=runner)


class Overwriting(DefaultBackends):
    """A claim written the obvious way, with a write, which never fails."""

    def open(self, cfg, session_id, /, *, catalogue=None, runner=None):
        built = super().open(cfg, session_id, catalogue=catalogue, runner=runner)
        built.claim = lambda name, *, stale_after, now=None: True
        return built


def _check(name):
    (check,) = [c for c in SESSION_BACKENDS_CONTRACT if c.__name__ == name]
    return check


class AsyncElsewhere(DefaultBackends):
    """An `aopen` written beside `open` rather than through it, reaching a session of
    its own -- the async path's reads somewhere the sync path never looks.
    """

    async def aopen(self, cfg, session_id, /, *, catalogue=None, runner=None):
        return self.open(cfg, f"{session_id}-async", catalogue=catalogue, runner=runner)


class ListsNothingAsync(DefaultBackends):
    """An `asessions` that forgot what `sessions` knows."""

    async def asessions(self, cfg):
        return ()


class KeepsEverythingAsync(DefaultBackends):
    """An `adelete` that reports success and removes nothing."""

    async def adelete(self, cfg, session_id):
        return None


def test_the_kit_catches_an_adelete_that_leaves_the_session(cfg):
    with pytest.raises(AssertionError, match="still listed"):
        _check("adelete_removes_the_session")(lambda: (cfg, KeepsEverythingAsync()))


def test_the_kit_catches_an_aopen_that_reaches_another_session(cfg):
    with pytest.raises(AssertionError, match="reach different sessions"):
        _check("aopen_reaches_the_session_open_does")(lambda: (cfg, AsyncElsewhere()))


def test_the_kit_catches_an_asessions_that_disagrees_with_sessions(cfg):
    with pytest.raises(AssertionError, match="does not list the sessions"):
        _check("asessions_lists_what_sessions_does")(lambda: (cfg, ListsNothingAsync()))


def test_the_kit_runs_from_a_test_already_on_an_event_loop(cfg):
    """A deployment's async suite calls the kit from a coroutine, where `asyncio.run`
    refuses to start a second loop.
    """

    async def from_a_coroutine() -> None:
        for check in SESSION_BACKENDS_CONTRACT:
            check(lambda: (cfg, default_backends))

    asyncio.run(from_a_coroutine())


def test_the_kit_catches_two_sessions_on_one_filesystem(cfg):
    """The security-relevant one: every path is legal, and only this notices."""
    with pytest.raises(AssertionError, match="share one filesystem"):
        _check("two_sessions_are_kept_apart")(lambda: (cfg, Shared()))


def test_the_kit_catches_a_claim_that_is_not_exclusive(cfg):
    with pytest.raises(AssertionError, match="still holds"):
        _check("a_claim_is_exclusive")(lambda: (cfg, Overwriting()))


def test_a_turn_refuses_while_the_backend_says_the_session_is_claimed(cfg):
    """The lock is the backend's, so a claim taken through any backend for the session
    is one the next turn meets.
    """
    from kingfisher import Request, SessionBusyError

    kf = Kingfisher(
        cfg, graph=StubAgent("ok"), backends=default_backends, threads=StubCheckpointer()
    )
    session_id = kf.run(Request("first")).session_id
    assert backend_at(cfg, cfg.workspace / "sessions" / session_id).claim(
        CLAIM, stale_after=3600
    )

    with pytest.raises(SessionBusyError):
        kf.run(Request("second", session_id=session_id))


# -- the command line, reaching a backend it was told about ------------------

#: Where `remote_backends` keeps its sessions, set by the test that names it.
REMOTE: list[Path] = []


def remote_backends() -> Elsewhere:
    """What `KINGFISHER_SESSION_BACKENDS_FACTORY` names in the tests below."""
    return Elsewhere(REMOTE[-1])


@pytest.fixture
def told_about_a_remote_backend(at_the_command_line, monkeypatch, tmp_path):
    REMOTE.append(tmp_path / "remote")
    monkeypatch.setenv("KINGFISHER_SESSION_BACKENDS_FACTORY", f"{__name__}:remote_backends")
    yield at_the_command_line
    REMOTE.pop()


def test_the_command_lists_what_the_named_backend_keeps(told_about_a_remote_backend, capsys):
    """Without the setting the command would list a local `sessions/` that holds none
    of them, and a deployment would see an empty workspace.
    """
    cfg = told_about_a_remote_backend
    remote_backends().open(cfg, "kept-elsewhere")

    assert main(["sessions", "--json"]) == 0

    listed = [one["id"] for one in json.loads(capsys.readouterr().out)["sessions"]]
    assert listed == ["kept-elsewhere"]
    assert not (cfg.workspace / "sessions" / "kept-elsewhere").exists()


def test_the_command_reaps_where_the_named_backend_keeps_them(told_about_a_remote_backend):
    cfg = told_about_a_remote_backend
    remote_backends().open(cfg, "old")
    stale = time.time() - 10_000
    os.utime(REMOTE[-1] / "old", (stale, stale))

    assert main(["reap", "--older-than", "1h"]) == 0

    assert not (REMOTE[-1] / "old").exists()


def test_building_a_session_s_backend_makes_its_data_read_only(cfg):
    """Not only after a placement: a session whose `/data` was left writable -- by
    hand, or by a process that died between unlocking and hardening -- is hardened the
    next time a turn asks for its backend, which is before the agent can touch it.
    """
    directory = cfg.workspace / "sessions" / "s"
    default_backends.open(cfg, "s")
    (directory / "data").chmod(0o755)
    assert os.access(directory / "data", os.W_OK), "not left writable; test proves nothing"

    built = default_backends.open(cfg, "s")

    assert not os.access(directory / "data", os.W_OK)
    assert built.unprotected == ()


def test_deciding_nothing_finds_a_session_the_named_backend_keeps(
    told_about_a_remote_backend, capsys
):
    """It checked for `<workspace>/sessions/<id>`, which a backend keeping sessions
    elsewhere never makes, and told somebody asking what their session waits on that
    it did not exist.
    """
    cfg = told_about_a_remote_backend
    remote_backends().open(cfg, "kept-elsewhere")

    main(["decide", "--session", "kept-elsewhere"])
    main(["decide", "--session", "never-was"])

    said = capsys.readouterr().err
    assert "session kept-elsewhere is not waiting on a decision" in said
    assert "no such session: never-was" in said


class Misdirected(DefaultBackends):
    """A backend whose own `host_path` answers from the wrong session: every file it
    serves is right, and every one it hands a tool by path is a neighbour's.
    """

    def open(self, cfg, session_id, /, *, catalogue=None, runner=None):
        built = super().open(cfg, session_id, catalogue=catalogue, runner=runner)
        neighbour = "kingfisher-contract-b" if session_id != "kingfisher-contract-b" else "x"
        root = cfg.workspace / "sessions" / neighbour
        built.host_path = lambda virtual: root / virtual.lstrip("/")
        return built


class NothingHere(DefaultBackends):
    """A backend that says none of its files are on this host."""

    def open(self, cfg, session_id, /, *, catalogue=None, runner=None):
        built = super().open(cfg, session_id, catalogue=catalogue, runner=runner)
        built.host_path = lambda virtual: None
        return built


def test_the_kit_catches_a_host_path_into_another_session(cfg):
    """The one that hands a tool, outside every fence, a neighbour's file -- and only a
    tool taking `path` would ever notice.
    """
    with pytest.raises(AssertionError, match="not this session's"):
        _check("a_host_path_stays_in_its_session")(lambda: (cfg, Misdirected()))


def test_a_backend_that_keeps_nothing_here_passes_the_host_path_check(cfg):
    """`None` is an honest answer: the tool is refused and reads through the backend."""
    _check("a_host_path_stays_in_its_session")(lambda: (cfg, NothingHere()))
