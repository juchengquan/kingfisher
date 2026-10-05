"""Every check a backend a deployment supplied must pass.

Here rather than beside the other four kits in `kingfisher.testing`, because the
sharpest check needs deepagents and that module is permitted nothing foreign --
`test_architecture` gives the package root an empty set, and widening it to reach
one function would have permitted the agent runtime in `config` and `layout` too.
Re-exported from `kingfisher`, so a deployment still writes one import.

`AssertionError` by hand and no pytest, the rule the other kits keep: a kit that
imported a test framework would put one in the runtime wheel.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from deepagents.backends import CompositeBackend
from deepagents.backends.protocol import SandboxBackendProtocol

from kingfisher.config import ConfigError
from kingfisher.infrastructure.harness.host_paths import HostPathError
from kingfisher.infrastructure.harness.permitted_backend import host_path
from kingfisher.layout import denied_read_scopes, denied_scopes

if TYPE_CHECKING:
    from collections.abc import Callable


def _executes(backend: Any) -> bool:
    """What deepagents asks before it offers the shell tool at all."""
    if isinstance(backend, CompositeBackend):
        return isinstance(backend.default, SandboxBackendProtocol)
    return isinstance(backend, SandboxBackendProtocol)


def execution_support(make: Callable[[], Any]) -> None:
    """The one failure with no symptom: deepagents drops `execute` and tells nobody.

    `isinstance`, not the methods present -- `SandboxBackendProtocol` is an abstract
    base class, so a backend that implements `execute` correctly and inherits nothing
    is treated as having none. `FilesystemMiddleware` removes the tool from the
    roster, the model is told execution is unavailable if it reaches for it anyway,
    and the deployment hears nothing at all. Inherit `BaseSandbox` or register with
    the ABC; implementing the methods is not enough.
    """
    backend = make()
    if not _executes(backend):
        msg = (
            f"{type(backend).__name__} is not recognised by deepagents as running "
            "commands, so the agent is given no shell and nothing says so. It has to "
            "be a SandboxBackendProtocol -- by inheritance, not by having the methods "
            "-- or a CompositeBackend whose `default` is one"
        )
        raise AssertionError(msg)


def route_coverage(make: Callable[[], Any]) -> None:
    """Otherwise the graph will not build, with a message that never says kingfisher.

    `FilesystemMiddleware` refuses `permissions=` outright on a backend that executes
    unless every rule path sits under one of its routes -- so a read-only rule is only
    a legal sentence about a path something routes. Caught here, where the scopes can
    be named, rather than at the first `create_deep_agent` call.
    """
    backend = make()
    if not _executes(backend):
        return  # the check above owns that failure; this one would only repeat it
    scopes = (*denied_scopes(), *denied_read_scopes())
    routes = tuple(backend.routes) if isinstance(backend, CompositeBackend) else ()
    uncovered = [
        scope for scope in scopes if not any(scope.startswith(route) for route in routes)
    ]
    if uncovered:
        msg = (
            f"{type(backend).__name__} routes nothing covering {sorted(set(uncovered))}, "
            "and kingfisher denies writes or reads under each of them. deepagents "
            "refuses permissions on an executing backend unless every rule sits under "
            f"a route, so the graph will not build. Routed: {sorted(routes)}"
        )
        raise AssertionError(msg)


def filesystem_consistency(make: Callable[[], Any]) -> None:
    """The promise `prompts/system.md` makes the model, and the one nothing else tests.

    *"A virtual path becomes a shell path by dropping the leading slash, and nothing
    in the workspace is out of the shell's reach"* -- written in a table the model
    reads every turn. A backend routing a path somewhere the shell cannot follow
    leaves the agent able to read its inputs and unable to run anything over them, and
    the only symptom is a confused model. Both directions, because they fail
    separately: a route the shell cannot see, and a shell whose writes the tools
    cannot find.

    **The content and the filename are different tokens, and that is not tidiness.**
    Written with one token for both, `cat` failing prints the path it could not find
    -- so the check passed against a backend where the shell reached nothing at all,
    on the error message quoting its own argument back.
    """
    backend = make()
    if not _executes(backend):
        return
    body, name = uuid4().hex, uuid4().hex
    for virtual, write in (
        (f"/derived/{name}-tools.txt", lambda path: backend.write(path, body)),
        (
            f"/derived/{name}-shell.txt",
            lambda path: backend.execute(f"printf %s {body} > {path.lstrip('/')}"),
        ),
    ):
        write(virtual)
        seen = backend.execute(f"cat {virtual.lstrip('/')}")
        if body not in (seen.output or ""):
            msg = (
                f"{virtual} does not hold what was written to it when the shell reads "
                f"it as {virtual.lstrip('/')}: got {seen.output!r} (exit "
                f"{seen.exit_code}). The agent is told the two are one filesystem"
            )
            raise AssertionError(msg)
        result = backend.read(virtual)
        if body not in ((result.file_data or {}).get("content", "")):
            msg = (
                f"{virtual} does not hold what was written to it when the file tools "
                f"read it: got {result.error or result.file_data!r}. The agent is told "
                "the two are one filesystem"
            )
            raise AssertionError(msg)


def shell_denied(make: Callable[[], Any]) -> None:
    """The agent's shell may not write under `/.harness` or `/data`.

    `/.harness` is what kingfisher reads back and trusts -- the pinned agent, the
    conversation, a paused turn -- and `/data` is the caller's input, which the agent
    is promised it cannot change. The file tools are refused both by the turn's
    permissions; the shell bypasses those, so on any backend but kingfisher's own this
    is the only thing that says the shell is refused too.

    Driven through `execute` rather than read off the backend's configuration, and
    both ways a shell writes: a new file, and over one kingfisher put there. The
    second is the one a pinned agent is rewritten by.
    """
    backend = make()
    if not _executes(backend):
        return
    token = uuid4().hex
    written: list[str] = []
    for route, what in (
        ("/.harness", "what kingfisher reads back and trusts"),
        ("/data", "the caller's input"),
    ):
        kept, fresh = f"{route}/{token}-kept", f"{route}/{token}-fresh"
        backend.upload_files([(kept, b"as written")])
        for path in (kept, fresh):
            backend.execute(f"printf %s {token} > {path.lstrip('/')}")
        after_kept, after_fresh = backend.download_files([kept, fresh])
        if after_kept.content != b"as written" or after_fresh.error is None:
            written.append(f"{route}, {what}")
    if written:
        msg = (
            f"the shell wrote under {'; and under '.join(written)}. Each is only safe "
            "where the backend keeps the shell out of it"
        )
        raise AssertionError(msg)


def host_path_refusal(make: Callable[[], Any]) -> None:
    """Conditional on purpose: refusing host paths at all is kingfisher's policy, not
    an obligation a replacement inherits.

    The refusal exists because the agent's paths are virtual and a real machine sits
    underneath, so naming one means the model confused the two views. Inside a sandbox
    of its own, `/etc/passwd` is an ordinary file and refusing it would be wrong -- so
    this asks nothing of a backend that allows it. What it does ask is that a backend
    which *does* refuse raises `HostPathError`, because `HostPathGuard` catches that
    type and nothing else: refuse with anything else and a mistake the model could
    have corrected reaches it as a failed turn.
    """
    backend = make()
    try:
        backend.read("/etc/passwd")
    except HostPathError:
        return
    except Exception as refused:
        msg = (
            f"reading a host path was refused with {type(refused).__name__}, which "
            "HostPathGuard does not catch -- it catches HostPathError and turns it "
            "into a correction the model can act on. Raise "
            "kingfisher.HostPathError, or do not "
            f"refuse. Got: {refused}"
        )
        raise AssertionError(msg) from refused


#: Every check a backend a deployment's `open` returned must pass, in the order a
#: deployment wants to read them: the silent failure first, then the one that stops
#: the graph building, then the promise the prompt makes, then what the shell must
#: not write, then the refusal.
BACKEND_CONTRACT: tuple[Callable[[Callable[[], Any]], None], ...] = (
    execution_support,
    route_coverage,
    filesystem_consistency,
    shell_denied,
    host_path_refusal,
)


def refuse_unusable_backend(backend: Any) -> None:
    """Two of the four, run on every backend the harness resolves.

    These two only *look*: an isinstance and a scan of the routes, no I/O and no
    turn's worth of work, so running them costs nothing and the default pays the
    same nothing every other build does. The other two write files and run shell
    commands -- a deployment's to pay in its own tests, never a turn's.

    Running any of them at all is what stands in for a property this seam used to
    have for free. A deployment handed the backend kingfisher built kept the route
    table and the confinement by returning what it was given; one that builds its
    own starts from nothing, and `execution_support` is the failure with no symptom
    at the end of that road -- deepagents drops the shell, tells the model execution
    is unavailable, and says nothing to the deployment.

    `ConfigError` rather than the kit's `AssertionError`, and the difference is who
    made the mistake: the kit is a deployment asserting about its own adapter, this
    is a library refusing what it was handed, and an `AssertionError` out of a
    library reads as the library's own bug.
    """
    try:
        execution_support(lambda: backend)
        route_coverage(lambda: backend)
    except AssertionError as unusable:
        raise ConfigError(str(unusable)) from unusable


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
    backends.open(cfg, one).upload_files([("/derived/kept-apart", b"one's")])
    (seen,) = backends.open(cfg, other).download_files(["/derived/kept-apart"])
    if seen.error is None:
        msg = f"{other!r} reads {one!r}'s /derived: two sessions share one filesystem"
        raise AssertionError(msg)


def a_session_is_there_on_the_next_turn(make: Callable[[], Any]) -> None:
    """Each turn builds its backend again. What one turn wrote, the next must find."""
    cfg, backends = make()
    one = CONTRACT_SESSIONS[0]
    backends.open(cfg, one).upload_files([("/derived/again", b"still here")])
    (seen,) = backends.open(cfg, one).download_files(["/derived/again"])
    if seen.content != b"still here":
        msg = f"a second backend for {one!r} did not find what the first wrote: {seen!r}"
        raise AssertionError(msg)


def a_claim_is_exclusive(make: Callable[[], Any]) -> None:
    """Two turns in one session share a conversation and the last write wins; the
    claim is what refuses the second. A `write` that overwrites would let both take it.
    """
    cfg, backends = make()
    one = CONTRACT_SESSIONS[0]
    first, second = backends.open(cfg, one), backends.open(cfg, one)
    if not first.claim("contract-claim", stale_after=3600):
        msg = "a claim nobody held was refused"
        raise AssertionError(msg)
    if second.claim("contract-claim", stale_after=3600):
        msg = "a second backend for the same session took a claim the first still holds"
        raise AssertionError(msg)
    first.release("contract-claim")
    if not second.claim("contract-claim", stale_after=3600):
        msg = "a released claim could not be taken again"
        raise AssertionError(msg)
    second.release("contract-claim")


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
    mine.upload_files([("/derived/whose", f"{one}'s".encode())])
    theirs.upload_files([("/derived/whose", f"{other}'s".encode())])
    answered = host_path(mine, "/derived/whose")
    if answered is None:
        return
    _, where = answered
    try:
        held = where.read_bytes()
    except OSError as unreadable:
        msg = f"{one!r}'s backend says /derived/whose is {where}, which cannot be read"
        raise AssertionError(msg) from unreadable
    if held != f"{one}'s".encode():
        msg = (
            f"{one!r}'s backend says /derived/whose is {where}, which holds {held!r}: "
            "a tool handed that path opens a file that is not this session's"
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
    backends.open(cfg, one).upload_files([("/derived/twins", b"written through open")])

    async def opened() -> Any:
        return await backends.aopen(cfg, one)

    (seen,) = _on_a_loop_of_its_own(opened).download_files(["/derived/twins"])
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
    """`adelete_session` deletes with `adelete`. One that left the session listed would
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


#: Every check a deployment's `SessionBackends` must pass. `make` returns a fresh
#: `(Config, backends)` pair, because every question a backends object answers is
#: about one deployment's sessions. These write files, as `filesystem_consistency`,
#: `shell_denied` and `host_path_refusal` do.
SESSION_BACKENDS_CONTRACT: tuple[Callable[[Callable[[], Any]], None], ...] = (
    two_sessions_are_kept_apart,
    a_session_is_there_on_the_next_turn,
    a_claim_is_exclusive,
    a_session_asked_for_is_listed,
    a_deleted_session_is_gone,
    a_host_path_stays_in_its_session,
    aopen_reaches_the_session_open_does,
    asessions_lists_what_sessions_does,
    adelete_removes_the_session,
)
