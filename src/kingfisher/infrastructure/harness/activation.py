"""What a request turns on: the skills and the subagents it activates."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from deepagents import FilesystemPermission

from kingfisher.config import ConfigError
from kingfisher.domain.access import handed_work_by, within_reach
from kingfisher.domain.capabilities import (
    ALL,
    Capabilities,
    Selection,
    bare_name,
    narrowed,
    refuse_unoffered,
)
from kingfisher.infrastructure.catalogue import Definitions
from kingfisher.infrastructure.harness.backend import bundled_skill_mounts
from kingfisher.infrastructure.harness.kinds.subagents import indistinct
from kingfisher.kinds.skills import registry as skill_registry
from kingfisher.kinds.skills.registry import SkillRegistry
from kingfisher.kinds.subagents.rules import (
    refuse_cycles,
    refuse_two_of_a_name,
    resolved_model,
)
from kingfisher.kinds.subagents.spec import RunOn, SubagentSpec
from kingfisher.layout import SKILLS_ROUTE

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from kingfisher.config import Config
    from kingfisher.kinds.agents.spec import AgentSpec


def available_skills(cfg: Config, *, catalogue: Definitions | None = None) -> tuple[str, ...]:
    """Every skill this request may activate."""
    return activatable_skills(cfg, catalogue=catalogue).names


def activatable_skills(
    cfg: Config, *, catalogue: Definitions | None = None
) -> SkillRegistry:
    """The registry that decides which skills the agent is told about."""
    return (catalogue or Definitions.from_config(cfg)).registry


def defined_subagents(
    cfg: Config, *, catalogue: Definitions | None = None
) -> dict[str, SubagentSpec]:
    """Every subagent this request may activate."""
    return dict((catalogue or Definitions.from_config(cfg)).subagents.specs)


def _unrunnable(cfg: Config, specs: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    """`(name, why)` for each of these a deployment cannot run.

    Shared by both kinds rather than written twice, because two places deciding
    whether a definition can run is how `doctor` came to answer for subagents only:
    the agent half was never written, and the row said "every definition resolves to
    a model" while the workspace's one agent could not start.
    """
    found: list[tuple[str, str]] = []
    for name, spec in sorted(specs.items()):
        try:
            model = resolved_model(spec.wanted)
            if model is not None:
                cfg.models.resolve(model)
        except ConfigError as exc:
            found.append((name, str(exc)))
    return tuple(found)


def unrunnable_subagents(
    cfg: Config, *, catalogue: Definitions | None = None
) -> tuple[tuple[str, str], ...]:
    """`(name, why)` for each defined subagent this deployment cannot run."""
    return _unrunnable(cfg, defined_subagents(cfg, catalogue=catalogue))


def unrunnable_agents(
    cfg: Config, *, catalogue: Definitions | None = None
) -> tuple[tuple[str, str], ...]:
    """`(name, why)` for each defined agent this deployment cannot run."""
    resolved = catalogue or Definitions.from_config(cfg)
    return _unrunnable(cfg, dict(resolved.agents.specs))


def for_the_caller(
    agent: AgentSpec | None, asked: Capabilities, *, held: frozenset[str] | None
) -> Capabilities:
    """What a request may use: what the agent declares for this caller, narrowed by
    what the request asked for.
    """
    return agent.declares(held).intersect(asked) if agent is not None else asked


def offered_to(
    defined: Mapping[str, SubagentSpec],
    held: frozenset[str] | None,
    *,
    by: Sequence[Any] = (),
) -> str:
    """The subagents a refusal of an unknown one lists to this caller.

    `by` is the agents they reach -- one, at a build. A subagent needs two things to be
    nameable: its own `source_ids:` has to admit the caller, and an agent they can open
    has to name it. This asked the first alone, so three of the four refusals listing
    through here named a definition nothing activates -- a name reachable by no path,
    which `formats.md` says a listing, a refusal and the withheld report all withhold.
    The listing and the report moved to `handed_work_by`; these are the rest.

    Which agents depends on what the refusal is about, and the two sites differ for a
    reason. A *file* naming a subagent wrong is fixed by editing that file, so what it
    could name instead is everything this caller reaches through any agent they can
    open -- `agents_to_ask` below. A *request* naming one is bound by the agent it
    opened, because `Capabilities.subagents` may only narrow what that agent declares,
    so there `by` is that agent alone.

    Empty `by` is a caller with no agent resolved to ask about, and their own line is
    then the whole of what can be asked. `settled_request` stays there and needs nothing
    more: it returns early unless the ceiling is `"*"`, and an agent declaring `"*"`
    activates every definition the caller reaches, so the two answers are one set.
    """
    reachable = handed_work_by(by, defined, held) if by else within_reach(defined, defined, held)
    return f"{tuple(sorted(reachable))}"


def agents_to_ask(cfg: Config, *, catalogue: Definitions | None = None) -> tuple[Any, ...]:
    """The agents whose activations decide what this caller can be handed work by.

    Read on a refusal path only, so the catalogue read costs nothing anyone waits on.
    What it is for is the listing beside a refusal about a *file*: the subagents that
    file could name instead are the ones some agent in this deployment can hand work
    to, not only the ones the agent being built happens to name.

    Every agent, and no narrowing here of any kind, because both the narrowings this
    first had changed no answer: `narrowed_for` reads a definition's own `source_ids:`
    as the default audience for its entries, so an agent the caller cannot open
    narrows every subagent it names away; and `handed_work_by` answers an unscoped
    caller itself. Mutating either away left the suite green -- a guard over nothing
    is the same defect as a rule that cannot fail, one level down, so the condition
    belongs where it can be read once and not restated here.
    """
    return tuple((catalogue or Definitions.from_config(cfg)).agents.specs.values())


def settled_request(
    cfg: Config,
    asked: Capabilities,
    *,
    ceiling: Selection,
    held: frozenset[str] | None,
    catalogue: Definitions | None = None,
) -> Capabilities:
    """`asked`, each subagent it names settled to the one it means for this caller.

    `ceiling` is what clamps the request's subagents: the deployment's grant under the
    agent's declaration. A list there narrows the request silently, a typo and a name
    out of reach alike, so only `"*"` leaves anything to settle.
    """
    if ceiling != ALL or not isinstance(asked.subagents, tuple):
        return asked
    defined = defined_subagents(cfg, catalogue=catalogue)
    reached = within_reach(defined, defined, held)
    names = tuple(dict.fromkeys(_one_reached(name, reached) for name in asked.subagents))
    # Against what this caller reaches rather than every definition: a request naming
    # one out of reach has to read what a typo reads, or guessing tells the two apart.
    refuse_unoffered(
        names,
        offered=reached,
        kind="subagent",
        subject="this request",
        listing=offered_to(defined, held),
    )
    return replace(asked, subagents=names)


def _one_reached(name: str, reached: Iterable[str]) -> str:
    """The definition a bare name means to a caller who reaches one of those sharing it.

    Refused as two of a name instead, the refusal would say that the other exists.
    """
    mine = [key for key in reached if bare_name(key) == name]
    return mine[0] if len(mine) == 1 else name


def refuse_misnamed_by(
    cfg: Config,
    agent: AgentSpec | None,
    asked: Capabilities,
    *,
    held: frozenset[str] | None,
    catalogue: Definitions | None = None,
) -> None:
    """Refuse a subagent an agent's own file names wrong, of those this request asks for.

    The file's names as written rather than as this caller reaches them: a mistake in
    the file is refused whoever is calling. Only the listing follows the caller.
    """
    if agent is None or not isinstance(agent.subagents, tuple):
        return
    written = narrowed(asked.subagents, by=agent.subagents)
    if not written:
        return
    defined = defined_subagents(cfg, catalogue=catalogue)
    subject = f"agent {agent.name!r}"
    refuse_unoffered(
        written,
        offered=defined,
        kind="subagent",
        subject=subject,
        listing=offered_to(defined, held, by=agents_to_ask(cfg, catalogue=catalogue)),
    )
    refuse_two_of_a_name(written, subject=subject)


def indistinct_subagents(  # noqa: PLR0913 -- the agent and the caller are what make
    # this the build's roster rather than the request's
    cfg: Config,
    capabilities: Capabilities,
    *,
    catalogue: Definitions | None = None,
    run_on: Mapping[str, RunOn] | None = None,
    agent: AgentSpec | None = None,
    held: frozenset[str] | None = None,
) -> tuple[tuple[str, str], ...]:
    """`(name, why)` for each activated subagent that asked to run elsewhere and did
    not.
    """
    capabilities = for_the_caller(agent, capabilities, held=held)
    if capabilities.subagents is None:
        return ()
    defined = defined_subagents(cfg, catalogue=catalogue)
    named = tuple(defined) if capabilities.subagents == ALL else capabilities.subagents
    wanted = run_on or {}

    found = []
    # A name nothing defines is skipped rather than refused: `build_agent` refuses it,
    # and reporting is not its job.
    for name in within_reach((n for n in named if n in defined), defined, held):
        spec = defined[name]
        try:
            model = resolved_model(spec.wanted, override=wanted.get(name))
        except ConfigError:
            # A model this deployment cannot run. The build
            # refuses it with the message worth reading; reporting is not
            # refusing, and raising a second copy of that refusal from here
            # would put it in front of the caller twice, worded for the wrong
            # question. Skipped, and the build says why.
            continue
        if why := indistinct(spec, cfg, model=model):
            found.append((name, why))
    return tuple(found)


def _denied_path(read_at: str) -> str:
    """One skill's own directory, as a rule the agent's routes can carry."""
    return f"{SKILLS_ROUTE}{read_at.lstrip('/').rsplit('/', 1)[0]}/**"


def _skill_denials(activated: tuple[str, ...], registry: Any) -> list[FilesystemPermission]:
    """Deny reads of skills this request did not activate.

    Built from each skill's own path rather than from its name, and that is the fix
    rather than a tidy-up. This wrote `/skills/{name}/**`, which is where a skill
    sits only while every skill sits at the top level. A skill in a folder lives at
    `/skills/research/lookup/`, so the rule denied a path that does not exist and the
    file tools could still read it -- a boundary failing open, silently, the moment
    folders were possible.
    """
    allowed = set(activated)
    return [
        FilesystemPermission(
            operations=["read"], paths=[_denied_path(one.path)], mode="deny"
        )
        for key, one in registry.offered.items()
        if key not in allowed
    ]


def _private_skills(
    catalogue: Definitions, name: str
) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]] | None:
    """The skills a subagent brings itself, and where they are mounted."""
    registry = catalogue.bundled_skills.get(name)
    if registry is None or not registry.offered:
        return None
    spec = catalogue.subagents.specs.get(name)
    offered = tuple(registry.offered)
    if spec is not None and not spec.carried:
        # Filtered for the reason `_private_tools` gives: only what is listed arrives.
        listed = set(spec.bundled.get("skills", ()))
        offered = tuple(
            key for key in offered if skill_registry.split_qualified(key)[1] in listed
        )
        if not offered:
            return None
    bundle = catalogue.subagents.bundles[name]
    where = bundle.where
    # Re-labelled from the source it was *read* under to the one it is *mounted*
    # under. `skill_registry.read` calls a root source `catalogue`, and a bundle
    # is mounted under the folder's own name -- so the two halves of this return
    # value named one skill two ways, and the subagent was told about none of it.
    return (
        tuple(
            skill_registry.qualified(where, skill_registry.split_qualified(key)[1])
            for key in offered
        ),
        # Every directory under the one label, so a skill's identity does not
        # depend on which of them it sits in -- and `bundled_skills` has already
        # refused two that both hold one name.
        tuple((route, where) for route, _ in bundled_skill_mounts(where, bundle.skills)),
    )


def _activated_subagents(
    cfg: Config,
    capabilities: Capabilities,
    *,
    catalogue: Definitions | None = None,
    held: frozenset[str] | None = None,
    #: The agent this build opened, for the refusal's listing alone -- what it judges
    #: is every definition, because a name nothing defines is the file's mistake
    #: whoever is asking. `None` where a build resolved no spec.
    agent: AgentSpec | None = None,
) -> tuple[Mapping[str, Any], tuple[str, ...]]:
    """Which subagents this request wired, and every definition available."""
    if capabilities.subagents is None:
        return {}, ()
    defined = defined_subagents(cfg, catalogue=catalogue)
    # A property of the definitions rather than of this request, and asked here
    # because here is where the set is known -- before anything reads a spec.
    refuse_cycles(defined)
    # There is deliberately *no* matching check that every definition names a
    # runnable model. Helper depth is structural -- a catalogue asking for two
    # levels is incoherent however it is used, and no request can rescue it. An
    # unrunnable model is not: `run_on` exists precisely so a caller can put a
    # shipped subagent on a model their credentials reach, and a catalogue-wide
    # refusal would fire before the override could apply. So it stays per-subagent,
    # at `as_subagent`, where the override has already been resolved.
    # `ALL` is every subagent this caller reaches, resolved here because here is where
    # "what it defines" is known. Every definition would hand two of a name to a
    # caller who reaches one of them, and the refusal would name the other.
    activated = (
        within_reach(defined, defined, held)
        if capabilities.subagents == ALL
        else capabilities.subagents
    )
    refuse_unoffered(
        activated,
        offered=defined,
        kind="subagent",
        subject="this request",
        listing=offered_to(defined, held, by=() if agent is None else (agent,)),
    )
    # After the unknown-name check, not before: naming one that does not exist
    # and naming two that do are different mistakes, and the first would
    # otherwise be reported as the second.
    refuse_two_of_a_name(activated, subject="this request")
    # Dropped rather than refused. The service settled a request's own names for this
    # caller before anything clamped them (`settled_request`), and an agent's were
    # refused as its file wrote them (`refuse_misnamed_by`), so what is out of reach
    # here is an entry of a list -- an agent's, or a deployment's grant -- and a list
    # narrows a subagent out of reach as silently as one it never held.
    return defined, within_reach(activated, defined, held)
