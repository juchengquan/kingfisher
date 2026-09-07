"""Building the middleware a definition asked for, and saying when it displaces.

Its own module because it answers to a registry rather than to a graph. Assembly
calls it once and passes the result on; nothing else here calls into it, and it calls
nothing here.
"""

from __future__ import annotations

import sys
import warnings
from collections.abc import Callable
from functools import cache
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware import AgentMiddleware

from kingfisher.domain.capabilities import (
    CapabilityError,
    Selection,
    approved_middleware,
    approved_settings,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


#: `defaults` plus whatever the definition was allowed to write.
#:
#: Deliberately loose. Narrowed to `Callable[[], Any]` it stopped covering a
#: registered class, so a deployment pasting the wiring block `call_cap.py`
#: documents got a type error on its own registry while the code ran correctly --
#: and a signature narrower than the contract is worse than a loose one, because
#: the reader who believes it is the one following the docs.
MiddlewareFactory = Callable[..., Any]


def declared_middleware(
    spec: Any,
    registry: Mapping[str, MiddlewareFactory],
    allowed: Selection,
    *,
    kind: str,
) -> list[Any]:
    """Build the middleware a definition asked for, agent or delegate."""
    subject = f"{kind} {spec.name!r}"
    approved = approved_middleware(
        spec.middleware,
        registered=registry,
        granted=allowed,
        subject=subject,
    )
    # Absent on a spec built in code rather than parsed, and on every spec that
    # predates the field. `{}` is the same answer either way: nothing was
    # written, so every name is built on what the deployment registered.
    wrote = getattr(spec, "middleware_settings", None) or {}
    built = []
    for name in approved:
        instance = _instantiate(
            registry[name],
            wrote.get(name) or {},
            registered_as=name,
            subject=subject,
        )
        _warn_if_it_replaces_deepagents(instance, registered_as=name, subject=subject)
        built.append(instance)
    return built


def _described(entry: Any) -> str:
    """What was registered, in words a deployment can match against its own code."""
    if isinstance(entry, AgentMiddleware):
        return f"a built {type(entry).__name__} rather than the class"
    return f"a {type(entry).__name__}, which cannot be called"


def _uncallable(entry: Any, *, registered_as: str, subject: str | None = None) -> str:
    """Why an entry cannot be built, and what to register instead."""
    opening = (
        f"{subject} names middleware {registered_as!r}, which this deployment registered as"
        if subject is not None
        else f"middleware {registered_as!r} is registered as"
    )
    return (
        f"{opening} {_described(entry)}. Register the class itself, or a "
        f"zero-argument factory returning one: a middleware is built again for "
        f"every graph, so a single shared object would carry one graph's state "
        f"into the next and one turn's into the turn after"
    )


def refuse_unbuildable_middleware(registry: Mapping[str, Any]) -> None:
    """Refuse a registry entry nothing can build, as the registry arrives."""
    for name, entry in registry.items():
        if not callable(entry):
            raise CapabilityError(_uncallable(entry, registered_as=name))


def _instantiate(
    entry: Any, wrote: Mapping[str, object], *, registered_as: str, subject: str
) -> Any:
    """One registry entry, built into the middleware it stands for.

    A **class** is the shape that can be configured. `defaults` is what the
    deployment supplies, the settings a definition wrote are laid over the top, and
    `yaml_settable` on the class decides which of those it was allowed to write.
    Deployment first and definition second is the whole precedence rule.

    Anything else is a **zero-argument factory**. It takes no settings and cannot be
    given any -- there is no seam to pass them through, since whatever values it uses
    were closed over when the deployment wrote the lambda.
    """
    if not callable(entry):
        # Reached when `Kingfisher` was not the door -- `build_agent` takes a
        # registry directly. `refuse_unbuildable_middleware` catches this at
        # construction for everyone who comes the ordinary way; this is what
        # stops the other path raising `TypeError: 'X' object is not callable`
        # out of the `entry()` below, which named neither the entry nor the
        # definition that asked for it.
        raise CapabilityError(_uncallable(entry, registered_as=registered_as, subject=subject))
    if not isinstance(entry, type):
        if wrote:
            msg = (
                f"{subject} writes settings for middleware {registered_as!r}, which "
                f"this deployment registered as a factory taking no arguments. Only "
                f"a registered *class* takes settings -- it declares what it accepts "
                f"in `yaml_settable` and what it falls back to in `defaults`; a "
                f"factory has already chosen its values and there is nowhere to put "
                f"these"
            )
            raise CapabilityError(msg)
        return entry()

    approved = approved_settings(
        wrote,
        settable=getattr(entry, "yaml_settable", ()) or (),
        subject=subject,
        registered_as=registered_as,
    )
    # `defaults` is the deployment's half and is copied rather than passed, so a
    # class attribute cannot be mutated by the merge and carry one definition's
    # setting into the next agent built from the same registry.
    arguments = {**dict(getattr(entry, "defaults", None) or {}), **approved}
    try:
        return entry(**arguments)
    except TypeError as exc:
        # The registry's mistake rather than the definition's, so it says which
        # entry and what it was given. Reached when `defaults` does not cover
        # the arguments the class actually requires -- which no definition can
        # cause and no definition can fix.
        given = ", ".join(sorted(arguments)) or "no arguments"
        msg = (
            f"{subject} could not build middleware {registered_as!r}: "
            f"{entry.__name__} was called with {given} and refused -- {exc}. A "
            f"registered class is called with its own `defaults` plus whatever the "
            f"definition was allowed to write, so every argument it requires "
            f"belongs in `defaults`"
        )
        raise CapabilityError(msg) from exc


#: The two deepagents will not run without, by the `.name` each answers to.
REQUIRED_BY_DEEPAGENTS = ("FilesystemMiddleware", "SubAgentMiddleware")


@cache
def _deepagents_middleware_names() -> frozenset[str]:
    """Every name deepagents' own middleware answers to, read off its modules."""
    import deepagents.graph  # noqa: F401, PLC0415  -- loads the submodules walked below

    return frozenset(
        obj.__name__
        for name, module in list(sys.modules.items())
        if name.startswith("deepagents") and module is not None
        for obj in vars(module).values()
        if isinstance(obj, type)
        and issubclass(obj, AgentMiddleware)
        and obj is not AgentMiddleware
    )


def _warn_if_it_replaces_deepagents(
    instance: Any, *, registered_as: str, subject: str
) -> None:
    """Say when a deployment's middleware displaces one of deepagents' own."""
    name = getattr(instance, "name", None)
    if name not in _deepagents_middleware_names():
        return

    weight = (
        f" {name} is also one of the two deepagents refuses to run without, so "
        f"whatever replaces it has to do that job as well -- unreplaced, it backs "
        f"every built-in file tool and enforces the `permissions` rules."
        if name in REQUIRED_BY_DEEPAGENTS
        else ""
    )
    warnings.warn(
        f"{subject} runs middleware registered as {registered_as!r} whose class is "
        f"named {name!r}, which is a name deepagents uses for its own. deepagents "
        f"merges by name, so this replaces its {name} in place rather than running "
        f"beside it.{weight} If that is what you meant, there is nothing to do; if "
        f"it is not, rename the class -- the registry key it is reached by is "
        f"separate and can stay as it is.",
        stacklevel=2,
    )
