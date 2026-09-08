"""Turning a `SubagentSpec` into the `SubAgent` deepagents expects.

Split out of `agent.py`, which was 657 lines doing four jobs. This was the largest of
them and the most self-contained: nothing in here calls anything in `agent.py`. It has
since grown other callers -- `activation` reports with `model_for` and `indistinct` --
so the one-caller claim that used to sit here is gone rather than corrected, being the
kind that goes stale in another file.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from deepagents.middleware import SubAgentMiddleware
from langchain_core.runnables import Runnable

from kingfisher.agents.spec import AgentSpec
from kingfisher.config import ConfigError
from kingfisher.domain.capabilities import (
    ALL,
    Selection,
    ceiling,
    narrowed,
    refuse_ungranted_endpoint,
    refuse_unoffered,
)
from kingfisher.infrastructure.harness.backend import (
    HostPathGuard,
    WorkspaceToolErrors,
    WorkspaceToolPaths,
)
from kingfisher.infrastructure.harness.models import build_model
from kingfisher.infrastructure.harness.narrowing import NarrowedSkills, ToolAllowlist
from kingfisher.infrastructure.prompting import with_user_prompt
from kingfisher.subagents.rules import resolved_model
from kingfisher.subagents.spec import RunOn, SubagentError, SubagentSpec
from kingfisher.tools.spec import Found, Offering, select, split_reference

if TYPE_CHECKING:
    from collections.abc import Mapping

    from kingfisher.config import Config
    from kingfisher.skills.registry import SkillRegistry


def _identities(registry: SkillRegistry, selection: Selection) -> Selection:
    """A selection as the skills it means, rather than as the words somebody wrote.

    A name nothing offers is kept as written. It narrows to nothing either way,
    and this also resolves a *request's* ceiling -- refusing there would fire on
    a grant no definition's author can edit.
    """
    if selection is None or selection == ALL:
        return selection
    return tuple(registry.identity(one) or one for one in selection)


def subagent_skills(
    spec: SubagentSpec, offered: SkillRegistry, activated: Selection
) -> Selection:
    """Which skills a delegate is told about, or `None` for none."""
    if spec.skills is not None and spec.skills != ALL:
        # `None` is none and `ALL` is whatever the request itself has -- neither
        # names anything, so neither can name something the workspace lacks.
        refuse_unoffered(
            spec.skills,
            offered=offered.spellings,
            kind="skill",
            subject=f"subagent {spec.name!r}",
            # The listing stays what a person would write, not every spelling
            # that resolves: printing both halves of each pair is a longer list
            # saying less.
            listing=f"{offered.names}",
        )
    # Both sides in the registry's vocabulary before they meet. `narrowed`
    # intersects strings, and one skill has two legal spellings -- so a delegate
    # granted `incident::postmortem` under a ceiling of `postmortem` kept
    # neither. The identity is also what `NarrowedSkills` filters its index by,
    # so a name left as written reached the delegate as an empty index: every
    # `skills:` a delegate declared was inert, silently, whichever way it was
    # spelled.
    return narrowed(_identities(offered, spec.skills), by=_identities(offered, activated))


def subagent_helpers(
    spec: SubagentSpec, defined: Mapping[str, Any], activated: Selection
) -> tuple[str, ...]:
    """Which delegates this one may consult, of the ones it names."""
    if spec.subagents is None:
        return ()
    named = tuple(defined) if spec.subagents == ALL else spec.subagents
    refuse_unoffered(
        named, offered=defined, kind="subagent", subject=f"subagent {spec.name!r}"
    )
    return tuple(narrowed(named, by=activated) or ())


def _host(url: str) -> str:
    """The host a base URL points at, which is what "somewhere else" means."""
    return urlsplit(url).netloc


def _subject(spec: SubagentSpec | AgentSpec) -> str:
    """How a refusal names the file it is about."""
    kind = "subagent" if isinstance(spec, SubagentSpec) else "agent"
    return f"{kind} {spec.name!r}"


def model_for(
    spec: SubagentSpec | AgentSpec, *, override: RunOn | None = None
) -> str | None:
    """The model this delegate will actually run, or `None` for the deployment's."""
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
    """The model instance this delegate runs, or `None` to leave it inheriting."""
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
    """Why this delegate is not running anywhere different, or `None`."""
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
    """A delegate the workspace built itself, wrapped the way deepagents takes one."""
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
    # Against `Runnable`, which is what `CompiledSubAgent` declares this field to be --
    # the same reason `test_the_compiled_shape_is_deepagents_own` pins the *keys*
    # against their declaration rather than a copy of it.
    #
    # This was a duck-type on `invoke`, which was too loose in a way the tests had to
    # admit: deepagents also calls `with_config`, so an object with only `invoke` got
    # past here and failed there. Measured against the four cases that matter -- a
    # compiled graph, an `invoke`-only stub, whatever a class constructs to, and `None`
    # -- `Runnable` is the only one of the three candidate checks that separates the
    # first from the other three.
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
) -> dict[str, Any]:
    """Translate kingfisher's definition into deepagents' `SubAgent`."""
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
    # Unconditional, for the reason the parent gives: the backend rejects host paths on
    # every run, so the thing that turns that rejection into a correction must always be
    # here. A delegate is built with the parent's backend and inherits none of the
    # parent's middleware, so the rejection fired for it exactly as it fires above and
    # had nothing to become -- `HostPathError` came out of the graph and killed the run.
    middleware.append(HostPathGuard())
    # Then the workspace tools' own failures. Both wrap every call this delegate makes
    # -- they catch different exceptions, so the order between them is the parent's
    # rather than a requirement.
    if catalogue or private:
        names = frozenset(entry.name for entry in (*catalogue, *private))
        middleware.append(WorkspaceToolErrors(names))
        # And the same translation the parent gets, from the backend it was
        # handed -- a delegate has no `session_dir` of its own, and the backend
        # is rooted at one.
        #
        # This matters more here than for the parent. A delegate is built with
        # its own prompt and none of `system.md`, so it never learns that host
        # paths exist and cannot be told one except by its caller. #245 left
        # that open in as many words: "a design question about what a delegate
        # is told". This is the answer -- it is told the same paths as everyone
        # else, because there is no other kind.
        root = getattr(backend, "workspace", None)
        if root is not None:
            middleware.append(WorkspaceToolPaths(names, root))
    if allowed != ALL:
        # `None` is a delegate permitted nothing, which is an empty allowlist rather
        # than an absent one -- the same split the parent makes.
        middleware.append(
            ToolAllowlist(
                tuple(split_reference(one)[1] for one in (allowed or ()))
                + tuple(one.name for one in private),
                subject=f"the {spec.name!r} subagent",
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
    # What lets this delegate delegate. deepagents gives a subagent no `task` tool of
    # its own -- `create_sub_agent` calls `create_agent` with the spec's tools and
    # nothing else -- so the only way in is the one field a spec has that carries code,
    # and `SubAgentMiddleware` is exactly what supplies `task` to the main agent.
    if helpers:
        middleware.append(SubAgentMiddleware(backend=backend, subagents=helpers))
    # Last, so a deployment's middleware sees the tool and skill narrowing
    # kingfisher applied rather than running ahead of it.
    middleware.extend(extra_middleware or [])
    if middleware:
        subagent["middleware"] = middleware

    # A *name* here would be resolved by deepagents' `init_chat_model`, which infers its
    # own provider and reads credentials from the environment -- around the catalogue,
    # its endpoint's base_url, and every param the profile carries. It also re-enables
    # the profile behaviour that `infrastructure.harness.models` exists to avoid. So we
    # build the instance ourselves.
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
