"""What a request turns on: the skills and the delegates it activates.

One module because the two answer the same question about different kinds. A
definition names what it *may* reach; a request narrows that; and what is left
is what the agent is actually built with. Every function here is that
subtraction for one kind, plus the two that say why a delegate did not survive
it -- `unrunnable_delegates` for one this deployment cannot build at all, and
`indistinct_delegates` for one that asked to run elsewhere and did not.

Separate from assembly because assembly only ever consumes the answers. It calls
five of these once each and passes the results into the graph; none of them
calls back, and none of them calls the tool surface or the middleware beside
them. That independence is why this is a module rather than a region of a
larger one.

The two skill denials are here rather than with the permissions in `agent`
because they are computed *from* what was activated: a rule denying reads of a
skill this request left out is the same subtraction, expressed as a route the
agent's backend can carry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from deepagents import FilesystemPermission

from kingfisher.config import ConfigError
from kingfisher.domain import layout
from kingfisher.domain.capabilities import ALL, Capabilities, refuse_unoffered
from kingfisher.domain.subagent import RunOn, SubagentSpec
from kingfisher.domain.subagent.rules import refuse_cycles, refuse_two_of_a_name
from kingfisher.infrastructure.catalogue import Definitions
from kingfisher.infrastructure.catalogue.layered import for_session
from kingfisher.infrastructure.harness.backend import SKILLS_ROUTE, bundled_skills_route
from kingfisher.infrastructure.harness.delegation import indistinct, model_for
from kingfisher.skills import registry as skill_registry
from kingfisher.skills.registry import SkillRegistry

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from kingfisher.config import Config


def available_skills(
    cfg: Config, session_dir: Path | None, *, catalogue: Definitions | None = None
) -> tuple[str, ...]:
    """Every skill this request may activate: the catalogue, plus its own.

    `catalogue` says where the shared half is read from, falling back to `cfg`.
    What a session adds, and how the two halves merge, is `layered.for_session`
    -- the rule lives there because it differs per kind and a reader comparing
    them should not have to visit two functions to see the difference.

    The catalogue half is the *registry*, not the directory listing, and that is
    the point of it: a directory that looks like a skill and will not parse
    used to be advertised here, accepted by the build, allowed through the
    filter, and then absent from an agent that reported nothing wrong. Asking
    what will be loaded makes naming one an ordinary unknown-skill refusal.

    A session's own skills stay a listing. They are written by `uploads`, which
    reads each header to file it under the name inside it, so the two cannot
    disagree the way a catalogue's could -- and a request's uploads are checked
    when they are provisioned rather than here.
    """
    return activatable_skills(cfg, session_dir, catalogue=catalogue).names


def activatable_skills(
    cfg: Config, session_dir: Path | None, *, catalogue: Definitions | None = None
) -> SkillRegistry:
    """One registry for both halves: the catalogue, plus this request's own.

    The single answer to "what may this request activate", and it is a single
    answer because the last time there were two they disagreed. `available_skills`
    merged the session's directory listing over the catalogue registry while
    `build_agent` resolved against the catalogue registry alone, so every
    uploaded skill was advertised and then refused as unknown -- the whole
    feature, not an edge of it.

    The catalogue half is cached for the life of the deployment; the session
    half is read per turn, because that is when it arrives. One listing of a
    directory holding at most a handful of skills.
    """
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
    """Every subagent this request may activate: the catalogue, plus its own.

    A function because two callers need the same answer: `build_agent`, which
    wants the specs, and the service, which wants only the names so it can say
    which of them a request did not grant. Written out at both, the rule about
    what a session adds to the catalogue would exist twice.
    """
    return dict(for_session(catalogue or Definitions.from_config(cfg), session_dir).subagents.specs)


def unrunnable_delegates(
    cfg: Config, *, catalogue: Definitions | None = None
) -> tuple[tuple[str, str], ...]:
    """`(name, why)` for each defined delegate this deployment cannot run.

    Every definition the catalogue holds, not the ones a request activated --
    which is the difference from `indistinct_delegates` beside it, and the whole
    point. A delegate binding an alias to a model on an endpoint with no key is
    invisible until somebody activates it: the workspace loads, the listing is
    clean, and the failure waits for the first request that names it.

    Through `model_for` and `resolve`, the two calls a build makes, so this
    cannot come to disagree with what actually happens. Both are needed and
    neither is enough: `model_for` catches an alias nothing binds and a delegate
    whose every candidate was passed over, and returns a model *name*; whether
    that name can be reached is `resolve`'s question, and it is the one the
    dropped-endpoint case fails.

    Reported, never refused, and never called -- no model is built and nothing
    goes over a network. It costs two dictionary lookups per definition, which
    is what lets `doctor` run it before a deployment rather than after.
    """
    from kingfisher.infrastructure.harness.delegation import model_for  # noqa: PLC0415

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
    """`(name, why)` for each activated delegate that asked to run elsewhere and
    did not.

    Asked after the build rather than during it, the way `reporting.withheld_by_kind`
    is: `build_agent` returns a graph, and a fact about the run is not one of
    the things a graph can carry. It re-resolves through `model_for`,
    the same call the build makes, so the two cannot come to disagree about
    where a delegate ended up.
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
    """One skill's own directory, as a rule the agent's routes can carry.

    The registry reads a catalogue through a backend rooted at the catalogue
    itself, so a skill's `path` is `/research/lookup/<file>` -- where the
    agent addresses that same file under `/skills/`. Two
    roots, two spellings, and a rule written in the wrong one is not merely
    wrong: `FilesystemMiddleware` refuses *every* permission when the backend
    can execute unless each rule is scoped to a route, so one unrouted path
    takes the whole deny list down with it. Found by a test doing exactly that.
    """
    return f"{SKILLS_ROUTE}{read_at.lstrip('/').rsplit('/', 1)[0]}/**"


def _skill_denials(activated: tuple[str, ...], registry: Any) -> list[FilesystemPermission]:
    """Deny reads of skills this request did not activate.

    The listing filter only stops the agent being *told*; this stops the file
    tools reading it anyway. Neither stops `execute`, which bypasses tool-level
    permissions entirely — so this is a real boundary only for a request that
    did not activate the shell.

    Built from each skill's own path rather than from its name, and that is the
    fix rather than a tidy-up. This wrote `/skills/{name}/**`, which is where a
    skill sits only while every skill sits at the top level. A skill in a folder
    lives at `/skills/research/lookup/`, so the rule denied a path that does not
    exist and the file tools could still read it -- a boundary failing open,
    silently, the moment folders were possible.
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
    """The skills a delegate brings itself, and where they are mounted.

    Answered from `bundled_skills`, which asked deepagents what it will actually
    load rather than listing directories -- the distinction `skills.registry`
    exists for, and the reason a delegate is never told about a skill that will
    not load.

    `None` when there are none, which is every delegate without a bundle, so the
    branch that folds these in never runs for them.
    """
    registry = catalogue.bundled_skills.get(name)
    if registry is None or not registry.offered:
        return None
    bundles = getattr(catalogue.subagents, "bundles", None) or {}
    where = bundles[name].where
    return tuple(registry.offered), (bundled_skills_route(where), where)


def _activated_subagents(
    cfg: Config,
    capabilities: Capabilities,
    session_dir: Path | None,
    *,
    catalogue: Definitions | None = None,
) -> tuple[Mapping[str, Any], tuple[str, ...]]:
    """Which delegates this request wired, and every definition available.

    Resolved before the tools rather than beside them, because whether any
    activated definition *names* a tool decides whether the tool probe has to
    run at all. Nothing here reads a tool, so the order costs nothing.
    """
    if capabilities.subagents is None:
        return {}, ()
    defined = defined_subagents(cfg, session_dir, catalogue=catalogue)
    # A property of the definitions, not of this request, so it is asked once
    # the merged set is known and before anything reads a single spec. An
    # upload can break it by shadowing a catalogue name, which is why it cannot
    # be checked at seed time and left at that.
    refuse_cycles(defined)
    # There is deliberately *no* matching check that every definition names a
    # runnable model. It was written and taken out again: the two rules look
    # alike and are not. Helper depth is structural -- a catalogue asking for
    # two levels is incoherent however it is used, and no request can rescue it.
    # An unrunnable model is not: `run_on` exists precisely so a caller can put
    # a shipped delegate on a model their credentials reach, without editing a
    # file they may not own, and a catalogue-wide refusal would fire before the
    # override could apply and defeat it.
    #
    # So it stays per-delegate, at `as_subagent`, where the override has already
    # been resolved -- and seeding a definition you cannot run costs nothing until
    # you activate it.
    # `ALL` is every subagent the workspace defines, resolved here because here
    # is where "what it defines" is known.
    activated = tuple(defined) if capabilities.subagents == ALL else capabilities.subagents
    refuse_unoffered(activated, offered=defined, kind="subagent", subject="this request")
    # After the unknown-name check, not before: naming one that does not exist
    # and naming two that do are different mistakes, and the first would
    # otherwise be reported as the second.
    refuse_two_of_a_name(activated, subject="this request")
    return defined, activated
