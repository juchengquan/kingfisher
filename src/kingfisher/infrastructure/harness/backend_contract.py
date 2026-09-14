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

from typing import TYPE_CHECKING, Any
from uuid import uuid4

from deepagents.backends import CompositeBackend
from deepagents.backends.protocol import SandboxBackendProtocol

from kingfisher.infrastructure.harness.backend import HostPathError
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
            "kingfisher.infrastructure.harness.backend.HostPathError, or do not "
            f"refuse. Got: {refused}"
        )
        raise AssertionError(msg) from refused


#: Every check a backend returned from `backend_from` must pass, in the order a
#: deployment wants to read them: the silent failure first, then the one that stops
#: the graph building, then the promise the prompt makes, then the refusal.
BACKEND_CONTRACT: tuple[Callable[[Callable[[], Any]], None], ...] = (
    execution_support,
    route_coverage,
    filesystem_consistency,
    host_path_refusal,
)
