"""What a request turns on: the skills and the subagents it activates."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from deepagents import FilesystemPermission

from kingfisher.config import ConfigError
from kingfisher.domain.access import reaches
from kingfisher.domain.capabilities import ALL, Capabilities, refuse_unoffered
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
    from collections.abc import Iterable, Mapping

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


def unrunnable_subagents(
    cfg: Config, *, catalogue: Definitions | None = None
) -> tuple[tuple[str, str], ...]:
    """`(name, why)` for each defined subagent this deployment cannot run."""
    found: list[tuple[str, str]] = []
    for name, spec in sorted(defined_subagents(cfg, catalogue=catalogue).items()):
        try:
            model = resolved_model(spec.wanted)
            if model is not None:
                cfg.models.resolve(model)
        except ConfigError as exc:
            found.append((name, str(exc)))
    return tuple(found)


def for_the_caller(
    agent: AgentSpec | None, asked: Capabilities, *, held: frozenset[str] | None
) -> Capabilities:
    """What a request may use: what the agent declares for this caller, narrowed by
    what the request asked for.
    """
    return agent.declares(held).intersect(asked) if agent is not None else asked


def within_reach(
    names: Iterable[str], defined: Mapping[str, SubagentSpec], held: frozenset[str] | None
) -> tuple[str, ...]:
    """`names`, less each subagent whose own `source_ids:` does not reach this caller.

    The build drops those, so whatever tells a caller about subagents drops them too:
    out of reach reads as not offered, and a report naming one says it exists.
    """
    if held is None:
        return tuple(names)
    return tuple(name for name in names if reaches(defined[name].source_ids, held))


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
    # `ALL` is every subagent the workspace defines, resolved here because here
    # is where "what it defines" is known.
    activated = tuple(defined) if capabilities.subagents == ALL else capabilities.subagents
    refuse_unoffered(activated, offered=defined, kind="subagent", subject="this request")
    # After the unknown-name check, not before: naming one that does not exist
    # and naming two that do are different mistakes, and the first would
    # otherwise be reported as the second.
    refuse_two_of_a_name(activated, subject="this request")
    # A subagent's own `source_ids:` holds wherever it is used, so one this caller
    # cannot reach is dropped as an agent's narrowed entry is -- after the refusals,
    # which are about what the request named and must not depend on who sent it.
    return defined, within_reach(activated, defined, held)
