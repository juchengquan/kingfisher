"""The two things kingfisher refuses of every backend a session opens, before a turn.

Both only look -- an `isinstance` and a scan of the routes, no I/O -- so running them on
every backend costs nothing. The rest of what a backend promises writes files and runs
commands, and is a deployment's to check in its own tests; this repository's are in
`tests/checks/`.

Running any at all is what stands in for a property this seam used to have for free. A
deployment handed the backend kingfisher built kept the route table and the confinement
by returning what it was given; one that builds its own starts from nothing, and a
backend deepagents gives no shell is the failure with no symptom at the end of that
road -- deepagents drops the shell, tells the model execution is unavailable, and says
nothing to the deployment.
"""

from __future__ import annotations

from typing import Any

from deepagents.backends import CompositeBackend
from deepagents.backends.protocol import SandboxBackendProtocol

from kingfisher.config import ConfigError
from kingfisher.layout import denied_read_scopes, denied_scopes


def executes(backend: Any) -> bool:
    """What deepagents asks before it offers the shell tool at all."""
    if isinstance(backend, CompositeBackend):
        return isinstance(backend.default, SandboxBackendProtocol)
    return isinstance(backend, SandboxBackendProtocol)


def not_executing(backend: Any) -> str | None:
    """Why deepagents would give this backend no shell, or `None` where it would.

    `isinstance`, not the methods present -- `SandboxBackendProtocol` is an abstract
    base class, so a backend that implements `execute` correctly and inherits nothing
    is treated as having none. Inherit `BaseSandbox` or register with the ABC;
    implementing the methods is not enough.
    """
    if executes(backend):
        return None
    return (
        f"{type(backend).__name__} is not recognised by deepagents as running "
        "commands, so the agent is given no shell and nothing says so. It has to "
        "be a SandboxBackendProtocol -- by inheritance, not by having the methods "
        "-- or a CompositeBackend whose `default` is one"
    )


def uncovered(backend: Any, scopes: tuple[str, ...] | None = None) -> str | None:
    """Which denied paths this backend routes nothing for, or `None` for none.

    `FilesystemMiddleware` refuses `permissions=` outright on a backend that executes
    unless every rule path sits under one of its routes -- so a read-only rule is only
    a legal sentence about a path something routes, and otherwise the graph will not
    build, with a message that never says kingfisher. `scopes` are the deny rules the
    backend will be given: the configured layout's at runtime, the module default when
    left out.
    """
    if not executes(backend):
        return None  # `not_executing` owns that failure; this would only repeat it
    if scopes is None:
        scopes = (*denied_scopes(), *denied_read_scopes())
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


def refuse_unusable_backend(backend: Any, scopes: tuple[str, ...] | None = None) -> None:
    """Refuse a backend deepagents would give no shell, or one routing too little.

    `ConfigError`, because this is a library refusing what it was handed. A deployment's
    own tests raise `AssertionError` for the same two failures, and out of a library that
    would read as the library's own bug.
    """
    for why in (not_executing(backend), uncovered(backend, scopes)):
        if why is not None:
            raise ConfigError(why)
