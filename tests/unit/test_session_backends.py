"""Where a deployment's sessions are: each one's backend, and the questions about all."""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import replace
from pathlib import Path

import pytest
from deepagents.backends import FilesystemBackend

from kingfisher import SESSION_BACKENDS_CONTRACT, Kingfisher, LocalSessions
from kingfisher.infrastructure.harness.backend import (
    WorkspaceScopedBackend,
    bundled_skills_route,
)
from kingfisher.infrastructure.harness.backend_contract import CONTRACT_SESSIONS
from kingfisher.layout import SKILLS_ROUTE
from kingfisher.presentation.cli.__main__ import main
from tests.conftest import StubCheckpointer
from tests.unit.test_run import StubAgent
from tests.unit.test_session_files import Elsewhere


def test_the_default_keeps_the_contract(cfg):
    for check in SESSION_BACKENDS_CONTRACT:
        check(lambda: (cfg, LocalSessions()))


def test_the_contract_is_not_quietly_empty():
    """A hand-maintained tuple, and a rule walking an empty one passes having checked
    nothing.
    """
    assert len(SESSION_BACKENDS_CONTRACT) >= 5


class Shared(LocalSessions):
    """Every session handed one directory: each works, and each reads the others."""

    def open(self, cfg, session_id, /, *, catalogue=None):
        return super().open(cfg, "everyone", catalogue=catalogue)


class Overwriting(LocalSessions):
    """A claim written the obvious way, with a write, which never fails."""

    def claim(self, cfg, session_id, /, *, stale_after, now=None):
        return True


class OneLockForAll(LocalSessions):
    """One lock for the whole object, taken whichever session asks."""

    def claim(self, cfg, session_id, /, *, stale_after, now=None):
        return super().claim(cfg, CONTRACT_SESSIONS[0], stale_after=stale_after, now=now)

    def release(self, cfg, session_id, /):
        super().release(cfg, CONTRACT_SESSIONS[0])


def _check(name):
    (check,) = [c for c in SESSION_BACKENDS_CONTRACT if c.__name__ == name]
    return check


class AsyncElsewhere(LocalSessions):
    """An `aopen` written beside `open` rather than through it, reaching a session of
    its own -- the async path's reads somewhere the sync path never looks.
    """

    async def aopen(self, cfg, session_id, /, *, catalogue=None):
        return self.open(cfg, f"{session_id}-async", catalogue=catalogue)


class ListsNothingAsync(LocalSessions):
    """An `asessions` that forgot what `sessions` knows."""

    async def asessions(self, cfg):
        return ()


class KeepsEverythingAsync(LocalSessions):
    """An `adelete` that reports success and removes nothing."""

    async def adelete(self, cfg, session_id):
        return None


class ClaimsBesideClaim(LocalSessions):
    """An `aclaim` that keeps its own book rather than the one `claim` writes."""

    async def aclaim(self, cfg, session_id, /, *, stale_after, now=None):
        return True


class KeepsTheClaimAsync(LocalSessions):
    """An `arelease` that gives nothing back."""

    async def arelease(self, cfg, session_id, /):
        return None


class WeighsNothingAsync(LocalSessions):
    async def asize(self, cfg, session_id):
        return 0


class MarksNothingAsync(LocalSessions):
    async def amark_used(self, cfg, session_id):
        return None


@pytest.mark.parametrize(
    ("check", "backends", "said"),
    [
        ("aclaim_and_claim_exclude_each_other", ClaimsBesideClaim, "still holds"),
        ("arelease_gives_back_what_claim_took", KeepsTheClaimAsync, "left the claim held"),
        ("asize_counts_what_size_does", WeighsNothingAsync, "where size counts"),
        ("amark_used_moves_the_session_on", MarksNothingAsync, "as last used as it was"),
    ],
    ids=["aclaim", "arelease", "asize", "amark_used"],
)
def test_the_kit_catches_an_async_twin_that_disagrees(cfg, check, backends, said):
    with pytest.raises(AssertionError, match=said):
        _check(check)(lambda: (cfg, backends()))


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
            check(lambda: (cfg, LocalSessions()))

    asyncio.run(from_a_coroutine())


def test_the_kit_catches_two_sessions_on_one_filesystem(cfg):
    """The security-relevant one: every path is legal, and only this notices."""
    with pytest.raises(AssertionError, match="share one filesystem"):
        _check("two_sessions_are_kept_apart")(lambda: (cfg, Shared()))


def test_the_kit_catches_a_claim_that_is_not_exclusive(cfg):
    with pytest.raises(AssertionError, match="still holds"):
        _check("a_claim_is_exclusive")(lambda: (cfg, Overwriting()))


def test_the_kit_catches_one_lock_for_every_session(cfg):
    """A turn in one session refusing a turn in another, which every other line of the
    check passes now that the lock is asked of the object serving every session."""
    with pytest.raises(AssertionError, match="one lock for every session"):
        _check("a_claim_is_exclusive")(lambda: (cfg, OneLockForAll()))


def test_a_turn_refuses_while_the_sessions_say_it_is_claimed(cfg):
    """The lock is the sessions', so a claim taken through any `LocalSessions` for the
    session is one the next turn meets.
    """
    from kingfisher import Request, SessionBusyError

    kf = Kingfisher(
        cfg, graph=StubAgent("ok"), sessions=LocalSessions(), threads=StubCheckpointer()
    )
    session_id = kf.run(Request("first")).session_id
    assert LocalSessions().claim(cfg, session_id, stale_after=3600)

    with pytest.raises(SessionBusyError):
        kf.run(Request("second", session_id=session_id))


# -- the command line, reaching a backend it was told about ------------------

#: Where `remote_backends` keeps its sessions, set by the test that names it.
REMOTE: list[Path] = []


def remote_backends() -> Elsewhere:
    """What `KINGFISHER_SESSIONS` names in the tests below."""
    return Elsewhere(REMOTE[-1])


@pytest.fixture
def told_about_a_remote_backend(at_the_command_line, monkeypatch, tmp_path):
    REMOTE.append(tmp_path / "remote")
    monkeypatch.setenv("KINGFISHER_SESSIONS", f"{__name__}:remote_backends")
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


def test_building_a_session_s_backend_makes_its_inputs_read_only(cfg):
    """Not only after a placement: a session whose `/inputs` was left writable -- by
    hand, or by a process that died between unlocking and hardening -- is hardened the
    next time a turn asks for its backend, which is before the agent can touch it.
    """
    directory = cfg.workspace / "sessions" / "s"
    LocalSessions().open(cfg, "s")
    (directory / "inputs").chmod(0o755)
    assert os.access(directory / "inputs", os.W_OK), "not left writable; test proves nothing"

    built = LocalSessions().open(cfg, "s")

    assert not os.access(directory / "inputs", os.W_OK)
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


class Misdirected(LocalSessions):
    """A backend whose own `host_path` answers from the wrong session: every file it
    serves is right, and every one it hands a tool by path is a neighbour's.
    """

    def open(self, cfg, session_id, /, *, catalogue=None):
        built = super().open(cfg, session_id, catalogue=catalogue)
        neighbour = "kingfisher-contract-b" if session_id != "kingfisher-contract-b" else "x"
        root = cfg.workspace / "sessions" / neighbour
        built.host_path = lambda virtual: root / virtual.lstrip("/")
        return built


class NothingHere(LocalSessions):
    """A backend that says none of its files are on this host."""

    def open(self, cfg, session_id, /, *, catalogue=None):
        built = super().open(cfg, session_id, catalogue=catalogue)
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


# -- the skills a session serves ----------------------------------------------

SKILL = "---\nname: {name}\ndescription: The {name} procedure.\n---\n\n# {name}\n\nDo it.\n"


def _skill(folder: Path, name: str) -> None:
    (folder / name).mkdir(parents=True)
    (folder / name / "SKILL.md").write_text(SKILL.format(name=name), encoding="utf-8")


@pytest.fixture
def skilled(cfg, tmp_path):
    """A skill in every place one is mounted from: the catalogue's top level, a folder
    of it, a mount, and a subagent carrying its own.
    """
    _skill(cfg.skills_dir, "review")
    _skill(cfg.skills_dir / "incident", "timeline")
    _skill(tmp_path / "vendor", "lookup")
    carried = tmp_path / "package" / "skills"
    _skill(carried, "masking")
    subagents = cfg.catalogue_roots["subagents"]
    subagents.mkdir(parents=True, exist_ok=True)
    entry = {
        "name": "carrier",
        "description": "Carries a skill of its own.",
        "system_prompt": "Go.",
        "skills": [str(carried)],
    }
    (subagents / "carrier.py").write_text(f"SUBAGENTS = [{entry!r}]\n", encoding="utf-8")
    return replace(cfg, skills_mounts={"vendor": tmp_path / "vendor"})


def _rerouted(edit):
    """Kingfisher's own backends, with their routes edited after each is built."""

    class Rerouted(LocalSessions):
        def open(self, cfg, session_id, /, *, catalogue=None):
            built = super().open(cfg, session_id, catalogue=catalogue)
            return WorkspaceScopedBackend(
                default=built.default,
                routes=edit(dict(built.routes)),
                session_dir=built._session_dir,
                workspace=built.workspace,
            )

    return Rerouted()


def _catalogue_over(*skills: str):
    """`/skills/` routed to a folder holding these, each at the top level."""

    def edit(routes, elsewhere):
        for name in skills:
            _skill(elsewhere, name)
        return {**routes, SKILLS_ROUTE: FilesystemBackend(root_dir=str(elsewhere))}

    return edit


def _without(route: str):
    def edit(routes, _elsewhere):
        return {path: backend for path, backend in routes.items() if path != route}

    return edit


def test_the_default_serves_every_skill_it_reads(skilled):
    """The control: each case below fails on the route it changes, not on a fixture
    the default could not pass either.
    """
    _check("a_session_serves_the_skills_kingfisher_read")(lambda: (skilled, LocalSessions()))


@pytest.mark.parametrize(
    "edit",
    [
        _catalogue_over("impostor"),
        # Every name still served, and `timeline` out of its folder: the deny rule for
        # it names `/skills/incident/timeline/**`, where nothing is.
        _catalogue_over("review", "timeline"),
        _catalogue_over("review"),
        _without(f"{SKILLS_ROUTE}vendor/"),
        _without(bundled_skills_route("carrier")),
    ],
    ids=[
        "other-skills",
        "same-names-other-paths",
        "a-folder-missing",
        "mount-unrouted",
        "bundle-unrouted",
    ],
)
def test_the_kit_catches_skills_served_from_somewhere_kingfisher_did_not_read(
    skilled, tmp_path, edit
):
    """A backend serving `/skills/` other than kingfisher read leaves grants naming
    skills the agent cannot find, and deny rules naming paths nothing is at.
    """
    backends = _rerouted(lambda routes: edit(routes, tmp_path / "elsewhere"))

    with pytest.raises(AssertionError, match="serves /skills/ differently"):
        _check("a_session_serves_the_skills_kingfisher_read")(lambda: (skilled, backends))
