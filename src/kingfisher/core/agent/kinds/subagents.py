"""Turning a `SubagentSpec` into the `SubAgent` deepagents expects.

Nothing here calls into `assembly.py`: a call back that way would make the two one job again.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from deepagents.middleware import SubAgentMiddleware
from langchain_core.runnables import Runnable

from kingfisher.core.access.capabilities import (
    ALL,
    Selection,
    ceiling,
    narrowed,
    refuse_unoffered,
)
from kingfisher.core.agent.middlewares.standing import standing_middleware
from kingfisher.core.agent.models import build_model, model_named, summarizer_trigger
from kingfisher.core.agent.prompting import behind_instructions
from kingfisher.core.agent.session_paths import SessionPaths
from kingfisher.core.agent.tool_guards import guarded_tools
from kingfisher.core.definitions.agents.spec import AgentSpec
from kingfisher.core.definitions.subagents.rules import resolved_model
from kingfisher.core.definitions.subagents.spec import (
    RunOn,
    SubagentError,
    SubagentSpec,
    opens_skills,
)
from kingfisher.core.definitions.tools.spec import FoundTool, Offering, select, split_reference

if TYPE_CHECKING:
    from collections.abc import Mapping

    from kingfisher.core.agent.middlewares.recorder import RecorderMiddleware
    from kingfisher.core.config import Config
    from kingfisher.core.definitions.skills.registry import SkillRegistry


def _identities(registry: SkillRegistry, selection: Selection) -> Selection:
    """A selection as the skills it means, rather than as written.

    A name nothing offers is kept as written, not refused: this also resolves a request's
    ceiling, a grant no definition's author can edit.
    """
    if selection is None or selection == ALL:
        return selection
    return tuple(registry.identity(one) or one for one in selection)


def subagent_skills(
    spec: SubagentSpec,
    offered: SkillRegistry,
    activated: Selection,
    *,
    held: frozenset[str] | None = None,
) -> Selection:
    """Which skills a subagent is told about, or `None` for none."""
    # The file's own names are refused, not the caller's share: a typo is the file's fault.
    if spec.skills is not None and spec.skills != ALL:
        refuse_unoffered(
            spec.skills,
            offered=offered.spellings,
            kind="skill",
            subject=f"subagent {spec.name!r}",
            # What a person would write, not every spelling that resolves.
            listing=f"{offered.names}",
        )
    # Both sides as identities before they meet: `narrowed` intersects strings, one skill
    # has two spellings, and `NarrowedSkillsMiddleware` filters its index by identity.
    reached = spec.declares(held).skills
    return narrowed(_identities(offered, reached), by=_identities(offered, activated))


def subagent_helpers(
    spec: SubagentSpec,
    defined: Mapping[str, Any],
    activated: Selection,
    *,
    held: frozenset[str] | None = None,
    #: What the refusal lists, for this caller. No default: `refuse_unoffered`'s own lists
    #: every definition, out of reach or not.
    listing: str,
) -> tuple[str, ...]:
    """Which subagents this one may consult, of the ones it names."""
    if spec.subagents is None:
        return ()
    named = tuple(defined) if spec.subagents == ALL else spec.subagents
    # Refused, never resolved: `refuse_cycles` walked only keys of `defined`, so a name
    # resolved here is an edge no walk saw, and a loop through it builds without end.
    refuse_unoffered(
        named,
        offered=defined,
        kind="subagent",
        subject=f"subagent {spec.name!r}",
        listing=listing,
    )
    return tuple(narrowed(spec.declares(held).subagents, by=activated) or ())


def _host(url: str) -> str:
    """The host a base URL points at, which is what "somewhere else" means."""
    return urlsplit(url).netloc


def _subject(spec: SubagentSpec | AgentSpec) -> str:
    """How a refusal names the file it is about."""
    kind = "subagent" if isinstance(spec, SubagentSpec) else "agent"
    return f"{kind} {spec.name!r}"


def model_object(  # a parameter per rule picking the model: folding two hides which answered
    spec: SubagentSpec | AgentSpec,
    cfg: Config,
    *,
    endpoints: Selection = ALL,
    run_on: RunOn | None = None,
    inherited: Any = None,
) -> Any | None:
    """The model instance this subagent runs, or `None` to leave it inheriting."""
    model_id = resolved_model(spec.wanted, override=run_on)
    if model_id is None:
        return inherited
    return model_named(model_id, cfg, endpoints=endpoints, subject=_subject(spec))


def indistinct(
    spec: SubagentSpec | AgentSpec,
    cfg: Config,
    *,
    model: str | None,
) -> str | None:
    """Why this subagent is not running anywhere different, or `None`."""
    if not spec.wanted:
        return None  # it never asked to be anywhere in particular

    against = cfg.models.default
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


def compiled(  # noqa: PLR0913 -- one per thing still decided for a graph kingfisher did not build
    spec: SubagentSpec,
    cfg: Config,
    *,
    endpoints: Selection = ALL,
    tools: Selection = ALL,
    catalogue: Sequence[FoundTool] = (),
    #: This subagent's own tools, held whatever the request granted, as on the assembled path.
    private: Sequence[FoundTool] = (),
    #: What its tools resolve paths against: a compiled subagent has no middleware to do it.
    paths: SessionPaths | None = None,
    run_on: RunOn | None = None,
    default_model: Any = None,
    held: frozenset[str] | None = None,
) -> dict[str, Any]:
    """A subagent the workspace built itself, wrapped the way deepagents takes one."""
    model = model_object(
        spec, cfg, endpoints=endpoints, run_on=run_on, inherited=default_model
    )
    if model is None:
        # Built here, not left to the graph: it would reach for `init_chat_model` and read
        # credentials around the catalogue.
        model = build_model(*cfg.models.resolve())

    # `narrowed`, not `ceiling`: `builtin_tools` is refused for a compiled subagent, so there
    # is no second axis to merge. `spelt` first, or `narrowed` silently drops a `where::what`
    # no other file defines.
    written = Offering.of(catalogue).spelt(spec.declares(held).tools)
    shared = select(narrowed(written, by=tools), catalogue)
    # Bundle first and the catalogue's namesake dropped: `build` dispatches by name, and a
    # subagent's own `fetch` should not change because somebody else shipped one.
    owned = {one.name for one in private}
    granted = [one.tool for one in (*private, *(o for o in shared if o.name not in owned))]

    # Wrapped: this graph gets no kingfisher middleware, so its tools carry their guards or
    # have none.
    runnable = spec.build(model, guarded_tools(granted, paths))
    # `Runnable`, as `CompiledSubAgent` declares the field: a duck-type on `invoke` is too
    # loose, since deepagents also calls `with_config`.
    if not isinstance(runnable, Runnable):
        made = "None" if runnable is None else type(runnable).__name__
        msg = (
            f"subagent {spec.name!r}: 'build' returned {made}, which is not a graph -- "
            f"nothing to run it with. It is given a model and the tools this subagent "
            f"was granted, and returns the graph to run. A class is callable, so "
            f"`\'build\': YourClass` gets constructed rather than called for a graph; "
            f"name a function that builds one"
        )
        raise SubagentError(msg)
    return {"name": spec.name, "description": spec.description, "runnable": runnable}


def as_subagent(  # noqa: PLR0913 -- one per narrowing rule; bundling them hides which applied
    spec: SubagentSpec,
    cfg: Config,
    *,
    #: What it runs: its own model, else its summoner's, which deepagents hands a spec
    #: naming none. Its summariser sizes the PDFs this subagent's turn may hold open.
    runs: Any,
    endpoints: Selection = ALL,
    builtin_tools: Selection = ALL,
    tools: Selection = ALL,
    backend: Any = None,
    skills: Selection = None,
    # `Any`: deepagents' `SubAgent` is a TypedDict a plain dict cannot satisfy without
    # restating its schema.
    helpers: list[Any] | None = None,
    # Passed only for a helper: `SubAgentMiddleware` supplies no model and `create_sub_agent`
    # refuses a spec naming none. Omitted at the top so deepagents' own defaults hold.
    default_model: Any = None,
    tool_objects: list[Any] | None = None,
    catalogue: Sequence[FoundTool] = (),
    #: This subagent's own tools, held whatever the request granted: a subagent is made of
    #: its parts. `catalogue` is the half that still narrows.
    private: Sequence[FoundTool] = (),
    #: Its own skills' `source::name` keys and the sources they mount at, held as `private` is.
    private_skills: tuple[tuple[str, ...], tuple[tuple[str, str], ...]] | None = None,
    skill_sources: list[Any] | None = None,
    #: The parent's: a subagent runs in the parent's session on the parent's backend.
    paths: SessionPaths | None = None,
    #: Where this request wants it to run, replacing the file's answer; `None` lets the file
    #: decide.
    run_on: RunOn | None = None,
    extra_middleware: list[Any] | None = None,
    #: The caller's source ids, for this definition's own audiences. `tools` is the agent's
    #: ceiling and is never narrowed by these.
    held: frozenset[str] | None = None,
    #: The turn's recorder for this definition. A compiled subagent's calls go unrecorded;
    #: its parent records its answer as the result of `task`.
    recorder: RecorderMiddleware | None = None,
    #: The deployment's instructions, which every subagent is given ahead of its own.
    instructions: str = "",
) -> dict[str, Any]:
    """Translate kingfisher's definition into deepagents' `SubAgent`."""
    if spec.build is not None:
        # deepagents runs a built graph as given, so nothing below applies to it.
        return compiled(
            spec,
            cfg,
            endpoints=endpoints,
            tools=tools,
            catalogue=catalogue,
            private=private,
            paths=paths,
            run_on=run_on,
            default_model=default_model,
            held=held,
        )

    subagent: dict[str, Any] = {
        "name": spec.name,
        "description": spec.description,
        # The deployment's instructions first, so delegated work cannot escape a house rule.
        "system_prompt": behind_instructions(spec.system_prompt, instructions),
    }
    middleware: list[Any] = []
    # A subagent inherits none of the parent's `GrantedOnlyMiddleware`, so the ceiling is applied
    # here or a withheld `execute` reaches every subagent. What it is stays `ceiling`'s call.
    allowed = ceiling(
        spec.builtin_tools,
        Offering.of(catalogue).spelt(spec.declares(held).tools),
        granted_builtin=builtin_tools,
        granted_tools=tools,
        subject=f"subagent {spec.name!r}",
    )
    # Objects, not names: two files may each define a `fetch`, and the parent dispatches by
    # name. `SubAgent.tools` adds to the built-ins rather than replacing them.
    mine = select(allowed, catalogue)
    # Bundle first, so one candidate answers each name and `duplicated` holds. The other
    # order would couple a bundle to every name the shared catalogue grows later.
    if private:
        owned = {one.name for one in private}
        mine = tuple(one for one in mine if one.name not in owned)
    # Asked of its own built-ins under the ceiling: a skill is opened with `read_file`, so
    # without it every entry of the index, its bundle's too, is a refused call waiting.
    if not opens_skills(narrowed(spec.builtin_tools, by=builtin_tools)):
        skills, private_skills = None, None
    # No parent middleware reaches a subagent, so its skills index is its own
    # (`SubAgent.skills` takes paths, not names). A bundle's skills are folded in first:
    # `skills` defaults to none, and a subagent must still be told about the skills it ships.
    own_names, own_sources = private_skills or ((), ())
    if own_names:
        granted = () if skills in (None, ALL) else tuple(skills)
        skills = tuple(dict.fromkeys((*granted, *own_names)))
        skill_sources = [*(skill_sources or []), *own_sources]
    # From `standing_middleware`, so the agent, this subagent and `general-purpose` cannot
    # differ. `paths` is handed down, not read off the backend, which need not carry it.
    middleware.extend(
        standing_middleware(
            "subagent",
            # Inside every standing one: deepagents gives a subagent no `task` tool, and
            # `middleware` is the only spec field that carries code, so `task` comes the way
            # the main agent gets it; the deployment's own go after that, so they see
            # kingfisher's narrowing rather than running ahead of it.
            inner=[
                *([SubAgentMiddleware(backend=backend, subagents=helpers)] if helpers else []),
                *(extra_middleware or []),
            ],
            names=frozenset(entry.name for entry in (*catalogue, *private)),
            paths=paths,
            layout=cfg.layout,
            # The parent's, whose shell is the one this subagent's `execute` runs on.
            backend=backend,
            step_limit=cfg.recursion_limit,
            trigger=summarizer_trigger(runs),
            recorder=recorder,
            # `None` permits nothing: an empty allowlist, not an absent one.
            tools=None
            if allowed == ALL
            else tuple(split_reference(one)[1] for one in (allowed or ()))
            + tuple(one.name for one in private),
            tools_subject=f"the {spec.name!r} subagent",
            # The parent's sources, passed in rather than rebuilt: two walks could disagree
            # and offer the subagent a different skill than the one named.
            skills=None
            if skills is None or skills == ALL or backend is None
            else tuple(skills),
            skill_sources=skill_sources or [],
        )
    )
    if middleware:
        subagent["middleware"] = middleware

    # Objects, not names: names raise inside `ToolNode`. The whole set goes in and
    # `GrantedOnlyMiddleware` decides. Set even when empty: deepagents reads a missing key
    # as "the parent's tools", and a subagent granted none would be built holding them.
    subagent["tools"] = [one.tool for one in (*private, *mine)] + list(tool_objects or [])

    # An instance, not a name: deepagents' `init_chat_model` would infer its own provider and
    # read credentials from the environment, around the catalogue and the profile's params.
    built = model_object(
        spec, cfg, endpoints=endpoints, run_on=run_on, inherited=default_model
    )
    if built is not None:
        subagent["model"] = built
    return subagent
