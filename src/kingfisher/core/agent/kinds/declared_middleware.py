"""Building the middleware a definition asked for, and saying when it displaces."""

from __future__ import annotations

import sys
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware import AgentMiddleware

from kingfisher.core.access import (
    CapabilityError,
    Selection,
    approved_middleware,
    approved_settings,
    refuse_unprovided_wants,
    refuse_written_wants,
)
from kingfisher.core.agent.models import SUMMARIZER, supplied_trigger

if TYPE_CHECKING:
    from collections.abc import Mapping

    from kingfisher.core.definitions.middlewares.catalogue import LocalMiddlewareRepository


#: `defaults` plus whatever the definition was allowed to write. Deliberately loose:
#: `Callable[[], Any]` does not cover a registered class, so the documented wiring would fail
#: to type-check while running correctly.
MiddlewareFactory = Callable[..., Any]


@dataclass(frozen=True)
class ByName:
    """A want a definition may choose by name, and what it falls back to.

    Only a wrapped want is writable: `backend` has no name a file could carry, so a value
    written for it is refused; a model has one, and `resolve` says what it means.
    """

    fallback: Any
    #: Called with the name written and who wrote it: the file's owner reads the refusal.
    resolve: Callable[[str, str], Any]


def offered_middleware(
    registered: Mapping[str, Any], workspace: LocalMiddlewareRepository
) -> dict[str, Any]:
    """Both sources of middleware, as the one mapping a definition selects from.

    Merged once, so an agent and its subagents select from the same thing. A name in both
    is refused, not qualified as a tool clash is: both are the deployment's, so it can rename.
    """
    offered = workspace.classes
    if clashing := sorted(set(registered) & set(offered)):
        names = ", ".join(repr(name) for name in clashing)
        msg = (
            f"middleware {names} is both registered by this deployment and defined "
            f"in its definitions folder. A definition names one and there is nothing to tell "
            f"them apart, so rename the class or its registry key"
        )
        raise CapabilityError(msg)
    return {**offered, **registered}


def declared_middleware(  # noqa: PLR0913 -- `chosen_by` only for a portable subagent
    spec: Any,
    registry: Mapping[str, MiddlewareFactory],
    allowed: Selection,
    *,
    kind: str,
    provisions: Mapping[str, Any] | None = None,
    chosen_by: str | None = None,
) -> list[Any]:
    """Build the middleware a definition asked for, agent or subagent.

    `provisions` fills a class's `wants`; it defaults to nothing so a caller with no graph
    around it can still build a middleware that wants none. `chosen_by` names who chose
    `spec.middlewares` when its definition did not.
    """
    subject = f"{kind} {spec.name!r}"
    approved = approved_middleware(
        spec.middlewares,
        registered=registry,
        granted=allowed,
        # Whoever chose the names is who a refusal of one sends the reader to.
        subject=subject if chosen_by is None else f"{chosen_by}, choosing for {subject},",
    )
    wrote = spec.middleware_settings
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

    Filled in where the agent is assembled, the only place that knows (the model this graph
    runs, say): none is a scalar a yaml file could hold, and a class closing over one would
    get the deployment's answer, not this build's. What may be wanted is whatever the call
    site holds: a list here would be a second place to be wrong.
    """
    wants = tuple(getattr(entry, "wants", ()) or ())
    if not wants:
        return written
    refuse_unprovided_wants(
        wants, provided=tuple(provisions), subject=subject, registered_as=registered_as
    )
    # Decided here, not in `core.access`: it is a fact about the objects this build holds.
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
            # As a name, whatever was written: `resolve` refuses `model: 5` as it does a typo.
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

    A class takes settings: its `defaults`, then what the definition wrote, as far as
    `yaml_settable` allows. A wanted key falls back to what the harness holds, and a name
    written over it is resolved. Anything else is a zero-argument factory, taking no settings.
    """
    if not callable(entry):
        # Reached through `build_agent`, which takes a registry directly and so skips
        # `refuse_unbuildable_middleware`; `entry()` would raise naming neither.
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
            # Refused, not ignored: a dropped want builds a middleware without the model it
            # was written for, which fails later and somewhere else.
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
    # Copied, so the merge cannot mutate a class attribute and carry one definition's
    # setting into the next agent built from the same registry.
    written = {**dict(getattr(entry, "defaults", None) or {}), **approved}
    arguments = _wanted(
        entry, written, provisions, registered_as=registered_as, subject=subject
    )
    try:
        return entry(**arguments)
    except TypeError as exc:
        # `defaults` missing a required argument: the registry's mistake, which no
        # definition can cause or fix, so it names the entry and what it was given.
        given = ", ".join(sorted(arguments)) or "no arguments"
        msg = (
            f"{subject} could not build middleware {registered_as!r}: "
            f"{entry.__name__} was called with {given} and refused -- {exc}. A "
            f"registered class is called with its own `defaults`, whatever the "
            f"definition was allowed to write, and whatever it named in `wants`, so "
            f"every argument it requires belongs in one of those"
        )
        raise CapabilityError(msg) from exc


#: The two deepagents will not run without, by the `.name` each answers to, and the
#: job whatever replaces one has to do as well.
REQUIRED_BY_DEEPAGENTS = {
    "FilesystemMiddleware": (
        "it backs every built-in file tool, enforces the `permissions` rules, and leaves "
        "tool-result offloading off -- kingfisher saves every result itself, in the one "
        "folder a ledger row names"
    ),
    "SubAgentMiddleware": (
        "it backs the `task` tool, through which the agent hands work to a subagent"
    ),
}


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

    job = REQUIRED_BY_DEEPAGENTS.get(name)
    weight = (
        f" {name} is also one of the two deepagents refuses to run without, so "
        f"whatever replaces it has to do that job as well -- unreplaced, {job}."
        if job
        else ""
    )
    if name == SUMMARIZER and supplied_trigger(instance) is None:
        weight = (
            " kingfisher reads the size a summariser fires at off deepagents' or "
            "langchain's trigger, and this one is neither or fires at no size, so the "
            "PDFs a turn may hold and where results are cleared are sized as if "
            "deepagents' own still ran."
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
