"""Building the middleware a definition asked for, and saying when it displaces.

Its own module because it answers to a registry rather than to a graph. Assembly
calls it once and passes the result on; nothing else here calls into it, and it
calls nothing here.

It also holds the one piece of genuine deepagents archaeology in this package.
`create_deep_agent` merges middleware *by name*, so a deployment's class called
`FilesystemMiddleware` removes deepagents' own from the stack rather than running
beside it -- and upstream protects two of them on one path and not the other.
Reading their names off their own modules is how that warning stays true across
an upgrade instead of going stale in a hand-written list.
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
#: It was `Callable[[], Any]`, which stopped being true the moment a class
#: could be registered -- and stopped being *checkable* in the same moment: a
#: deployment pasting the wiring block `call_cap.py` documents got a type error
#: on its own registry while the code it described ran correctly. A signature
#: narrower than the contract is worse than a loose one, because the reader who
#: believes it is the one following the docs.
MiddlewareFactory = Callable[..., Any]


def declared_middleware(
    spec: Any,
    registry: Mapping[str, MiddlewareFactory],
    allowed: Selection,
    *,
    kind: str,
) -> list[Any]:
    """Build the middleware a definition asked for, agent or delegate.

    Which names it may have is `approved_middleware`, and that is the whole of
    the rule -- two refusals, both raising, neither the "caller was narrower"
    case that quietly drops a skill. This half is the part that needs the
    registry: an approved name is still only a name until something calls the
    factory behind it.

    Split that way because the decision is expressible in kingfisher's own
    vocabulary and the construction is not. `Capabilities.middleware`,
    `AgentSpec.middleware` and `SubagentSpec.middleware` are all name lists;
    only the objects are ours.

    Duck-typed on `.name` and `.middleware` rather than taking a union, because
    the two specs share those two fields and nothing else here reads any other.
    `kind` is required and has no default: it is the word the refusal uses, and
    a delegate's refusal that said "agent" would send the reader to the wrong
    file.

    It was `subagent_middleware` in `delegation.py`, which was the right home
    while delegates were the only definitions whose middleware was ever built.
    An agent file has carried the field since `agents-as-definitions` and it
    reached nothing -- so the function moved to the module that assembles both,
    rather than an agent's wiring reaching into the delegates'.
    """
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


def _instantiate(
    entry: Any, wrote: Mapping[str, object], *, registered_as: str, subject: str
) -> Any:
    """One registry entry, built into the middleware it stands for.

    Two shapes, because a registry has held one of them since before settings
    existed and breaking every deployment that wrote one would be a poor trade
    for a field most definitions will never use.

    A **class** is the shape that can be configured. `defaults` is what the
    deployment supplies, the settings a definition wrote are laid over the top,
    and `yaml_settable` on the class decides which of those it was allowed to
    write. Deployment first and definition second is the whole precedence rule:
    the registry holds the value that applies when nobody says otherwise, and a
    definition overrides it only where the class said it may.

    Anything else is a **zero-argument factory**, which is what a registry
    entry used to be and still may be. It takes no settings and cannot be
    given any -- there is no seam to pass them through, since whatever values
    it uses were closed over when the deployment wrote the lambda.

    Which is why a definition writing settings for one is refused rather than
    built without them. A factory that quietly ignored a `settings:` block
    would be the exact failure `approved_settings` exists to prevent, one layer
    down and harder to see: the file says a value and the object does not have
    it.
    """
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
#:
#: Not a ban. Replacing either is a deployment's business -- a fence of one's own
#: over the filesystem is a reasonable thing to build, and refusing it here would
#: be kingfisher inventing a policy deepagents does not have. What these two buy
#: is a louder sentence in the warning below, because the consequence of getting
#: one wrong is not a missing hook but an agent whose file tools no longer
#: enforce `permissions`.
#:
#: Written here rather than imported from `_REQUIRED_MIDDLEWARE_NAMES`, which is
#: private: an upstream rename would otherwise change what this says with nobody
#: deciding, and `test_the_required_names_match_deepagents` fails loudly instead.
REQUIRED_BY_DEEPAGENTS = ("FilesystemMiddleware", "SubAgentMiddleware")


@cache
def _deepagents_middleware_names() -> frozenset[str]:
    """Every name deepagents' own middleware answers to, read off its modules.

    Discovered rather than listed, because a list would be wrong the first time
    upstream adds a middleware and nobody here noticed -- and a notice that has
    silently stopped covering half the stack is worse than none.

    Across the package rather than `graph.py`'s namespace alone, which was the
    first version and missed three: `SummarizationMiddleware` is reached there
    through `create_summarization_middleware`, so the factory is imported and
    the class it returns is not. A name-collision notice blind to the summarizer
    would have been exactly the half-covering kind.

    `deepagents.graph` is imported first because importing it is what pulls the
    submodules in; walking `sys.modules` without that would depend on what
    somebody else happened to import.

    Cached because it walks every loaded module and the answer cannot change
    inside a process.
    """
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
    """Say when a deployment's middleware displaces one of deepagents' own.

    `create_deep_agent` does not append what it is handed. It merges by name --
    `_apply_custom_middleware`, which replaces in place when a custom
    middleware's `.name` matches something already in the base stack. And
    `AgentMiddleware.name` defaults to the class name, while the registry key a
    definition reaches a middleware by is a different string entirely, so
    nothing on either side of that would otherwise mention it.

    A warning rather than a refusal, because the substitution is a legitimate
    thing to deploy: a filesystem middleware of one's own, a summarizer that
    truncates differently. What is not legitimate is doing it *by accident*,
    which is the whole failure mode here -- the two names collide because
    somebody picked an obvious class name, not because they meant to replace
    anything. A deployment that meant it reads this once and moves on.

    Through `warnings.warn` rather than a logger, which is what
    `model_catalogue` does for the same shape of thing: a deployment's own
    configuration is probably not what it meant, said once, fatal to nothing.
    """
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
