"""Where a deployment's sessions are: each one's backend, and the questions about all."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import time
from dataclasses import replace
from pathlib import Path

import pytest
from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import FileDownloadResponse, FileUploadResponse

from kingfisher import Kingfisher
from kingfisher.cli.__main__ import main
from kingfisher.core.definitions.catalogue import bundled_skills_route
from kingfisher.core.layout import DEFAULT_LAYOUT, SKILLS_ROUTE, Layout
from kingfisher.plugins import LocalSessions
from kingfisher.plugins.sessions.backend import WorkspaceScopedBackend, _claim_at
from kingfisher.plugins.sessions.folders import LocalSessionDirs
from tests.checks import sessions as sessions_kit
from tests.checks.sessions import CONTRACT_SESSIONS, SESSIONS_CHECKS
from tests.unit.test_run import StubAgent
from tests.unit.test_session_files import Elsewhere

#: A layout renaming every folder a deployment may, beside the shipped one.
RENAMED = Layout(inputs="in", outputs="out", memory="mem", scratchpad="scratch")


@pytest.mark.parametrize("layout", [DEFAULT_LAYOUT, RENAMED], ids=["shipped", "renamed"])
def test_the_default_keeps_the_contract(cfg, layout):
    """Under a renamed layout too, since a kit spelling the shipped folder names fails a
    deployment that renamed them."""
    made = replace(cfg, layout=layout)
    for check in SESSIONS_CHECKS:
        check(lambda: (made, LocalSessions()))


class OnlyItsOwnFolders(LocalSessions):
    """A `Sessions` whose backend serves only the folders the layout names, as a stricter
    deployment's may, where kingfisher's own serves any path in a session."""

    def open(self, cfg, session_id, /, *, catalogue=None):
        backend = super().open(cfg, session_id, catalogue=catalogue)
        layout = cfg.layout
        served = (layout.inputs_route, layout.outputs_route, layout.memory_route,
                  layout.scratch_route)
        upload, download = backend.upload_files, backend.download_files

        def upload_files(files):
            return [
                upload([(path, body)])[0] if path.startswith(served)
                else FileUploadResponse(path=path, error="permission_denied")
                for path, body in files
            ]

        def download_files(paths):
            return [
                download([path])[0] if path.startswith(served)
                else FileDownloadResponse(path=path, error="permission_denied")
                for path in paths
            ]

        backend.upload_files = upload_files
        backend.download_files = download_files
        return backend


def test_the_kit_writes_where_the_deployment_keeps_its_outputs(cfg):
    """A kit spelling the shipped `/outputs` fails a deployment that renamed it, which
    kingfisher's own sessions, serving any path, never show."""
    made = replace(cfg, layout=RENAMED)
    writing = ("a_session_is_there_on_the_next_turn", "aopen_reaches_the_session_open_does")

    for check in (c for c in SESSIONS_CHECKS if c.__name__ in writing):
        check(lambda: (made, OnlyItsOwnFolders()))


def test_the_kit_runs_every_check_it_defines():
    """A check the kit defines and `SESSIONS_CHECKS` leaves out is one no deployment running
    the kit ever runs."""
    # Public and defined in the kit itself: its helpers are private, and what it imports
    # is not a check.
    defined = {
        name
        for name, value in vars(sessions_kit).items()
        if inspect.isfunction(value)
        and value.__module__ == sessions_kit.__name__
        and not name.startswith("_")
    }

    assert defined
    assert sorted(check.__name__ for check in SESSIONS_CHECKS) == sorted(defined)


class Shared(LocalSessions):
    """Every session handed one directory: each works, and each reads the others."""

    def open(self, cfg, session_id, /, *, catalogue=None):
        return super().open(cfg, "everyone", catalogue=catalogue)


class Overwriting(LocalSessions):
    """A claim written the obvious way, with a write, which never fails."""

    def claim(self, cfg, session_id, /, *, stale_after, now=None):
        return "written"


class GivesBackAnyClaim(LocalSessions):
    """A release that never reads the token, as `release(cfg, session_id)` was."""

    def release(self, cfg, session_id, token, /):
        LocalSessionDirs().remove_tree(_claim_at(cfg, session_id))


class OneLockForAll(LocalSessions):
    """One lock for the whole object, taken whichever session asks."""

    def claim(self, cfg, session_id, /, *, stale_after, now=None):
        return super().claim(cfg, CONTRACT_SESSIONS[0], stale_after=stale_after, now=now)

    def release(self, cfg, session_id, token, /):
        super().release(cfg, CONTRACT_SESSIONS[0], token)


def _check(name):
    (check,) = [c for c in SESSIONS_CHECKS if c.__name__ == name]
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
        return "beside"


class KeepsTheClaimAsync(LocalSessions):
    """An `arelease` that gives nothing back."""

    async def arelease(self, cfg, session_id, token, /):
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
        for check in SESSIONS_CHECKS:
            check(lambda: (cfg, LocalSessions()))

    asyncio.run(from_a_coroutine())


def test_the_kit_catches_two_sessions_on_one_filesystem(cfg):
    """The security-relevant one: every path is legal, and only this notices."""
    with pytest.raises(AssertionError, match="share one filesystem"):
        _check("two_sessions_are_kept_apart")(lambda: (cfg, Shared()))


def test_the_kit_catches_a_claim_that_is_not_exclusive(cfg):
    with pytest.raises(AssertionError, match="still holds"):
        _check("a_claim_is_exclusive")(lambda: (cfg, Overwriting()))


@pytest.mark.parametrize(
    "check",
    ["a_claim_is_released_only_by_its_token", "a_late_release_leaves_the_claim_that_took_over"],
)
def test_the_kit_catches_a_release_that_ignores_its_token(cfg, check):
    with pytest.raises(AssertionError, match="gave back"):
        _check(check)(lambda: (cfg, GivesBackAnyClaim()))


def test_two_callers_judging_one_claim_stale_take_it_over_once(cfg, monkeypatch):
    """Both saw a dead turn's claim, and the takeover was check, delete, create: the
    second deleted the claim the first had just taken and created its own, and both ran
    a turn in one session. Forced rather than raced: the second caller's verdict is
    reached, then the first takes over whole, then the second goes on.
    """
    session = CONTRACT_SESSIONS[0]
    sessions = LocalSessions()
    sessions.open(cfg, session)
    assert sessions.claim(cfg, session, stale_after=3600) is not None
    later = time.time() + 7200
    first: list[str | None] = []
    judging = LocalSessions.held

    def held_then_overtaken(self, cfg, session_id, /, *, stale_after, now=None):
        verdict = judging(self, cfg, session_id, stale_after=stale_after, now=now)
        if self is sessions and not first:
            first.append(LocalSessions().claim(cfg, session_id, stale_after=3600, now=later))
        return verdict

    with monkeypatch.context() as patched:
        patched.setattr(LocalSessions, "held", held_then_overtaken)
        second = sessions.claim(cfg, session, stale_after=3600, now=later)

    assert first[0] is not None, "the first caller did not take the stale claim over"
    assert second is None, "both callers took the claim over"
    # And the first one's claim is the one left standing.
    assert sessions.claim(cfg, session, stale_after=3600) is None
    sessions.release(cfg, session, first[0])
    assert sessions.claim(cfg, session, stale_after=3600) is not None


@pytest.mark.parametrize("racing", [False, True], ids=["alone", "two takers"])
def test_a_stale_claim_with_no_token_is_taken_over_once(cfg, monkeypatch, racing):
    """A claim an older kingfisher left, or a taker that died before naming itself, has
    no token and was named by its directory's mtime -- which marking it taken changes,
    so the taker's own re-check refused it and left it looking fresh for another
    `stale_after`.
    """
    session = CONTRACT_SESSIONS[0]
    sessions = LocalSessions()
    sessions.open(cfg, session)
    _claim_at(cfg, session).mkdir()
    later = time.time() + 7200
    first: list[str | None] = []
    judging = LocalSessions.held

    def held_then_overtaken(self, cfg, session_id, /, *, stale_after, now=None):
        verdict = judging(self, cfg, session_id, stale_after=stale_after, now=now)
        if self is sessions and not first:
            first.append(LocalSessions().claim(cfg, session_id, stale_after=3600, now=later))
        return verdict

    with monkeypatch.context() as patched:
        if racing:
            patched.setattr(LocalSessions, "held", held_then_overtaken)
        taken = sessions.claim(cfg, session, stale_after=3600, now=later)

    winners = [one for one in (*first, taken) if one is not None]
    assert len(winners) == 1, f"{len(winners)} callers took the claim over"
    assert sessions.claim(cfg, session, stale_after=3600) is None


def test_a_takeover_leaves_a_claim_taken_after_it_judged(cfg, monkeypatch):
    """The other order: the turn judged dead ends after all, and a new one claims the
    session, between a taker's verdict and its rename. Moving aside whatever was there
    would take the new turn's claim and let the taker in beside it.
    """
    session = CONTRACT_SESSIONS[0]
    sessions = LocalSessions()
    sessions.open(cfg, session)
    late = sessions.claim(cfg, session, stale_after=3600)
    assert late is not None
    newer: list[str | None] = []
    judging = LocalSessions.held

    def held_then_replaced(self, cfg, session_id, /, *, stale_after, now=None):
        verdict = judging(self, cfg, session_id, stale_after=stale_after, now=now)
        if self is sessions and not newer:
            LocalSessions().release(cfg, session_id, late)
            newer.append(LocalSessions().claim(cfg, session_id, stale_after=3600))
        return verdict

    with monkeypatch.context() as patched:
        patched.setattr(LocalSessions, "held", held_then_replaced)
        taker = sessions.claim(cfg, session, stale_after=3600, now=time.time() + 7200)

    assert newer[0] is not None
    assert taker is None, "the taker moved a fresh claim aside and took the session"
    assert sessions.claim(cfg, session, stale_after=3600) is None, "the new claim is gone"
    sessions.release(cfg, session, newer[0])


def test_a_release_not_its_own_never_frees_the_slot(cfg, monkeypatch):
    """A release that moved the claim aside before reading whose it was left the slot
    empty while it looked: a turn claiming in that instant held the session, and the
    claim could not go back. Forced: a claim is taken the moment anything is renamed.
    """
    session = CONTRACT_SESSIONS[0]
    sessions = LocalSessions()
    sessions.open(cfg, session)
    held = sessions.claim(cfg, session, stale_after=3600)
    assert held is not None
    renaming = Path.rename
    slipped_in: list[str | None] = []

    def rename_then_claimed(self, target):
        moved = renaming(self, target)
        if self.name == "claim" and not slipped_in:
            slipped_in.append(LocalSessions().claim(cfg, session, stale_after=3600))
        return moved

    with monkeypatch.context() as patched:
        patched.setattr(Path, "rename", rename_then_claimed)
        sessions.release(cfg, session, "a-token-this-claim-was-never-given")

    assert not slipped_in, "a release that was not this claim's moved it"
    sessions.release(cfg, session, held)
    assert sessions.claim(cfg, session, stale_after=3600) is not None


def test_a_release_leaves_a_claim_taken_over_while_it_looked(cfg, monkeypatch):
    """The turn read its own token, and before it moved the claim a taker judged it dead
    and replaced it: moving whatever was there then would end the taker's claim.
    """
    from kingfisher.plugins.sessions import backend

    session = CONTRACT_SESSIONS[0]
    sessions = LocalSessions()
    sessions.open(cfg, session)
    late = sessions.claim(cfg, session, stale_after=3600)
    assert late is not None
    identity = backend._identity
    taker: list[str | None] = []

    def read_then_overtaken(path):
        seen = identity(path)
        if path.name == "claim" and seen == late and not taker:
            taker.append("")
            taker[0] = LocalSessions().claim(
                cfg, session, stale_after=3600, now=time.time() + 7200
            )
        return seen

    with monkeypatch.context() as patched:
        patched.setattr(backend, "_identity", read_then_overtaken)
        sessions.release(cfg, session, late)

    assert taker[0] is not None, "the taker did not take the claim over"
    assert sessions.claim(cfg, session, stale_after=3600) is None, "the taker's claim is gone"
    sessions.release(cfg, session, taker[0])


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
        cfg, graph=StubAgent("ok"), sessions=LocalSessions()
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

    assert main(["reap", "--all"]) == 0

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
        "builtin_tools": ["read_file"],
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
                layout=DEFAULT_LAYOUT,
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
