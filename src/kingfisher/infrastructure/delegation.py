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

from kingfisher.domain.capabilities import (
    CapabilityError,
    approved_middleware,
    narrowed,
)
from kingfisher.domain.subagent import resolved_endpoint
from kingfisher.infrastructure.backend import SKILLS_SOURCES
from kingfisher.infrastructure.models import build_model
from kingfisher.infrastructure.prompting import with_user_prompt
from kingfisher.infrastructure.scoping import ScopedSkills, ToolAllowlist

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
    dropped, by the same `narrowed` that drops it for the parent. A delegate
    cannot reach past the request that summoned it.

    That last sentence used to describe a resemblance: the dropping was two
    lines here, equal to `narrowed` across every input pair, which is a third
    copy of one rule. Only the refusal is this function's own -- `narrowed` has
    no opinion about what exists, and should not.
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
    return narrowed(spec.skills, by=activated)


def subagent_middleware(
    spec: SubagentSpec,
    registry: Mapping[str, Callable[[], Any]],
    allowed: tuple[str, ...] | None,
) -> list[Any]:
    """Build the middleware a definition asked for.

    Which names it may have is `capabilities.approved_middleware`, and that is
    the whole of the rule -- two refusals, both raising, neither the "caller was
    narrower" case that quietly drops a skill. This half is the part that needs
    the registry: an approved name is still only a name until something calls
    the factory behind it.

    Split that way because the decision is expressible in kingfisher's own
    vocabulary and the construction is not. `Capabilities.middleware` and
    `SubagentSpec.middleware` are both name lists; only the objects are ours.
    """
    approved = approved_middleware(
        spec.middleware,
        registered=registry,
        granted=allowed,
        subject=f"subagent {spec.name!r}",
    )
    return [registry[name]() for name in approved]


def _subagent_endpoint(
    spec: SubagentSpec, cfg: Config, allowed: tuple[str, ...] | None
) -> tuple[str | None, str | None]:
    """Read the operator's overrides out of `Config`, and let the domain decide.

    The whole of this layer's part is knowing that a role's overrides live at
    `role_models[SUBAGENT_ROLE]` and `role_providers[SUBAGENT_ROLE]`. That the
    pair is atomic, and that a pinned provider must be one the request may use,
    is `subagent.resolved_endpoint` -- a rule about kingfisher's own vocabulary,
    which happens to need two values a deployment supplies.
    """
    return resolved_endpoint(
        spec,
        model_override=cfg.role_models.get(SUBAGENT_ROLE),
        provider_override=cfg.role_providers.get(SUBAGENT_ROLE),
        granted=allowed,
    )


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
        # Its own procedure, then the workspace's own instructions. A house
        # rule written in `PROMPT.md` reached the main agent and nothing it
        # delegated to, so delegated work quietly escaped it.
        "system_prompt": with_user_prompt(spec.system_prompt, cfg.workspace),
    }
    middleware: list[Any] = []
    # The definition's own restriction, narrowed by the request's -- by the
    # domain's rule, not a copy of it. A delegate may never be offered more than
    # whoever reached it: the parent's `ToolAllowlist` sits on the parent's
    # middleware, and a subagent inherits none of it, so a request that withheld
    # `execute` handed it straight to any delegate. The ceiling has to be
    # applied here to exist at all; deciding what it *is* does not belong here.
    ceiling = narrowed(spec.tools, by=tools)
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
    # `role_models` from `KINGFISHER_MODEL_SUBAGENT`, so a lookup by name only
    # ever matched a delegate literally called that -- the override above was
    # documented, tested nowhere, and fired for nothing. Per-delegate overrides
    # would need `ROLES` to become unbounded and its names to come from
    # workspace content, which is a different decision.
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
