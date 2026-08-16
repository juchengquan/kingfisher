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

from deepagents.middleware import SubAgentMiddleware

from kingfisher.domain.capabilities import (
    ALL,
    CapabilityError,
    Selection,
    approved_middleware,
    belongs_in,
    narrowed,
)
from kingfisher.domain.subagent import RunOn, resolved_endpoint
from kingfisher.infrastructure.backend import SKILLS_SOURCES
from kingfisher.infrastructure.models import build_model
from kingfisher.infrastructure.prompting import with_user_prompt
from kingfisher.infrastructure.scoping import ScopedSkills, ToolAllowlist

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from kingfisher.config import Config
    from kingfisher.domain.subagent import SubagentSpec

def tool_ceiling(spec: SubagentSpec, *, builtin: Selection, workspace: Selection) -> Selection:
    """Every tool this delegate may call, from the two lists it may narrow.

    Resolved apart against their own grants and then unioned, exactly as
    `_permitted_tools` does it for a request -- which is the point of the
    split: `tools: [http_fetch]` costs a delegate no built-in, the way it
    stopped costing a caller one.

    `builtin` and `workspace` arrive already resolved to names, or both `ALL`.
    Never one of each: `ALL` here means "everything offered", and this cannot
    enumerate that -- so a mixed pair would silently drop the unenumerable half.
    `_resolve_tools` computes them together for exactly that reason.
    """
    from_builtin = narrowed(spec.builtin_tools, by=builtin)
    from_workspace = narrowed(spec.tools, by=workspace)
    if from_builtin == ALL and from_workspace == ALL:
        # Narrowed by nobody on either axis, so no allowlist at all -- which is
        # not an empty one, and the difference is what `ToolAllowlist` enforces.
        return ALL
    if ALL in (from_builtin, from_workspace):
        # Unenumerable, and the failure is quiet if unguarded: `ALL` is the
        # string `"*"`, so unpacking it into the union contributes a tool
        # *named* `*` and drops the axis it stood for. An allowlist is one flat
        # set of names and cannot say "all of those, plus these".
        msg = (
            f"subagent {spec.name!r}: one tool axis resolved to {ALL!r} while the "
            f"other named tools ({from_builtin!r} / {from_workspace!r}). Resolve "
            f"both against what is offered before calling this, or neither"
        )
        raise ValueError(msg)
    return (*(from_builtin or ()), *(from_workspace or ()))


def refuse_unknown_tools(
    spec: SubagentSpec, *, builtin: tuple[str, ...], workspace: tuple[str, ...]
) -> None:
    """Refuse a tool name nothing offers, before narrowing quietly drops it.

    The half of this module's rule that `tools` never had. The docstring above
    lists `tools` among the fields where "a name nothing defines is a mistake
    and raises", and `subagent_skills` does exactly that -- but a declared tool
    name went straight into `narrowed`, where a name nothing offers is simply
    absent from the intersection and leaves no trace.

    So `tools: [reed_file]` built a delegate with *no* tools rather than one
    missing a tool. Silently, which is the failure this format refuses unknown
    *keys* to avoid; the keys were checked and the values were not.

    Two lists now, so there is a second mistake to make and it deserves its own
    sentence: a built-in named under `tools` exists, and telling someone their
    `read_file` is unknown would send them looking for a bug in kingfisher.
    `_refuse_unknown_tools` says the same thing to a request, for the same
    reason.

    Only the refusal. Narrowing stays in `tool_ceiling`, where the two axes
    meet.
    """
    for asked, own, other, here, there in (
        (spec.tools, workspace, builtin, "tools", "builtin_tools"),
        (spec.builtin_tools, builtin, workspace, "builtin_tools", "tools"),
    ):
        if asked in (ALL, None):
            continue
        if misplaced := tuple(n for n in asked if n in set(other)):
            msg = (
                f"subagent {spec.name!r} names {', '.join(misplaced)} in {here}, "
                f"but {belongs_in(misplaced, field=there)}"
            )
            raise CapabilityError(msg)
        if unknown := tuple(n for n in asked if n not in set(own)):
            msg = (
                f"subagent {spec.name!r} names unknown {here[:-1]}(s): "
                f"{', '.join(unknown)}; this agent offers {own}"
            )
            raise CapabilityError(msg)


def subagent_skills(
    spec: SubagentSpec, available: tuple[str, ...], activated: Selection
) -> Selection:
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
    if spec.skills is None or spec.skills == ALL:
        # `None` is none, `ALL` is whatever the request itself has -- neither
        # names anything, so neither can name something the workspace lacks.
        return narrowed(spec.skills, by=activated)
    unknown = tuple(name for name in spec.skills if name not in available)
    if unknown:
        msg = (
            f"subagent {spec.name!r} names unknown skill(s): {', '.join(unknown)}; "
            f"this request offers {available}"
        )
        raise CapabilityError(msg)
    return narrowed(spec.skills, by=activated)


def subagent_helpers(
    spec: SubagentSpec, defined: Mapping[str, Any], activated: Selection
) -> tuple[str, ...]:
    """Which delegates this one may consult, of the ones it names.

    The same two refusals as `subagent_skills`, for the same reasons. A name
    nothing defines is a mistake in the definition and raises. A name that
    exists but this request did not activate is a caller being narrower than
    the definition -- so it is dropped, and reported as withheld rather than
    refused.

    That second half matters more here than anywhere else. `second-opinion`
    runs on another company's servers, so a caller declining it is often
    declining *that*, and refusing the whole request would mean they cannot use
    `reviewer` at all without also accepting OpenAI. The delegate runs alone
    and the report says so.

    Which is why a definition naming a helper should say what to do without
    one: the prompt has to work both ways, because the caller decides.
    """
    if spec.subagents is None:
        return ()
    named = tuple(defined) if spec.subagents == ALL else spec.subagents
    if unknown := tuple(n for n in named if n not in defined):
        msg = (
            f"subagent {spec.name!r} names unknown subagent(s): {', '.join(unknown)}; "
            f"this request offers {tuple(defined)}"
        )
        raise CapabilityError(msg)
    return tuple(narrowed(named, by=activated) or ())


def subagent_middleware(
    spec: SubagentSpec,
    registry: Mapping[str, Callable[[], Any]],
    allowed: Selection,
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


def as_subagent(  # noqa: PLR0913 -- one parameter per thing a definition may
    # narrow, each resolved by its own rule above. Bundling them would hide
    # which of those rules applied to a given delegate.
    spec: SubagentSpec,
    cfg: Config,
    *,
    providers: Selection = ALL,
    builtin_tools: Selection = ALL,
    tools: Selection = ALL,
    backend: Any = None,
    skills: Selection = None,
    # `Any` at the seam: `as_subagent` returns a plain dict and deepagents
    # declares a `SubAgent` TypedDict, which nothing here can satisfy
    # structurally without restating their schema.
    helpers: list[Any] | None = None,
    # What `create_deep_agent` supplies to a delegate it builds, and
    # `SubAgentMiddleware` does not: `create_sub_agent` refuses a spec that
    # names neither. Passed when this delegate is itself a helper, and omitted
    # otherwise so the top-level path keeps deepagents' own defaults.
    default_model: Any = None,
    tool_objects: list[Any] | None = None,
    #: Where this request wants this delegate to run, replacing its file's
    #: answer. `None` is the ordinary case: the file decides.
    run_on: RunOn | None = None,
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
    ceiling = tool_ceiling(spec, builtin=builtin_tools, workspace=tools)
    if ceiling != ALL:
        # `None` is a delegate permitted nothing, which is an empty allowlist
        # rather than an absent one -- the same split the parent makes.
        middleware.append(ToolAllowlist(ceiling or ()))
    # A subagent inherits none of its parent's middleware, so an index it is
    # not given is an index it has no idea exists. `SubAgent.skills` would take
    # source *paths*; this selects by name, which is what a definition writes.
    if skills is not None and skills != ALL and backend is not None:
        middleware.append(
            ScopedSkills(allowed=skills, backend=backend, sources=SKILLS_SOURCES)
        )
    # What lets this delegate delegate. deepagents gives a subagent no `task`
    # tool of its own -- `create_sub_agent` calls `create_agent` with the
    # spec's tools and nothing else -- so the only way in is the one field a
    # spec has that carries code, and `SubAgentMiddleware` is exactly what
    # supplies `task` to the main agent.
    #
    # `helpers` are already built, by a caller that did *not* pass them helpers
    # of their own. That is the whole of the depth bound: not a check, but a
    # call that is never made. `SubAgentMiddleware` refuses an empty list, so
    # an unhelped delegate gets no middleware and no `task` tool -- which is
    # also what a caller who withheld the helper should see.
    if helpers:
        middleware.append(SubAgentMiddleware(backend=backend, subagents=helpers))
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
    # The definition decides, and nothing here second-guesses it. An operator
    # pair -- `KINGFISHER_MODEL_SUBAGENT` / `KINGFISHER_PROVIDER_SUBAGENT` --
    # used to win over this, on the theory that cost is an operator's call and
    # should not need editing content. It said "every delegate" or nothing,
    # which made it useless for the one thing a per-delegate model is for:
    # `second-opinion` exists in order *not* to be the model beside it, and a
    # blanket override silently defeats it. The file says where it runs.
    if tool_objects is not None:
        # Objects, not names -- `SubAgent.tools` is what deepagents registers,
        # and handing it names raises inside `ToolNode`. Narrowing still
        # happens through `ToolAllowlist` above, which is why the whole set
        # goes in and the allowlist decides.
        subagent["tools"] = tool_objects

    provider, model_id = resolved_endpoint(spec, granted=providers, override=run_on)
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
    elif default_model is not None:
        subagent["model"] = default_model
    return subagent
