"""What a `Sessions` promises, as checks.

Not shipped: kingfisher holds `LocalSessions` to these, and a deployment writing its own
copies them. Plain functions raising `AssertionError`, as `backend` beside it is. `make`
returns a fresh `(Config, sessions)` pair, because every question a `Sessions` answers
is about one deployment's sessions. They write files, as the backend's do.
"""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from deepagents.backends import FilesystemBackend

from kingfisher.plugins import Definitions, host_path, list_skills

if TYPE_CHECKING:
    from collections.abc import Callable




#: Session ids the backends checks use. Two, because the property that matters most is
#: that they do not collide.
CONTRACT_SESSIONS = ("kingfisher-contract-a", "kingfisher-contract-b")


def _ids(listing: Any) -> set[str]:
    return {name for name, _ in listing}


def a_session_asked_for_is_listed(make: Callable[[], Any]) -> None:
    """`kingfisher sessions` and `reap` see what the listing says, and nothing else."""
    cfg, backends = make()
    one = CONTRACT_SESSIONS[0]
    backends.open(cfg, one)
    if one not in _ids(backends.sessions(cfg)):
        msg = f"a backend was built for {one!r}, and `sessions` does not list it"
        raise AssertionError(msg)


def two_sessions_are_kept_apart(make: Callable[[], Any]) -> None:
    """The one that matters most. Every path is legal and each session reads the other's
    files as its own, so nothing else would notice.
    """
    cfg, backends = make()
    one, other = CONTRACT_SESSIONS
    kept = f"{cfg.layout.outputs_route}kept-apart"
    backends.open(cfg, one).upload_files([(kept, b"one's")])
    (seen,) = backends.open(cfg, other).download_files([kept])
    if seen.error is None:
        msg = f"{other!r} reads {one!r}'s {kept}: two sessions share one filesystem"
        raise AssertionError(msg)


def a_session_is_there_on_the_next_turn(make: Callable[[], Any]) -> None:
    """Each turn builds its backend again. What one turn wrote, the next must find."""
    cfg, backends = make()
    one = CONTRACT_SESSIONS[0]
    again = f"{cfg.layout.outputs_route}again"
    backends.open(cfg, one).upload_files([(again, b"still here")])
    (seen,) = backends.open(cfg, one).download_files([again])
    if seen.content != b"still here":
        msg = f"a second backend for {one!r} did not find what the first wrote: {seen!r}"
        raise AssertionError(msg)


def a_claim_is_exclusive(make: Callable[[], Any]) -> None:
    """Two turns in one session share a conversation and the last write wins; the
    claim is what refuses the second. A `write` that overwrites would let both take it.

    And it is one session's: the lock is asked of the object serving every session, so
    one that kept a single lock for all of them would let one turn run at a time across
    the whole deployment, and pass every other line here.
    """
    cfg, backends = make()
    one, two = CONTRACT_SESSIONS
    backends.open(cfg, one)
    backends.open(cfg, two)
    if (first := backends.claim(cfg, one, stale_after=3600)) is None:
        msg = "a claim nobody held was refused"
        raise AssertionError(msg)
    if backends.claim(cfg, one, stale_after=3600) is not None:
        msg = f"{one!r} was claimed a second time while the first claim still holds"
        raise AssertionError(msg)
    if (other := backends.claim(cfg, two, stale_after=3600)) is None:
        msg = f"claiming {one!r} refused a claim on {two!r}: one lock for every session"
        raise AssertionError(msg)
    backends.release(cfg, one, first)
    if (again := backends.claim(cfg, one, stale_after=3600)) is None:
        msg = "a released claim could not be taken again"
        raise AssertionError(msg)
    backends.release(cfg, one, again)
    backends.release(cfg, two, other)


def a_claim_is_released_only_by_its_token(make: Callable[[], Any]) -> None:
    """`release` is told whose claim to give back. One that gave back whatever was there
    would let any caller holding a session id end the turn running in it.
    """
    cfg, backends = make()
    one = CONTRACT_SESSIONS[0]
    backends.open(cfg, one)
    if (token := backends.claim(cfg, one, stale_after=3600)) is None:
        msg = "a claim nobody held was refused"
        raise AssertionError(msg)
    backends.release(cfg, one, "a-token-this-claim-was-never-given")
    if backends.claim(cfg, one, stale_after=3600) is not None:
        msg = "a release naming another token gave back a claim it did not take"
        raise AssertionError(msg)
    backends.release(cfg, one, token)


def a_late_release_leaves_the_claim_that_took_over(make: Callable[[], Any]) -> None:
    """A turn that outlived `stale_after` has had its claim taken over. Its release, when
    it does end, must not end the claim of the turn that took it -- that turn would then
    share its session with the next one.
    """
    cfg, backends = make()
    one = CONTRACT_SESSIONS[0]
    backends.open(cfg, one)
    if (late := backends.claim(cfg, one, stale_after=3600)) is None:
        msg = "a claim nobody held was refused"
        raise AssertionError(msg)
    # Seen from an hour on, the first claim is a dead turn's.
    later = time.time() + 7200
    if (taken := backends.claim(cfg, one, stale_after=3600, now=later)) is None:
        msg = "a claim older than stale_after was not taken over"
        raise AssertionError(msg)
    if taken == late:
        msg = "the claim that took over was handed the token of the one it replaced"
        raise AssertionError(msg)
    backends.release(cfg, one, late)
    if backends.claim(cfg, one, stale_after=3600) is not None:
        msg = "a late release gave back the claim of the turn that took it over"
        raise AssertionError(msg)
    backends.release(cfg, one, taken)


def a_deleted_session_is_gone(make: Callable[[], Any]) -> None:
    cfg, backends = make()
    one = CONTRACT_SESSIONS[0]
    backends.open(cfg, one)
    failure = backends.delete(cfg, one)
    if failure is not None or one in _ids(backends.sessions(cfg)):
        msg = f"deleting {one!r} answered {failure!r} and left it listed"
        raise AssertionError(msg)


def a_host_path_stays_in_its_session(make: Callable[[], Any]) -> None:
    """Where a backend says a file is on this host, it is that session's file.

    Asked the way a workspace tool's `path` is resolved -- a backend's own `host_path`,
    or its routes -- because that answer is handed to a tool as a real file to open,
    in kingfisher's own process and outside every fence. An answer pointing into
    another session hands a tool that session's files. `None`, "not on this host", is
    always allowed: the tool is refused and told to read through the backend instead.

    Checked by reading what the answer names rather than by comparing paths, because
    a backend answering from a mount knows its own layout and this does not.
    """
    cfg, backends = make()
    one, other = CONTRACT_SESSIONS
    mine, theirs = backends.open(cfg, one), backends.open(cfg, other)
    whose = f"{cfg.layout.outputs_route}whose"
    mine.upload_files([(whose, f"{one}'s".encode())])
    theirs.upload_files([(whose, f"{other}'s".encode())])
    answered = host_path(mine, whose)
    if answered is None:
        return
    _, where = answered
    try:
        held = where.read_bytes()
    except OSError as unreadable:
        msg = f"{one!r}'s backend says {whose} is {where}, which cannot be read"
        raise AssertionError(msg) from unreadable
    if held != f"{one}'s".encode():
        msg = (
            f"{one!r}'s backend says {whose} is {where}, which holds {held!r}: "
            "a tool handed that path opens a file that is not this session's"
        )
        raise AssertionError(msg)


def a_session_serves_the_skills_kingfisher_read(make: Callable[[], Any]) -> None:
    """Under `/skills/`, a session's backend serves the skills kingfisher read itself.

    Grants and the read-deny rules are built from what kingfisher read; the agent reads
    through the backend. One serving `/skills/` from anywhere else splits the two with
    nothing said: a granted skill the agent cannot find, and a skill kingfisher never
    read, which no deny rule names, so it stays readable whatever a request narrowed.

    Listed through deepagents' own lister at each route a skills index is given, and
    compared by name and path, rather than by asking what backs each route: a backend
    serving the same folders another way passes, and the right kind of backend over
    the wrong folder does not.
    """
    cfg, backends = make()
    catalogue = Definitions.from_config(cfg).warm()
    one = CONTRACT_SESSIONS[0]
    session = backends.open(cfg, one, catalogue=catalogue)
    # Source by source, because deepagents lists a source one level deep: a skill in a
    # catalogue folder is found only by listing that folder.
    places = catalogue.skill_sources()
    read: set[tuple[str, str]] = set()
    served: set[tuple[str, str]] = set()
    for route, directory, path in places:
        read |= {
            (skill["name"], f"{route}{skill['path'].lstrip('/')}")
            for skill in list_skills(FilesystemBackend(root_dir=str(directory)), path)
        }
        served |= {
            (skill["name"], skill["path"])
            for skill in list_skills(session, f"{route}{path.lstrip('/')}")
        }
    if read != served:
        msg = (
            f"{one!r} serves /skills/ differently from the folders kingfisher read -- "
            f"read and not served: {sorted(read - served)}; served and never read: "
            f"{sorted(served - read)}. Grants and read-deny rules come from what "
            "kingfisher read, so the first are granted and missing and the second "
            "are readable whatever a request narrowed"
        )
        raise AssertionError(msg)


def _on_a_loop_of_its_own(awaited: Callable[[], Any]) -> Any:
    """`awaited()` run to the end on a new event loop, in a thread of its own.

    A thread because a deployment may run this kit from a test that is already on a
    loop, where `asyncio.run` refuses to start a second one.
    """
    with ThreadPoolExecutor(max_workers=1) as one:
        return one.submit(lambda: asyncio.run(awaited())).result()


def aopen_reaches_the_session_open_does(make: Callable[[], Any]) -> None:
    """The async path opens sessions with `aopen`. One that reached another session, or
    a fresh one, would put an async caller's reads somewhere the sync path never looks.
    """
    cfg, backends = make()
    one, _ = CONTRACT_SESSIONS
    twins = f"{cfg.layout.outputs_route}twins"
    backends.open(cfg, one).upload_files([(twins, b"written through open")])

    async def opened() -> Any:
        return await backends.aopen(cfg, one)

    (seen,) = _on_a_loop_of_its_own(opened).download_files([twins])
    if seen.error or seen.content != b"written through open":
        msg = (
            f"what aopen returned for {one!r} does not hold what open's backend wrote "
            f"there ({seen.error or seen.content!r}): the two reach different sessions"
        )
        raise AssertionError(msg)


def asessions_lists_what_sessions_does(make: Callable[[], Any]) -> None:
    """An async caller's `asession` is answered from `asessions`. A listing that
    disagreed would make a session exist for one caller and not for another.
    """
    cfg, backends = make()
    one, _ = CONTRACT_SESSIONS
    backends.open(cfg, one)

    async def listed() -> Any:
        return await backends.asessions(cfg)

    if _ids(_on_a_loop_of_its_own(listed)) != _ids(backends.sessions(cfg)):
        msg = "asessions does not list the sessions that sessions does"
        raise AssertionError(msg)


def adelete_removes_the_session(make: Callable[[], Any]) -> None:
    """`Session.adelete` deletes through `adelete`. One that left the session listed would
    leave an async caller's deletion undone, with nothing said.
    """
    cfg, backends = make()
    one, _ = CONTRACT_SESSIONS
    backends.open(cfg, one)

    async def deleted() -> Any:
        return await backends.adelete(cfg, one)

    failure = _on_a_loop_of_its_own(deleted)
    if failure is not None or one in _ids(backends.sessions(cfg)):
        msg = f"adelete({one!r}) answered {failure!r} and the session is still listed"
        raise AssertionError(msg)


def aclaim_and_claim_exclude_each_other(make: Callable[[], Any]) -> None:
    """An async turn takes its claim with `aclaim`. One that did not see a claim `claim`
    took, or the other way round, would let a sync turn and an async one into the same
    session at once.
    """
    cfg, backends = make()
    one = CONTRACT_SESSIONS[0]
    backends.open(cfg, one)

    async def taken() -> str | None:
        return await backends.aclaim(cfg, one, stale_after=3600)

    if (first := backends.claim(cfg, one, stale_after=3600)) is None:
        msg = "a claim nobody held was refused"
        raise AssertionError(msg)
    if _on_a_loop_of_its_own(taken) is not None:
        msg = "aclaim took a claim that claim still holds"
        raise AssertionError(msg)
    backends.release(cfg, one, first)
    if (second := _on_a_loop_of_its_own(taken)) is None:
        msg = "aclaim could not take a released claim"
        raise AssertionError(msg)
    if backends.claim(cfg, one, stale_after=3600) is not None:
        msg = "claim took a claim that aclaim still holds"
        raise AssertionError(msg)
    backends.release(cfg, one, second)


def arelease_gives_back_what_claim_took(make: Callable[[], Any]) -> None:
    """An async turn gives its claim back with `arelease`. One that kept it would leave
    the session answering "busy" to every turn after the first.
    """
    cfg, backends = make()
    one = CONTRACT_SESSIONS[0]
    backends.open(cfg, one)

    if (token := backends.claim(cfg, one, stale_after=3600)) is None:
        msg = "a claim nobody held was refused"
        raise AssertionError(msg)

    async def released() -> None:
        await backends.arelease(cfg, one, token)

    _on_a_loop_of_its_own(released)
    if (again := backends.claim(cfg, one, stale_after=3600)) is None:
        msg = "arelease left the claim held: a second turn could not take it"
        raise AssertionError(msg)
    backends.release(cfg, one, again)


def asize_counts_what_size_does(make: Callable[[], Any]) -> None:
    """An async turn checks the quota with `asize`. Counted differently, a session would
    be over its bound for one kind of caller and under it for the other.
    """
    cfg, backends = make()
    one = CONTRACT_SESSIONS[0]
    backends.open(cfg, one).upload_files([(f"{cfg.layout.outputs_route}weighed", b"x" * 1024)])

    async def counted() -> Any:
        return await backends.asize(cfg, one)

    if (said := _on_a_loop_of_its_own(counted)) != (expected := backends.size(cfg, one)):
        msg = f"asize counts {said} bytes where size counts {expected}"
        raise AssertionError(msg)


def amark_used_moves_the_session_on(make: Callable[[], Any]) -> None:
    """An async turn records its session as used with `amark_used`, which is what keeps
    a conversation in daily use out of a sweep. One that did nothing would leave it
    looking as idle as the day it opened, and `reap` would take it.

    Waits a second first, so a filesystem clock that counts whole seconds still sees
    the session move on.
    """
    cfg, backends = make()
    one = CONTRACT_SESSIONS[0]
    backends.open(cfg, one)
    before = dict(backends.sessions(cfg))[one]
    time.sleep(1.1)

    async def marked() -> None:
        await backends.amark_used(cfg, one)

    _on_a_loop_of_its_own(marked)
    if dict(backends.sessions(cfg))[one] <= before:
        msg = f"amark_used({one!r}) left the session as last used as it was before"
        raise AssertionError(msg)


#: Every check a deployment's `Sessions` must pass. `make` returns a fresh
#: `(Config, backends)` pair, because every question a backends object answers is
#: about one deployment's sessions. These write files, as `filesystem_consistency`,
#: `shell_denied` and `host_path_refusal` do.
SESSIONS_CHECKS: tuple[Callable[[Callable[[], Any]], None], ...] = (
    two_sessions_are_kept_apart,
    a_session_is_there_on_the_next_turn,
    a_claim_is_exclusive,
    a_claim_is_released_only_by_its_token,
    a_late_release_leaves_the_claim_that_took_over,
    a_session_asked_for_is_listed,
    a_deleted_session_is_gone,
    a_host_path_stays_in_its_session,
    a_session_serves_the_skills_kingfisher_read,
    aopen_reaches_the_session_open_does,
    asessions_lists_what_sessions_does,
    adelete_removes_the_session,
    aclaim_and_claim_exclude_each_other,
    arelease_gives_back_what_claim_took,
    asize_counts_what_size_does,
    amark_used_moves_the_session_on,
)
