"""What a request turns on: the skills and the delegates it activates."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from deepagents import FilesystemPermission

from kingfisher import layout
from kingfisher.config import ConfigError
from kingfisher.domain.capabilities import ALL, Capabilities, refuse_unoffered
from kingfisher.infrastructure.catalogue import Definitions
from kingfisher.infrastructure.catalogue.layered import for_session
from kingfisher.infrastructure.harness.backend import bundled_skills_route
from kingfisher.infrastructure.harness.subagents import indistinct, model_for
from kingfisher.kinds.skills import registry as skill_registry
from kingfisher.kinds.skills.registry import SkillRegistry
from kingfisher.kinds.subagents.rules import refuse_cycles, refuse_two_of_a_name
from kingfisher.kinds.subagents.spec import RunOn, SubagentSpec
from kingfisher.layout import SKILLS_ROUTE

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from kingfisher.config import Config


def available_skills(
    cfg: Config, session_dir: Path | None, *, catalogue: Definitions | None = None
) -> tuple[str, ...]:
    """Every skill this request may activate: the catalogue, plus its own."""
    return activatable_skills(cfg, session_dir, catalogue=catalogue).names


def activatable_skills(
    cfg: Config, session_dir: Path | None, *, catalogue: Definitions | None = None
) -> SkillRegistry:
    """One registry for both halves: the catalogue, plus this request's own."""
    resolved = catalogue or Definitions.from_config(cfg)
    uploaded = (
        None
        if session_dir is None
        else session_dir / layout.SKILLS / layout.UPLOADED_SKILL_DIR
    )
    return resolved.registry.merged(skill_registry.read_uploaded(uploaded))


def defined_subagents(
    cfg: Config, session_dir: Path | None, *, catalogue: Definitions | None = None
) -> dict[str, SubagentSpec]:
    """Every subagent this request may activate: the catalogue, plus its own."""
    return dict(for_session(catalogue or Definitions.from_config(cfg), session_dir).subagents.specs)


def unrunnable_delegates(
    cfg: Config, *, catalogue: Definitions | None = None
) -> tuple[tuple[str, str], ...]:
    """`(name, why)` for each defined delegate this deployment cannot run."""
    found: list[tuple[str, str]] = []
    for name, spec in sorted(defined_subagents(cfg, None, catalogue=catalogue).items()):
        try:
            model = model_for(spec)
            if model is not None:
                cfg.models.resolve(model)
        except ConfigError as exc:
            found.append((name, str(exc)))
    return tuple(found)


def indistinct_delegates(
    cfg: Config,
    capabilities: Capabilities,
    session_dir: Path | None,
    *,
    catalogue: Definitions | None = None,
    run_on: Mapping[str, RunOn] | None = None,
) -> tuple[tuple[str, str], ...]:
    """`(name, why)` for each activated delegate that asked to run elsewhere and did
    not.
    """
    if capabilities.subagents is None:
        return ()
    defined = defined_subagents(cfg, session_dir, catalogue=catalogue)
    activated = tuple(defined) if capabilities.subagents == ALL else capabilities.subagents
    wanted = run_on or {}

    found = []
    for name in activated:
        spec = defined.get(name)
        if spec is None:
            continue  # `build_agent` refuses this; reporting is not its job
        try:
            model = model_for(spec, override=wanted.get(name))
        except ConfigError:
            # An unbound alias, or a model this deployment cannot run. The build
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
            operations=["read"], paths=[_denied_path(one["path"])], mode="deny"
        )
        for key, one in registry.offered.items()
        if key not in allowed
    ]


def _private_skills(
    catalogue: Definitions, name: str
) -> tuple[tuple[str, ...], tuple[str, str]] | None:
    """The skills a delegate brings itself, and where they are mounted."""
    registry = catalogue.bundled_skills.get(name)
    if registry is None or not registry.offered:
        return None
    bundles = getattr(catalogue.subagents, "bundles", None) or {}
    where = bundles[name].where
    # Re-labelled from the source it was *read* under to the one it is *mounted*
    # under. `skill_registry.read` calls a root source `catalogue`, and a bundle
    # is mounted under the folder's own name -- so the two halves of this return
    # value named one skill two ways, and the delegate was told about none of it.
    return (
        tuple(
            skill_registry.qualified(where, skill_registry.split_qualified(key)[1])
            for key in registry.offered
        ),
        (bundled_skills_route(where), where),
    )


def _activated_subagents(
    cfg: Config,
    capabilities: Capabilities,
    session_dir: Path | None,
    *,
    catalogue: Definitions | None = None,
) -> tuple[Mapping[str, Any], tuple[str, ...]]:
    """Which delegates this request wired, and every definition available."""
    if capabilities.subagents is None:
        return {}, ()
    defined = defined_subagents(cfg, session_dir, catalogue=catalogue)
    # A property of the definitions, not of this request, so it is asked once
    # the merged set is known and before anything reads a single spec. An
    # upload can break it by shadowing a catalogue name, which is why it cannot
    # be checked at seed time and left at that.
    refuse_cycles(defined)
    # There is deliberately *no* matching check that every definition names a
    # runnable model. Helper depth is structural -- a catalogue asking for two
    # levels is incoherent however it is used, and no request can rescue it. An
    # unrunnable model is not: `run_on` exists precisely so a caller can put a
    # shipped delegate on a model their credentials reach, and a catalogue-wide
    # refusal would fire before the override could apply. So it stays per-delegate,
    # at `as_subagent`, where the override has already been resolved.
    # `ALL` is every subagent the workspace defines, resolved here because here
    # is where "what it defines" is known.
    activated = tuple(defined) if capabilities.subagents == ALL else capabilities.subagents
    refuse_unoffered(activated, offered=defined, kind="subagent", subject="this request")
    # After the unknown-name check, not before: naming one that does not exist
    # and naming two that do are different mistakes, and the first would
    # otherwise be reported as the second.
    refuse_two_of_a_name(activated, subject="this request")
    return defined, activated
