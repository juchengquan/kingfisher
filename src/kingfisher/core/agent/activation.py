"""What a request turns on: the skills and the subagents it activates."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from deepagents import FilesystemPermission

from kingfisher.core.access.capabilities import (
    ALL,
    Capabilities,
    Selection,
    bare_name,
    narrowed,
    refuse_unoffered,
)
from kingfisher.core.access.reach import handed_work_by, within_reach
from kingfisher.core.agent.kinds.subagents import indistinct
from kingfisher.core.config import ConfigError
from kingfisher.core.definitions.catalogue import Definitions, bundled_skill_mounts
from kingfisher.core.definitions.skills import registry as skill_registry
from kingfisher.core.definitions.skills.registry import SkillRegistry
from kingfisher.core.definitions.subagents.rules import (
    refuse_cycles,
    refuse_two_of_a_name,
    resolved_model,
)
from kingfisher.core.definitions.subagents.spec import RunOn, SubagentSpec
from kingfisher.core.layout import SKILLS_ROUTE

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from kingfisher.core.config import Config
    from kingfisher.core.definitions.agents.spec import AgentSpec


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

    Shared by both kinds, so `doctor` answers for agents as it does for subagents.
    """
    found: list[tuple[str, str]] = []
    for name, spec in sorted(specs.items()):
        try:
            if spec.wanted is not None:
                cfg.models.resolve(spec.wanted)
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

    Nameable means admitted by its own `source_ids:` and named by an agent in `by`. A
    refusal about a *file* passes every agent the caller can open (`agents_to_ask`),
    since editing the file can name any of theirs; one about a *request* passes the
    agent it opened, since `Capabilities.subagents` only narrows what that one declares.

    Empty `by` lists the caller's own reach. `settled_request` passes none, which is
    right: it runs only under a `"*"` ceiling, where the two answers are one set.
    """
    reachable = handed_work_by(by, defined, held) if by else within_reach(defined, defined, held)
    return f"{tuple(sorted(reachable))}"


def agents_to_ask(cfg: Config, *, catalogue: Definitions | None = None) -> tuple[Any, ...]:
    """The agents whose activations decide what this caller can be handed work by.

    For the listing beside a refusal about a file, so read on a refusal path only.
    Every agent, unnarrowed: `narrowed_for` and `handed_work_by` already narrow for the
    caller, and restating that here would be a condition nothing can tell is right.
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

    `ceiling` is the deployment's grant under the agent's declaration. A list there
    narrows a typo and a name out of reach alike, silently, so only `"*"` leaves anything
    to settle.
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


def indistinct_subagents(  # noqa: PLR0913 -- the agent and caller make this the build's roster
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
    # A name nothing defines is skipped, not refused: `build_agent` refuses it.
    for name in within_reach((n for n in named if n in defined), defined, held):
        spec = defined[name]
        model = resolved_model(spec.wanted, override=wanted.get(name))
        try:
            why = indistinct(spec, cfg, model=model)
        except ConfigError:
            # The build refuses an unrunnable model with the message worth reading, and a
            # supplied graph is not built here at all: a report must not fail the turn.
            continue
        if why:
            found.append((name, why))
    return tuple(found)


def _denied_path(read_at: str) -> str:
    """One skill's own directory, as a rule the agent's routes can carry."""
    return f"{SKILLS_ROUTE}{read_at.lstrip('/').rsplit('/', 1)[0]}/**"


def _skill_denials(activated: tuple[str, ...], registry: Any) -> list[FilesystemPermission]:
    """Deny reads of skills this request did not activate.

    From each skill's own path, not its name: a skill in a folder is not at
    `/skills/{name}/`, and a rule there would leave it readable.
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
        # Only what the spec lists arrives.
        listed = set(spec.bundled.get("skills", ()))
        offered = tuple(
            key for key in offered if skill_registry.split_qualified(key)[1] in listed
        )
        if not offered:
            return None
    bundle = catalogue.subagents.bundles[name]
    where = bundle.where
    # Re-labelled from the source it was read under, which `skill_registry.read` calls
    # `catalogue`, to the folder it is mounted under: both halves of this value must name
    # a skill one way, or the subagent is told of none.
    return (
        tuple(
            skill_registry.qualified(where, skill_registry.split_qualified(key)[1])
            for key in offered
        ),
        # Every directory under one label, so a skill's identity does not depend on
        # which it sits in; `bundled_skills` has refused two holding one name.
        tuple((route, where) for route, _ in bundled_skill_mounts(where, bundle.skills)),
    )


def _activated_subagents(
    cfg: Config,
    capabilities: Capabilities,
    *,
    catalogue: Definitions | None = None,
    held: frozenset[str] | None = None,
    #: For the refusal's listing alone; what it judges is every definition, whoever
    #: asks. `None` where a build resolved no spec.
    agent: AgentSpec | None = None,
) -> tuple[Mapping[str, Any], tuple[str, ...]]:
    """Which subagents this request wired, and every definition available."""
    if capabilities.subagents is None:
        return {}, ()
    defined = defined_subagents(cfg, catalogue=catalogue)
    # Of the whole catalogue, before anything reads a spec.
    refuse_cycles(defined)
    # Deliberately no catalogue-wide check that every model is runnable: `run_on` lets a
    # caller move a subagent onto a model they reach, so that is refused per subagent, at
    # `as_subagent`, once the override is resolved.
    # `ALL` is every subagent this caller reaches, not every definition: that would hand
    # two of a name to a caller who reaches one, and the refusal would name the other.
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
        # The agent this build opened, not every agent the caller can open: a request
        # only narrows what that agent declares, so another's subagent is advice that
        # cannot be taken.
        listing=offered_to(defined, held, by=() if agent is None else (agent,)),
    )
    # After the unknown-name check, or a name nothing defines would be reported as
    # two of a name.
    refuse_two_of_a_name(activated, subject="this request")
    # Dropped, not refused: a request's own names and an agent's were refused already
    # (`settled_request`, `refuse_misnamed_by`), so what is out of reach here is an
    # entry of a list, and a list narrows silently.
    return defined, within_reach(activated, defined, held)
