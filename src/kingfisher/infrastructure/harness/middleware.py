"""Building the middleware a definition asked for, and saying when it displaces."""

from __future__ import annotations

import sys
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware import AgentMiddleware

from kingfisher.domain.capabilities import (
    CapabilityError,
    Selection,
    approved_middleware,
    approved_settings,
    refuse_unprovided_wants,
    refuse_written_wants,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from kingfisher.domain.ports import MiddlewareRepository


#: `defaults` plus whatever the definition was allowed to write.
#:
#: Deliberately loose. Narrowed to `Callable[[], Any]` it stopped covering a
#: registered class, so a deployment pasting the wiring block `call_cap.py`
#: documents got a type error on its own registry while the code ran correctly --
#: and a signature narrower than the contract is worse than a loose one, because
#: the reader who believes it is the one following the docs.
MiddlewareFactory = Callable[..., Any]


@dataclass(frozen=True)
class ByName:
    """A want a definition may choose by name, and what it falls back to.

    Wrapping is what makes a want writable at all. `backend` is an object with no
    name a file could carry, so a value written for it is refused; a model has one,
    and `resolve` is what that name means -- the catalogue lookup, the endpoint the
    request may or may not reach, and the instance built from the profile.
    """

    fallback: Any
    #: Called with the name a definition wrote and a subject naming who wrote it --
    #: the refusal it raises is read by whoever owns that file, not by the harness.
    resolve: Callable[[str, str], Any]


def offered_middleware(
    registered: Mapping[str, Any], workspace: MiddlewareRepository
) -> dict[str, Any]:
    """Both sources of middleware, as the one mapping a definition selects from.

    Merged once, so an agent and its delegates select from the same thing. A name
    in both is refused rather than resolved: both belong to the same deployment,
    so renaming is available -- unlike a tool clash between two vendors, which is
    why that one is qualified instead.
    """
    offered = workspace.classes
    if clashing := sorted(set(registered) & set(offered)):
        names = ", ".join(repr(name) for name in clashing)
        msg = (
            f"middleware {names} is both registered by this deployment and defined "
            f"in its workspace. A definition names one and there is nothing to tell "
            f"them apart, so rename the class or its registry key"
        )
        raise CapabilityError(msg)
    return {**offered, **registered}


def declared_middleware(
    spec: Any,
    registry: Mapping[str, MiddlewareFactory],
    allowed: Selection,
    *,
    kind: str,
    provisions: Mapping[str, Any] | None = None,
) -> list[Any]:
    """Build the middleware a definition asked for, agent or delegate.

    `provisions` is what this build holds for a class that declared `wants` -- see
    `_wanted`. Defaulted to nothing rather than required, so a caller with no graph
    around it can still build a middleware that wants none.
    """
    subject = f"{kind} {spec.name!r}"
    approved = approved_middleware(
        spec.middlewares,
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
            provisions=provisions or {},
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


def _wanted(
    entry: type,
    written: dict[str, Any],
    provisions: Mapping[str, Any],
    *,
    registered_as: str,
    subject: str,
) -> dict[str, Any]:
    """`written`, with every key the class declared in `wants` replaced by an object.

    A want is filled in where the agent is assembled, because that is the only place
    that knows -- the model this graph runs, say. None of what belongs here is a
    scalar, so none of it can come out of a yaml file, and a class closing over one
    instead would close over the deployment's answer rather than this build's.

    What may be wanted is whatever the call site holds, deliberately: a list here
    would be a second place to edit and a place to be wrong.
    """
    wants = tuple(getattr(entry, "wants", ()) or ())
    if not wants:
        return written
    refuse_unprovided_wants(
        wants, provided=tuple(provisions), subject=subject, registered_as=registered_as
    )
    # Which wants may be written is decided here rather than in the domain because it
    # is a fact about the objects this build is holding, not about a list of names.
    whole = tuple(name for name in wants if not isinstance(provisions[name], ByName))
    refuse_written_wants(
        whole,
        written=(*(getattr(entry, "yaml_settable", ()) or ()), *written),
        subject=subject,
        registered_as=registered_as,
    )

    named = set(wants)
    arguments = {key: value for key, value in written.items() if key not in named}
    for name in wants:
        held = provisions[name]
        if not isinstance(held, ByName):
            arguments[name] = held
        elif name in written:
            # Whatever the file wrote, as the name it was meant to be. `resolve`
            # refuses an unknown one and quotes what it read, which is the answer
            # `model: 5` wants as much as a misspelling is.
            arguments[name] = held.resolve(
                str(written[name]), f"middleware {registered_as!r} on {subject}"
            )
        else:
            arguments[name] = held.fallback
    return arguments


def _instantiate(
    entry: Any,
    wrote: Mapping[str, object],
    *,
    registered_as: str,
    subject: str,
    provisions: Mapping[str, Any],
) -> Any:
    """One registry entry, built into the middleware it stands for.

    A **class** is the shape that can be configured. `defaults` is what the
    deployment supplies, the settings a definition wrote are laid over the top, and
    `yaml_settable` on the class decides which of those it was allowed to write.
    Deployment first and definition second is the whole precedence rule, and `wants`
    sits underneath both: a wanted key falls back to what the harness holds, and a
    name written over it in either half is resolved rather than passed through.

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
        if getattr(entry, "wants", None):
            # A callable object that declared `wants` and is not a class. Refused
            # rather than ignored: a want silently dropped is a middleware built
            # without the model it was written to use, which fails later and
            # somewhere else.
            msg = (
                f"{subject} names middleware {registered_as!r}, which declares `wants` "
                f"and was registered as a factory taking no arguments. Only a "
                f"registered *class* is handed anything -- a factory is called with "
                f"nothing, and there is nowhere for these to go"
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
    written = {**dict(getattr(entry, "defaults", None) or {}), **approved}
    arguments = _wanted(
        entry, written, provisions, registered_as=registered_as, subject=subject
    )
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
            f"registered class is called with its own `defaults`, whatever the "
            f"definition was allowed to write, and whatever it named in `wants`, so "
            f"every argument it requires belongs in one of those"
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
