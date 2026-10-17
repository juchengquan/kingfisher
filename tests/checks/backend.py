"""What a backend `Sessions.open` returns promises, as checks.

Not shipped: kingfisher holds its own backend to these, and a deployment writing its own
copies them. Plain functions raising `AssertionError` and no pytest, so a suite of any
kind can run them. The first two are also what kingfisher refuses at startup, in
`usable_backend`; the rest write files and run commands, which a library should not do
at a build.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

from deepagents.backends import CompositeBackend

from kingfisher.plugins import DEFAULT_LAYOUT, HostPathError

if TYPE_CHECKING:
    from collections.abc import Callable


from kingfisher.core.agent.usable_backend import executes, not_executing, uncovered


def execution_support(make: Callable[[], Any]) -> None:
    """The one failure with no symptom: deepagents drops `execute` and tells nobody."""
    if (why := not_executing(make())) is not None:
        raise AssertionError(why)


def route_coverage(make: Callable[[], Any], scopes: tuple[str, ...] | None = None) -> None:
    """Otherwise the graph will not build, with a message that never says kingfisher."""
    if (why := uncovered(make(), scopes)) is not None:
        raise AssertionError(why)


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
    if not executes(backend):
        return
    body, name = uuid4().hex, uuid4().hex
    for virtual, write in (
        (f"{DEFAULT_LAYOUT.outputs_route}{name}-tools.txt", lambda path: backend.write(path, body)),
        (
            f"{DEFAULT_LAYOUT.outputs_route}{name}-shell.txt",
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


def offloads_in_the_scratchpad(make: Callable[[], Any]) -> None:
    """deepagents' own files land where `system.md` tells the agent they are.

    A tool result too long for a message, and the history the summarizer keeps, go
    under the backend's `artifacts_root`, which defaults to `/`. `WorkspaceScopedBackend`
    sets it, so a subclass passes; a `CompositeBackend` assembled without it puts both
    folders at the session root, where the agent is never sent to search. Read the way
    deepagents reads it, `isinstance` included, because a backend that is no
    `CompositeBackend` is `/` whatever attribute it carries.
    """
    backend = make()
    root = backend.artifacts_root if isinstance(backend, CompositeBackend) else "/"
    scratch = DEFAULT_LAYOUT.scratch_route
    if root.rstrip("/") != scratch.rstrip("/"):
        msg = (
            f"{type(backend).__name__} has deepagents write the tool results it evicts "
            f"and the history it summarizes under {root!r}, and the agent is told they "
            f"are under {scratch}. Pass artifacts_root={scratch!r} to the "
            "CompositeBackend, as the one backend_at builds does"
        )
        raise AssertionError(msg)


def shell_denied(make: Callable[[], Any]) -> None:
    """The agent's shell may not write under `/.harness` or `/inputs`.

    `/.harness` is what kingfisher reads back and trusts -- the pinned agent, the
    conversation, a paused turn -- and `/inputs` is the caller's input, which the agent
    is promised it cannot change. The file tools are refused both by the turn's
    permissions; the shell bypasses those, so on any backend but kingfisher's own this
    is the only thing that says the shell is refused too.

    Driven through `execute` rather than read off the backend's configuration, and
    both ways a shell writes: a new file, and over one kingfisher put there. The
    second is the one a pinned agent is rewritten by.

    **With the mode bits lifted first**, which is what this missed. kingfisher's own
    backend kept `/inputs` by dropping the write bits, and the shell runs as the uid
    that owns those files -- so a plain write was refused, this passed, and
    `chmod u+w` then the same write replaced the caller's file. A deployment author
    was told the shell was refused a folder it could take in two commands. The `chmod`
    is allowed to fail: on a backend that has none, or where something else refuses
    it, what is asserted is still only the file afterwards.
    """
    backend = make()
    if not executes(backend):
        return
    token = uuid4().hex
    written: list[str] = []
    for route, what in (
        (DEFAULT_LAYOUT.harness_route, "what kingfisher reads back and trusts"),
        (DEFAULT_LAYOUT.inputs_route, "the caller's input"),
    ):
        kept, fresh = f"{route}{token}-kept", f"{route}{token}-fresh"
        backend.upload_files([(kept, b"as written")])
        here = route.strip("/")
        for path in (kept, fresh):
            inside = path.lstrip("/")
            backend.execute(
                f"chmod u+w {here} {inside} 2>/dev/null; printf %s {token} > {inside}"
            )
        after_kept, after_fresh = backend.download_files([kept, fresh])
        if after_kept.content != b"as written" or after_fresh.error is None:
            written.append(f"{route.rstrip('/')}, {what}")
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
            "kingfisher.plugins.HostPathError, or do not "
            f"refuse. Got: {refused}"
        )
        raise AssertionError(msg) from refused


#: Every check a backend a deployment's `open` returned must pass, in the order a
#: deployment wants to read them: the silent failure first, then the one that stops
#: the graph building, then the promises the prompt makes, then what the shell must
#: not write, then the refusal.
BACKEND_CHECKS: tuple[Callable[[Callable[[], Any]], None], ...] = (
    execution_support,
    route_coverage,
    filesystem_consistency,
    offloads_in_the_scratchpad,
    shell_denied,
    host_path_refusal,
)


