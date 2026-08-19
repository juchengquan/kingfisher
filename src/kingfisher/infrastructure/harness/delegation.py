"""Turning a `SubagentSpec` into the `SubAgent` deepagents expects.

`domain.subagent` owns what a definition is and `domain.subagent.reading` what
it means; `infrastructure.catalogue.subagents` finds the files and
`infrastructure.catalogue.documents` reads one. This resolves what a delegate
actually runs with.
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
from langchain_core.runnables import Runnable

from kingfisher.config import ConfigError
from kingfisher.domain.agent import AgentSpec
from kingfisher.domain.capabilities import (
    ALL,
    Selection,
    approved_middleware,
    narrowed,
    refuse_ungranted_endpoint,
    refuse_unoffered,
)
from kingfisher.domain.subagent import RunOn, SubagentError, SubagentSpec
from kingfisher.domain.subagent.rules import resolved_model
from kingfisher.domain.tool import Found, Offering, ceiling, select, split_reference
from kingfisher.infrastructure.harness.backend import HostPathGuard, WorkspaceToolErrors
from kingfisher.infrastructure.harness.models import build_model
from kingfisher.infrastructure.harness.narrowing import NarrowedSkills, ToolAllowlist
from kingfisher.infrastructure.prompting import with_user_prompt

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from kingfisher.config import Config

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

    That second half matters more here than anywhere else. A helper may run on
    another company's servers, so a caller declining it is often declining
    *that*, and refusing the whole request would mean they cannot use the
    delegate that names it without also accepting the vendor. The delegate runs
    alone and the report says so.

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


def _subject(spec: SubagentSpec | AgentSpec) -> str:
    """How a refusal names the file it is about.

    Two folders hold definitions that look alike, so "which kind" is the first
    thing somebody reading the error needs -- it decides which directory they
    open.
    """
    kind = "subagent" if isinstance(spec, SubagentSpec) else "agent"
    return f"{kind} {spec.name!r}"


def model_for(
    spec: SubagentSpec | AgentSpec, *, override: RunOn | None = None
) -> str | None:
    """The model this delegate will actually run, or `None` for the deployment's.

    One function because two callers must not disagree: `as_subagent` builds
    from it and `indistinct_delegates` reports from it, and a report about where
    a delegate ended up is worthless if it is computed by a second copy of the
    rule.

    Barely a function now, and that is the shape of what `alias` was. This held
    a loop over candidates, trying each and passing over the ones this
    deployment had not bound; a definition could name several and say which
    deployments it was still useful in. With one kind of name there is nothing
    to try in turn -- a model this deployment cannot run refuses, and always did
    -- so what is left is the override rule and a lookup. Kept as a function
    rather than inlined at the two call sites, because "the build and the report
    resolve identically" is the property, not the number of lines. It takes no
    `Config` any more either: binding an alias was the only thing here that
    needed one.
    """
    return resolved_model(spec.wanted, override=override)


def model_object(  # five things decide which model a delegate
    # runs, and each is a separate rule: what the file names, what the deployment
    # binds, which endpoints this request may reach, what the request overrode,
    # and what it inherits when it names nothing. Folding any pair together would
    # hide which of the five produced the answer. It was six until `distinct`
    # went, and the sixth -- what it may not match -- took the caller chain with
    # it.
    spec: SubagentSpec | AgentSpec,
    cfg: Config,
    *,
    endpoints: Selection = ALL,
    run_on: RunOn | None = None,
    inherited: Any = None,
) -> Any | None:
    """The model instance this delegate runs, or `None` to leave it inheriting.

    `model_for` answers which model *id*; this answers with the thing that can
    be called, which is a second step and a second set of refusals -- a model
    outside the catalogue, an endpoint this request may not reach.

    A function because two callers need the same answer and must not compute it
    twice. `as_subagent` puts it on the spec it hands deepagents; `agent.py`
    needs it *before* that, because a delegate's helpers inherit it and the
    helpers are built first.

    `inherited` is what summoned this one runs, and it is what a definition
    naming no model gets. `None` for both means the spec carries no model at
    all, which is how a top-level delegate keeps deepagents' own inheritance
    from the agent that holds it.
    """
    model_id = model_for(spec, override=run_on)
    if model_id is None:
        return inherited
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
        msg = f"{_subject(spec)}: {exc}"
        raise ConfigError(msg) from exc
    refuse_ungranted_endpoint(profile.endpoint, granted=endpoints, subject=_subject(spec))
    return build_model(profile, endpoint)


def indistinct(
    spec: SubagentSpec | AgentSpec,
    cfg: Config,
    *,
    model: str | None,
) -> str | None:
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

    # Whoever summoned this one, which is the main agent for a delegate a
    # request activated and the delegate above it for a helper. It used to be
    # the deployment's default in both cases, and that reading survived only
    # while nothing above a delegate could name a model of its own: an agent
    # pinned to `gpt-5` summoning a helper bound to `gpt-5` compared it against
    # a MiniMax default, found a difference, and let the two run side by side.
    # Which is the answer this whole function exists to catch.
    against = cfg.models.default
    # "whatever", not "the delegate". A summoner used to be one, because only a
    # delegate could name a model above another; an agent can now, so naming the
    # kind here would be wrong exactly when the agent is the one that pinned it.
    # `None` still means the main agent on the deployment's own model, which is
    # the one case this can name precisely.
    if model == against:
        return f"runs {model!r}, the same model as the main agent"
    profile, endpoint = cfg.models.resolve(model)
    summoner = _host(cfg.models.resolve(against)[1].base_url)
    if _host(endpoint.base_url) == summoner:
        return (
            f"runs {model!r} on endpoint {profile.endpoint!r}, which points at the "
            f"same host as the default ({summoner})"
        )
    return None



def compiled(  # noqa: PLR0913 -- one parameter per thing kingfisher still
    # decides for a graph it did not build, each resolved by its own rule.
    # `as_subagent` carries the same note for the same reason.
    spec: SubagentSpec,
    cfg: Config,
    *,
    endpoints: Selection = ALL,
    tools: Selection = ALL,
    catalogue: Sequence[Found] = (),
    run_on: RunOn | None = None,
    default_model: Any = None,
) -> dict[str, Any]:
    """A delegate the workspace built itself, wrapped the way deepagents takes one.

    `CompiledSubAgent` is three keys -- name, description, runnable -- and
    deepagents uses the runnable as given: no state schema, no tools, no model,
    no middleware of ours reaches it. Which is why almost none of `as_subagent`
    applies here, and why this returns early rather than sharing that body.

    What kingfisher still decides is the two things a file cannot know: which
    model this deployment binds the delegate's name to, and which of the
    workspace's tools this request granted it. Both are resolved here and handed
    in. The graph is free to ignore them -- nothing can stop it, since deepagents
    never applies an allowlist to a graph it did not build -- but the honest
    thing is the easy thing, and `--list` says which delegates are compiled so
    nobody reads a tool grant as a guarantee.

    The required keys come from deepagents' own declaration rather than a copy
    of it, so a rename upstream fails `test_the_compiled_shape_is_deepagents_own`
    instead of arriving as something confusing much later.
    """
    model = model_object(
        spec, cfg, endpoints=endpoints, run_on=run_on, inherited=default_model
    )
    if model is None:
        # It named nothing and inherited nothing, so it runs what the deployment
        # runs -- built here rather than left to the graph, which would otherwise
        # reach for `init_chat_model` and read credentials around the catalogue
        # entirely. A graph cannot be handed "no model" the way a spec can.
        model = build_model(*cfg.models.resolve())

    # `narrowed` rather than `ceiling`, and the difference is the point.
    # `ceiling` merges the two tool axes into one allowlist, and says plainly
    # that both must be resolved against what is offered or neither. A compiled
    # delegate has one axis: deepagents' built-ins do not exist as objects here,
    # so `builtin_tools` is refused in the declaration and there is no second
    # axis to merge. What is left is the workspace's own, narrowed by what this
    # request granted -- which is the same rule, with nothing to fold.
    # `spelt` for the same reason the parent needs it: this definition may have
    # written `where::what` for a tool no other file defines, and `narrowed`
    # would drop it silently rather than hand the delegate nothing loudly.
    written = Offering.of(catalogue).spelt(spec.tools)
    granted = [one.tool for one in select(narrowed(written, by=tools), catalogue)]

    runnable = spec.build(model, granted)
    # Against `Runnable`, which is what `CompiledSubAgent` declares this field
    # to be -- the same reason `test_the_compiled_shape_is_deepagents_own` pins
    # the *keys* against their declaration rather than a copy of it.
    #
    # This was a duck-type on `invoke`, which was too loose in a way the tests
    # had to admit: deepagents also calls `with_config`, so an object with only
    # `invoke` got past here and failed there. Measured against the four cases
    # that matter -- a compiled graph, an `invoke`-only stub, whatever a class
    # constructs to, and `None` -- `Runnable` is the only one of the three
    # candidate checks that separates the first from the other three.
    #
    # Not `isinstance(runnable, CompiledStateGraph)`, which was the objection
    # that produced the duck-type and is still right: that is an implementation
    # class upstream may rename, and a rule broken by a rename would take every
    # compiled delegate down to enforce a spelling. `Runnable` is the published
    # interface, and a rename there is a breaking change we should hear about.
    #
    # `None` is caught by the same line rather than separately: it was the only
    # thing caught here once, and it is the least likely mistake -- nobody
    # writes `build` meaning to return nothing, where naming a class is an easy
    # reach and `callable()` accepts one.
    if not isinstance(runnable, Runnable):
        made = "None" if runnable is None else type(runnable).__name__
        msg = (
            f"subagent {spec.name!r}: 'build' returned {made}, which is not a graph -- "
            f"nothing to run it with. It is given a model and the tools this delegate "
            f"was granted, and returns the graph to run. A class is callable, so "
            f"`\'build\': YourClass` gets constructed rather than called for a graph; "
            f"name a function that builds one"
        )
        raise SubagentError(msg)
    return {"name": spec.name, "description": spec.description, "runnable": runnable}


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
    #: This delegate's own tools, from the folder named after it. Held whatever
    #: the request granted, which is the whole of what a bundle is for: the
    #: request activated the delegate, and a delegate is made of parts. Everything
    #: else on this signature narrows against what the caller allowed; this one
    #: deliberately does not, and `catalogue` is the half that still does.
    private: Sequence[Found] = (),
    #: This delegate's own skills: the `source::name` keys deepagents will list
    #: them under, and the one source they are mounted at. Held whatever the
    #: request granted, for the reason `private` is.
    private_skills: tuple[tuple[str, ...], tuple[str, str]] | None = None,
    skill_sources: list[Any] | None = None,
    #: Where this request wants this delegate to run, replacing its file's
    #: answer. `None` is the ordinary case: the file decides.
    run_on: RunOn | None = None,
    extra_middleware: list[Any] | None = None,
    #: The model whoever summoned this delegate is running, by name. `None`
    #: means the main agent on the deployment's own model, which is what a
    #: top-level delegate under an unpinned agent has. It is what a definition
    #: naming no model inherits, and what `indistinct` compares against.
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
    if spec.build is not None:
        # A graph the workspace assembled. Nothing below applies to it --
        # deepagents runs it as given -- so this leaves before building a
        # middleware stack that would be dropped on the floor.
        return compiled(
            spec,
            cfg,
            endpoints=endpoints,
            tools=tools,
            catalogue=catalogue,
            run_on=run_on,
            default_model=default_model,
        )

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
        Offering.of(catalogue).spelt(spec.tools),
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
    # Bundle first: a catalogue tool answering a name this delegate defines
    # itself is dropped, so exactly one candidate answers to each name and
    # `duplicated` still holds. Nothing is silently replaced -- the order is
    # stated here, before the lookup, rather than discovered after it -- and the
    # reason it is this way round is that the alternative couples a bundle to
    # every name the shared catalogue may grow later. A delegate that has had
    # its own `fetch` for months should not break because someone else shipped
    # one.
    if private:
        owned = {one.name for one in private}
        mine = tuple(one for one in mine if one.name not in owned)
    # Unconditional, for the reason the parent gives: the backend rejects host
    # paths on every run, so the thing that turns that rejection into a
    # correction must always be here. A delegate is built with the parent's
    # backend and inherits none of the parent's middleware, so the rejection
    # fired for it exactly as it fires above and had nothing to become --
    # `HostPathError` came out of the graph and killed the run.
    #
    # The one with the widest reach of the two, and it needs no workspace tools
    # at all: `write_file` is a built-in, and a delegate that leaves
    # `builtin_tools` out has every one of them.
    middleware.append(HostPathGuard())
    # Then the workspace tools' own failures. Both wrap every call this delegate
    # makes -- they catch different exceptions, so the order between them is the
    # parent's rather than a requirement.
    #
    # A delegate is handed the workspace's tool *objects* and inherits none of
    # its parent's middleware, so the guard the parent installed stopped at the
    # parent while the code it guards went one level down. That was rare while a
    # delegate ran only when a caller named one; an agent declares its own
    # roster now and `subagents` defaults to everything in it, so several
    # delegates holding the workspace's tools is the ordinary case.
    #
    # Built from everything walked rather than from what this delegate was
    # granted, for the reason the parent gives: a delegate that holds none of
    # them cannot reach one, and narrowing it here would mean building the guard
    # from a set that is decided afterwards.
    if catalogue or private:
        middleware.append(
            WorkspaceToolErrors(frozenset(entry.name for entry in (*catalogue, *private)))
        )
    if allowed != ALL:
        # `None` is a delegate permitted nothing, which is an empty allowlist
        # rather than an absent one -- the same split the parent makes.
        #
        # Flattened to bare names, because the middleware compares against
        # `tool.name` and a tool is called `fetch` however a definition spelled
        # it. Safe here for the same reason it is safe for the parent: what this
        # delegate holds was just selected, and `refuse_ambiguous` would have
        # stopped a definition naming two of a name.
        #
        # Private names are added rather than filtered against, and leaving them
        # out was a silent failure rather than a missing feature: the tool is
        # registered on the delegate either way, so the model sees it, calls it,
        # and this refuses -- a capability that exists and cannot be used, with
        # nothing in the output saying why. They are held whatever the request
        # granted, so there is nothing here for them to be narrowed by.
        middleware.append(
            ToolAllowlist(
                tuple(split_reference(one)[1] for one in (allowed or ()))
                + tuple(one.name for one in private)
            )
        )
    # A subagent inherits none of its parent's middleware, so an index it is
    # not given is an index it has no idea exists. `SubAgent.skills` would take
    # source *paths*; this selects by name, which is what a definition writes.
    # A bundle's skills are held whichever way the definition wrote `skills:`,
    # so they are folded in before the branch rather than inside it. That
    # matters because `skills` defaults to *none* -- a delegate saying nothing
    # gets no skills index at all -- and a delegate that ships a skill of its
    # own and is told about none of it is the silent emptiness this package
    # keeps refusing.
    own_names, own_source = private_skills or ((), None)
    if own_names:
        granted = () if skills in (None, ALL) else tuple(skills)
        skills = tuple(dict.fromkeys((*granted, *own_names)))
        skill_sources = [*(skill_sources or []), own_source]
    if skills is not None and skills != ALL and backend is not None:
        # The same sources the parent got, so a delegate reads a folder's skills
        # under the label its parent granted them by. Passed in rather than
        # rebuilt: two walks of the catalogue could disagree, and a delegate
        # silently offered a different skill than the one named is the failure
        # this whole area exists to stop.
        middleware.append(
            NarrowedSkills(allowed=skills, backend=backend, sources=skill_sources or [])
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
    # Last, so a deployment's middleware sees the tool and skill narrowing
    # kingfisher applied rather than running ahead of it.
    middleware.extend(extra_middleware or [])
    if middleware:
        subagent["middleware"] = middleware

    # A *name* here would be resolved by deepagents' `init_chat_model`, which
    # infers its own provider and reads credentials from the environment --
    # around the catalogue, its endpoint's base_url, and every param the
    # profile carries. It also re-enables the profile behaviour that
    # `infrastructure.harness.models` exists to avoid. So we build the instance
    # ourselves.
    #
    # The definition decides, and nothing here second-guesses it. An operator
    # pair -- `KINGFISHER_MODEL_SUBAGENT` / `KINGFISHER_PROVIDER_SUBAGENT` --
    # used to win over this, on the theory that cost is an operator's call and
    # should not need editing content. It said "every delegate" or nothing,
    # which made it useless for the one thing a per-delegate model is for: a
    # delegate that exists in order *not* to be the model beside it, which a
    # blanket override silently defeats. The file says where it runs.
    if mine or private or tool_objects is not None:
        # Objects, not names -- `SubAgent.tools` is what deepagents registers,
        # and handing it names raises inside `ToolNode`. Narrowing still
        # happens through `ToolAllowlist` above, which is why the whole set
        # goes in and the allowlist decides.
        subagent["tools"] = [one.tool for one in (*private, *mine)] + list(tool_objects or [])

    built = model_object(
        spec, cfg, endpoints=endpoints, run_on=run_on, inherited=default_model
    )
    if built is not None:
        subagent["model"] = built
    return subagent
