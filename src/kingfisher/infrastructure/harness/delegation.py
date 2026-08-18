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

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from deepagents.middleware import SubAgentMiddleware

from kingfisher.config import ConfigError
from kingfisher.domain.capabilities import (
    ALL,
    Selection,
    approved_middleware,
    narrowed,
    refuse_ungranted_endpoint,
    refuse_unoffered,
)
from kingfisher.domain.subagent import RunOn, resolved_model
from kingfisher.domain.tool import Found, ceiling, select, split_reference
from kingfisher.infrastructure.harness.models import build_model
from kingfisher.infrastructure.harness.scoping import ScopedSkills, ToolAllowlist
from kingfisher.infrastructure.prompting import with_user_prompt

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from kingfisher.config import Config
    from kingfisher.domain.subagent import SubagentSpec

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
    refuse_unoffered(
        spec.skills, offered=available, kind="skill", subject=f"subagent {spec.name!r}"
    )
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
    refuse_unoffered(
        named, offered=defined, kind="subagent", subject=f"subagent {spec.name!r}"
    )
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


def _host(url: str) -> str:
    """The host a base URL points at, which is what "somewhere else" means.

    Compared rather than the style name, because the names are kingfisher's and
    the destination is not: a deployment is free to fill `OPENAI_BASE_URL` with
    a gateway that also serves the `anthropic` style, and then `provider:
    openai` reads as "somewhere else" while being the same machine.
    """
    return urlsplit(url).netloc


def model_for(spec: SubagentSpec, cfg: Config, *, override: RunOn | None = None) -> str | None:
    """The model this delegate will actually run, or `None` for the deployment's.

    One function because two callers must not disagree: `as_subagent` builds
    from it and `indistinct_delegates` reports from it, and a report about where
    a delegate ended up is worthless if it is computed by a second copy of the
    rule. `indistinct_delegates` said so already -- "it re-resolves through the
    same call the build makes" -- and binding an alias is a second step that
    would otherwise have to be written twice to keep that true.

    The refusal wears the subagent's name. `bound` knows the alias and the
    catalogue and not who asked, so a reader is otherwise told `alternate` is
    unbound and left to grep for whoever wanted it -- and this is the one
    refusal that fires on a file they may not own.
    """
    candidates = resolved_model(spec, override=override)
    if not candidates:
        return None  # it asked for nothing: run whatever the deployment runs

    passed_over: list[str] = []
    for wanted in candidates:
        if wanted.model is not None:
            model = wanted.model
        else:
            assert wanted.alias is not None  # noqa: S101 -- Wanted sets exactly one
            try:
                model = cfg.models.bound(wanted.alias)
            except ConfigError as exc:
                # Not fatal while another candidate is left. A file naming
                # several has said which deployments it can still be useful in,
                # and an alias nobody bound is precisely one it anticipated.
                passed_over.append(f"alias {wanted.alias!r}: {exc}")
                continue
        if spec.distinct and (why := indistinct(spec, cfg, model=model)):
            passed_over.append(f"{model!r} {why}")
            continue
        return model

    # Every candidate was passed over, so there is nothing left to run. The
    # message carries each one and why, because the fix is in the deployment's
    # bindings and a reader has to know which of them to change -- and because
    # this is the one refusal that fires on a file they may not own.
    reasons = "; ".join(passed_over)
    msg = (
        f"subagent {spec.name!r}: none of the {len(candidates)} model(s) it names "
        f"can be used here -- {reasons}"
    )
    raise ConfigError(msg)


def indistinct(spec: SubagentSpec, cfg: Config, *, model: str | None) -> str | None:
    """Why this delegate is not running anywhere different, or `None`.

    Reported, never refused. Kingfisher cannot know that a delegate *needs* to
    differ -- `reviewer` deliberately runs on the deployment's own model, and
    that is the right choice for it. Only a definition that asked to be
    elsewhere can be disappointed, so only those are checked.

    Which is also why silence is the failure worth catching here. A delegate
    that ends up beside the agent it was meant to check still builds, still
    answers, and the answer is worth nothing -- there is no error to notice and
    nothing in the output that looks wrong.

    Two ways to arrive there. Naming the model the deployment already runs is
    the plain one. The other survives the catalogue: two endpoints may point at
    one host, so a different model id is not proof of a different machine, and a
    "second opinion" served by the same gateway is the disappointment this
    exists to name.
    """
    if not spec.wanted:
        return None  # it never asked to be anywhere in particular

    if model == cfg.models.default:
        return f"runs {model!r}, the same model as the main agent"
    profile, endpoint = cfg.models.resolve(model)
    default = _host(cfg.models.resolve()[1].base_url)
    if _host(endpoint.base_url) == default:
        return (
            f"runs {model!r} on endpoint {profile.endpoint!r}, which points at the "
            f"same host as the default ({default})"
        )
    return None


def as_subagent(  # noqa: PLR0913 -- one parameter per thing a definition may
    # narrow, each resolved by its own rule above. Bundling them would hide
    # which of those rules applied to a given delegate.
    spec: SubagentSpec,
    cfg: Config,
    *,
    endpoints: Selection = ALL,
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
    catalogue: Sequence[Found] = (),
    skill_sources: list[Any] | None = None,
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
    allowed = ceiling(
        spec.builtin_tools,
        spec.tools,
        granted_builtin=builtin_tools,
        granted_tools=tools,
        subject=f"subagent {spec.name!r}",
    )
    # Its own workspace tools, chosen as objects rather than inherited as a
    # name. Two files may each define a `fetch`, and the parent cannot register
    # both -- it dispatches by name -- so a delegate that wants one has to be
    # handed that one. Measured: `SubAgent.tools` *adds* to the built-ins rather
    # than replacing them, so this costs a delegate none of its file tools.
    mine = select(allowed, catalogue)
    if allowed != ALL:
        # `None` is a delegate permitted nothing, which is an empty allowlist
        # rather than an absent one -- the same split the parent makes.
        #
        # Flattened to bare names, because the middleware compares against
        # `tool.name` and a tool is called `fetch` however a definition spelled
        # it. Safe here for the same reason it is safe for the parent: what this
        # delegate holds was just selected, and `refuse_ambiguous` would have
        # stopped a definition naming two of a name.
        middleware.append(
            ToolAllowlist(tuple(split_reference(one)[1] for one in (allowed or ())))
        )
    # A subagent inherits none of its parent's middleware, so an index it is
    # not given is an index it has no idea exists. `SubAgent.skills` would take
    # source *paths*; this selects by name, which is what a definition writes.
    if skills is not None and skills != ALL and backend is not None:
        # The same sources the parent got, so a delegate reads a folder's skills
        # under the label its parent granted them by. Passed in rather than
        # rebuilt: two walks of the catalogue could disagree, and a delegate
        # silently offered a different skill than the one named is the failure
        # this whole area exists to stop.
        middleware.append(
            ScopedSkills(allowed=skills, backend=backend, sources=skill_sources or [])
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
    # around the catalogue, its endpoint's base_url, and every param the
    # profile carries. It also re-enables the profile behaviour that
    # `infrastructure.models` exists to avoid. So we build the instance ourselves.
    #
    # The definition decides, and nothing here second-guesses it. An operator
    # pair -- `KINGFISHER_MODEL_SUBAGENT` / `KINGFISHER_PROVIDER_SUBAGENT` --
    # used to win over this, on the theory that cost is an operator's call and
    # should not need editing content. It said "every delegate" or nothing,
    # which made it useless for the one thing a per-delegate model is for:
    # `second-opinion` exists in order *not* to be the model beside it, and a
    # blanket override silently defeats it. The file says where it runs.
    if mine or tool_objects is not None:
        # Objects, not names -- `SubAgent.tools` is what deepagents registers,
        # and handing it names raises inside `ToolNode`. Narrowing still
        # happens through `ToolAllowlist` above, which is why the whole set
        # goes in and the allowlist decides.
        subagent["tools"] = [one.tool for one in mine] + list(tool_objects or [])

    model_id = model_for(spec, cfg, override=run_on)
    if model_id is not None:
        # A lookup, where this used to `replace` four fields of the `Config` and
        # build from the copy. That copy is why the change happened: a param
        # nobody remembered to add to it was silently the deployment's own, so
        # a per-model `max_tokens` would have been dropped without a word. A
        # profile carries every param, and there is nothing here to forget.
        try:
            profile, endpoint = cfg.models.resolve(model_id)
        except ConfigError as exc:
            # `resolve` knows the model and the catalogue; only here knows
            # *who asked*. Without the name the reader is told `gpt-5` cannot be
            # run and left to grep the catalogue for whoever wanted it -- and
            # this is the one refusal that fires on a file they may not own.
            msg = f"subagent {spec.name!r}: {exc}"
            raise ConfigError(msg) from exc
        refuse_ungranted_endpoint(
            profile.endpoint, granted=endpoints, subject=f"subagent {spec.name!r}"
        )
        subagent["model"] = build_model(profile, endpoint)
    elif default_model is not None:
        subagent["model"] = default_model
    return subagent
