"""Turning a `SubagentSpec` into the `SubAgent` deepagents expects.

`domain.subagent` owns what a definition means, `subagent_store` finds the files
and `definitions` reads one; this resolves what a delegate actually runs with.
Each field a definition may narrow -- skills, tools, middleware, endpoint -- has
its own rule here, and every one of them shares a shape: a name nothing defines
is a mistake and raises, while a name the *request* did not activate is a caller
being narrower than the definition and is dropped.

Split out of `agent.py`, which was 657 lines doing four jobs. This was the
largest of them and the most self-contained: nothing in here calls anything in
`agent.py`, and `build_agent` is the only caller of anything in here.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from kingfisher.infrastructure.backend import SKILLS_SOURCES
from kingfisher.infrastructure.models import build_model
from kingfisher.infrastructure.scoping import CapabilityError, ScopedSkills, ToolAllowlist

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from kingfisher.config import Config
    from kingfisher.domain.subagent import SubagentSpec

#: The role every delegate runs as, for `Config.role_models`. One of `ROLES`,
#: which is what `from_env` populates -- a delegate's own name is not.
SUBAGENT_ROLE = "subagent"


def subagent_skills(
    spec: SubagentSpec, available: tuple[str, ...], activated: tuple[str, ...] | None
) -> tuple[str, ...] | None:
    """Which skills a delegate is told about, or `None` for none.

    Two different refusals, and the difference is the same one `build_agent`
    already draws for a request. A name nothing defines is a mistake in the
    definition and raises. A name that exists but this request did not activate
    is not a mistake -- it is a caller narrower than the definition -- so it is
    dropped, exactly as `Capabilities.intersect` drops it for the parent. A
    delegate cannot reach past the request that summoned it.
    """
    if spec.skills is None:
        return None
    unknown = tuple(name for name in spec.skills if name not in available)
    if unknown:
        msg = (
            f"subagent {spec.name!r} names unknown skill(s): {', '.join(unknown)}; "
            f"this request offers {available}"
        )
        raise CapabilityError(msg)
    if activated is None:
        return spec.skills
    return tuple(name for name in spec.skills if name in activated)


def _narrow_tools(
    declared: tuple[str, ...] | None, granted: tuple[str, ...] | None
) -> tuple[str, ...] | None:
    """What a delegate may use: its own declaration, capped by its caller's.

    `None` on either side means "no opinion", so the other wins -- the same
    rule `Capabilities.intersect` uses one level up, and for the same reason:
    narrowing must only ever subtract.
    """
    if declared is None:
        return granted
    if granted is None:
        return declared
    allowed = set(granted)
    return tuple(name for name in declared if name in allowed)


def subagent_middleware(
    spec: SubagentSpec,
    registry: Mapping[str, Callable[[], Any]],
    allowed: tuple[str, ...] | None,
) -> list[Any]:
    """Build the middleware a definition asked for, or refuse to.

    Two refusals, and both raise -- neither is the "caller was narrower" case
    that quietly drops a skill. A name nothing registered is a mistake in the
    definition. A name the deployment registered but did not *grant* is an
    escalation attempt or a misconfiguration, and running with silently less
    middleware than the definition specified could mean running without the
    rate limit or the audit hook it was written to have.

    Checked identically for a catalogue definition and an uploaded one.
    `Capabilities.including` widens skills and subagents for an upload because
    those are the caller's own text; a middleware name selects code the
    deployment wrote, so an upload gets no such exemption.
    """
    if not spec.middleware:
        return []

    unknown = tuple(name for name in spec.middleware if name not in registry)
    if unknown:
        msg = (
            f"subagent {spec.name!r} names unregistered middleware: {', '.join(unknown)}; "
            f"this deployment registered {tuple(registry)}"
        )
        raise CapabilityError(msg)

    if allowed is not None:
        ungranted = tuple(name for name in spec.middleware if name not in allowed)
        if ungranted:
            msg = (
                f"subagent {spec.name!r} names middleware this request may not use: "
                f"{', '.join(ungranted)}; permitted {allowed}"
            )
            raise CapabilityError(msg)

    return [registry[name]() for name in spec.middleware]


def _subagent_endpoint(
    spec: SubagentSpec, cfg: Config, allowed: tuple[str, ...] | None
) -> tuple[str | None, str | None]:
    """The (provider, model) a delegate runs as, or refuse to choose one.

    They move together. Overriding only the model, against a definition that
    pins `provider: openai`, would send a MiniMax model name to OpenAI -- a 404
    if you are lucky and a wrong-model run if you are not. Which endpoint runs
    which model should not be settled by two people who cannot see each other's
    half, so a half-override against a pinned provider is refused.

    An operator who overrides both has said what they mean and wins, which is
    the point of the override existing at all.
    """
    model_override = cfg.role_models.get(SUBAGENT_ROLE)
    provider_override = cfg.role_providers.get(SUBAGENT_ROLE)

    if model_override is not None and provider_override is None and spec.provider is not None:
        msg = (
            f"subagent {spec.name!r} pins provider {spec.provider!r}, but an operator "
            f"overrode only its model; set KINGFISHER_PROVIDER_SUBAGENT too, or neither"
        )
        raise CapabilityError(msg)

    provider = provider_override if provider_override is not None else spec.provider
    model = model_override if model_override is not None else spec.model

    if provider is not None and allowed is not None and provider not in allowed:
        msg = (
            f"subagent {spec.name!r} names endpoint {provider!r}, which this request "
            f"may not use; permitted {allowed}"
        )
        raise CapabilityError(msg)

    return provider, model


def as_subagent(  # noqa: PLR0913 -- one parameter per thing a definition may
    # narrow, each resolved by its own rule above. Bundling them would hide
    # which of those rules applied to a given delegate.
    spec: SubagentSpec,
    cfg: Config,
    *,
    providers: tuple[str, ...] | None = None,
    tools: tuple[str, ...] | None = None,
    backend: Any = None,
    skills: tuple[str, ...] | None = None,
    extra_middleware: list[Any] | None = None,
) -> dict[str, Any]:
    """Translate kingfisher's definition into deepagents' `SubAgent`.

    Every field maps directly except `tools`, `skills` and `middleware`.
    deepagents' `SubAgent.tools` is a sequence of tool *objects* it will
    register, not a selection from the ones the parent already has — handing it
    names raises inside `ToolNode`. The
    objects are built from the backend deep inside `create_deep_agent` and are
    not reachable here, so the restriction is applied the same way a request's
    own tool restriction is: a `ToolAllowlist` on the subagent's middleware,
    which selects by name and refuses anything else.
    """
    subagent: dict[str, Any] = {
        "name": spec.name,
        "description": spec.description,
        "system_prompt": spec.system_prompt,
    }
    middleware: list[Any] = []
    # The definition's own restriction, narrowed by the request's. A delegate
    # may never be offered more than whoever reached it: the parent's
    # `ToolAllowlist` sits on the parent's middleware, and a subagent inherits
    # none of it, so a request that withheld `execute` handed it straight to
    # any delegate. The ceiling has to be applied here to exist at all.
    ceiling = _narrow_tools(spec.tools, tools)
    if ceiling is not None:
        middleware.append(ToolAllowlist(ceiling))
    # A subagent inherits none of its parent's middleware, so an index it is
    # not given is an index it has no idea exists. `SubAgent.skills` would take
    # source *paths*; this selects by name, which is what a definition writes.
    if skills is not None and backend is not None:
        middleware.append(
            ScopedSkills(allowed=skills, backend=backend, sources=SKILLS_SOURCES)
        )
    # Last, so a deployment's middleware sees the tool and skill scoping
    # kingfisher applied rather than running ahead of it.
    middleware.extend(extra_middleware or [])
    if middleware:
        subagent["middleware"] = middleware

    # A *name* here would be resolved by deepagents' `init_chat_model`, which
    # infers its own provider and reads credentials from the environment --
    # around the provider table, the configured base_url, and the api_style
    # this deployment chose. It also re-enables the profile behaviour that
    # `infrastructure.models` exists to avoid. So we build the instance ourselves.
    #
    # `role_models` wins over the definition: which model a role runs on is an
    # operator's cost decision, and it should not require editing content.
    #
    # Keyed by *role*, not by this subagent's name. `from_env` populates
    # `role_models` from `KINGFISHER_MODEL_MAIN`, `_SUBAGENT` and `_SUMMARIZER`,
    # so a lookup by name only ever matched a delegate literally called one of
    # those -- the override above was documented, tested nowhere, and fired for
    # nothing. Per-delegate overrides would need `ROLES` to become unbounded and
    # its names to come from workspace content, which is a different decision.
    provider, model_id = _subagent_endpoint(spec, cfg, providers)
    if model_id is not None or provider is not None:
        # `replace` rather than a build_model parameter: an endpoint is exactly
        # the three Config fields a model is built from, so swapping them says
        # "build as if this deployment were pointed there" with nothing else
        # changed.
        endpoint = cfg.endpoint_for(provider)
        subagent["model"] = build_model(
            replace(
                cfg,
                model=model_id if model_id is not None else cfg.model,
                api_style=endpoint.api_style,
                base_url=endpoint.base_url,
                api_key=endpoint.api_key,
            )
        )
    return subagent
