"""The two things kingfisher refuses of every backend a session opens, before a turn.

Both only look, with no I/O, so they cost nothing on every backend; the rest of what a
backend promises is a deployment's to check in its own tests. A backend deepagents
gives no shell is the failure with no symptom: the model is told execution is
unavailable, and the deployment is told nothing.
"""

from __future__ import annotations

from typing import Any

from deepagents.backends import CompositeBackend
from deepagents.backends.protocol import SandboxBackendProtocol

from kingfisher.core.config import ConfigError
from kingfisher.core.layout import Layout


def executes(backend: Any) -> bool:
    """What deepagents asks before it offers the shell tool at all."""
    if isinstance(backend, CompositeBackend):
        return isinstance(backend.default, SandboxBackendProtocol)
    return isinstance(backend, SandboxBackendProtocol)


def why_no_shell(backend: Any) -> str | None:
    """Why deepagents would give this backend no shell, or `None` where it would.

    `isinstance`, not the methods present: `SandboxBackendProtocol` is an abstract base
    class, so a backend must inherit `BaseSandbox` or register with the ABC.
    """
    if executes(backend):
        return None
    return (
        f"{type(backend).__name__} is not recognised by deepagents as running "
        "commands, so the agent is given no shell and nothing says so. It has to "
        "be a SandboxBackendProtocol -- by inheritance, not by having the methods "
        "-- or a CompositeBackend whose `default` is one"
    )


def unrouted_denials(backend: Any, layout: Layout) -> str | None:
    """Which paths `layout` denies that this backend routes nothing for, or `None`.

    `FilesystemMiddleware` refuses `permissions=` on an executing backend unless every
    rule path sits under a route, with a message that never says kingfisher. The layout
    has no default: the shipped one's paths are the wrong ones for a renamed deployment.
    """
    if not executes(backend):
        return None  # `why_no_shell` owns that failure; this would only repeat it
    scopes = (*layout.denied_scopes(), *layout.denied_read_scopes())
    routes = tuple(backend.routes) if isinstance(backend, CompositeBackend) else ()
    missing = [scope for scope in scopes if not any(scope.startswith(r) for r in routes)]
    if not missing:
        return None
    return (
        f"{type(backend).__name__} routes nothing covering {sorted(set(missing))}, "
        "and kingfisher denies writes or reads under each of them. deepagents "
        "refuses permissions on an executing backend unless every rule sits under "
        f"a route, so the graph will not build. Routed: {sorted(routes)}"
    )


def refuse_unusable_backend(backend: Any, layout: Layout) -> None:
    """Refuse a backend deepagents would give no shell, or one routing too little.

    `ConfigError`, not `AssertionError`: out of a library, that would read as its own bug.
    """
    for why in (why_no_shell(backend), unrouted_denials(backend, layout)):
        if why is not None:
            raise ConfigError(why)
