"""What reading a deployment's definitions needs that no one kind owns."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from functools import cached_property
from pathlib import Path

from kingfisher.config import Config, ConfigError
from kingfisher.domain.ports import (
    AgentRepository,
    MiddlewareRepository,
    SkillRepository,
    SubagentRepository,
    ToolRepository,
)
from kingfisher.kinds.agents.catalogue import LocalAgentRepository
from kingfisher.kinds.agents.spec import DIRECTORY as AGENT_DIRECTORY
from kingfisher.kinds.middlewares.catalogue import LocalMiddlewareRepository, NoMiddleware
from kingfisher.kinds.skills import registry as skill_registry
from kingfisher.kinds.skills.catalogue import LocalSkillRepository
from kingfisher.kinds.skills.registry import SkillRegistry
from kingfisher.kinds.subagents.catalogue import LocalSubagentRepository
from kingfisher.kinds.subagents.rules import refuse_miscounted
from kingfisher.kinds.tools.catalogue import LocalToolRepository
from kingfisher.kinds.tools.spec import Offering


@dataclass(frozen=True)
class Definitions:
    """This deployment's definitions: one repository per kind."""

    agents: AgentRepository
    skills: SkillRepository
    subagents: SubagentRepository
    tools: ToolRepository
    #: Last and defaulted, unlike its four siblings, because it arrived after
    #: them. `NoMiddleware` says what a deployment that offers none is offering;
    #: a required field would have made every caller spelling the other four out
    #: stop working, which is the breakage `from_config` reads roots with `.get`
    #: to avoid.
    middlewares: MiddlewareRepository = field(default_factory=NoMiddleware)

    @cached_property
    def registry(self) -> SkillRegistry:
        """What the agent will actually be told about, asked of deepagents."""
        return skill_registry.read(self.skills)

    @cached_property
    def bundled_tools(self) -> Mapping[str, ToolRepository]:
        """Each subagent's own tools, by the name a grant would use."""
        bundles = self.subagents.bundles
        if not bundles:
            return {}
        # Asked of the bundle rather than built here. What backs one is the bundle's
        # own business now that there are two backings -- a folder to walk, or the
        # objects a definition carried -- and a second reading of that here would be
        # the place the two could disagree about what a delegate holds.
        return {
            name: repository
            for name, bundle in bundles.items()
            if (repository := bundle.tools) is not None
        }

    @cached_property
    def bundled_skills(self) -> Mapping[str, SkillRegistry]:
        """Each subagent's own skills, as deepagents will actually load them."""
        bundles = self.subagents.bundles
        if not bundles:
            return {}
        return {
            name: skill_registry.read(LocalSkillRepository(bundle.skills))
            for name, bundle in bundles.items()
            if bundle.skills is not None
        }

    def warm(self) -> Definitions:
        """Read all three now, so a broken definition fails here."""
        _ = self.agents.specs, self.skills.names, self.subagents.specs, self.tools.found
        # The fifth kind, which this read for none of the time it has existed.
        # `middlewares/*.py` is Python that has to import, exactly like `tools/*.py`,
        # and the refusal for a class that is not an `AgentMiddleware` was written
        # to fire "as the directory is read rather than at the first turn" -- true
        # of the refusal and not of anything that read the directory, because
        # nothing did until a definition named one.
        _ = self.middlewares.classes
        _ = self.registry
        # A bundle's tools are imported here for the reason every other kind is,
        # and the reason survives the fact that only one delegate can call them:
        # a private tool is still Python that has to import, and a deployment
        # that starts, reports itself fine, and fails on the first request that
        # happens to activate `surveyor` is the shape of the bug `list` exiting
        # zero over a broken agent catalogue already was. Encapsulation decides
        # who may *call* a tool, not whether it is allowed to be broken.
        for repository in self.bundled_tools.values():
            _ = repository.found
        # Skills are read here too, and the difference from tools is what
        # happens next rather than whether it happens: a skill that will not
        # load is reported by `unloadable` and never fatal, which is the rule
        # `list` already follows -- a broken tool exits 1, a broken skill does
        # not, because a run works without it.
        _ = self.bundled_skills
        # A definition saying where its tools live is checked here for the same
        # reason the reading happens here: it is a claim about this catalogue,
        # both halves are now in hand, and a stale path found on the first turn
        # that activates one delegate is a deployment that started while broken.
        offers = Offering.of(self.tools.found)
        for spec in self.subagents.specs.values():
            offers.refuse_moved(spec.tool_sources, subject=f"subagent {spec.name!r}")
        # And a definition saying what its own folder holds, checked here for the
        # same reason and against the two readings above: both halves are now in
        # hand, and neither half alone can tell whether the claim is still true.
        for name, spec in self.subagents.specs.items():
            where, tools, skills = self.bundled(name)
            refuse_miscounted(spec, where=where, tools=tools, skills=skills)
        return self

    def bundled(self, name: str) -> tuple[str | None, tuple[str, ...], tuple[str, ...]]:
        """Where one subagent's own folder is and what is in it, by the name a grant uses.

        `None` for a definition that owns no folder, which is most of them and is not
        the same answer as a folder holding nothing.
        """
        bundle = self.subagents.bundles.get(name)
        tools = self.bundled_tools.get(name)
        skills = self.bundled_skills.get(name)
        return (
            None if bundle is None else bundle.where,
            () if tools is None else tuple(one.name for one in tools.found),
            () if skills is None else tuple(skills.names),
        )

    @classmethod
    def from_config(cls, cfg: Config) -> Definitions:
        """The deployment's own directories, without staging anything."""
        return cls.from_roots(cfg.catalogue_roots)

    @classmethod
    def from_roots(cls, roots: Mapping[str, Path]) -> Definitions:
        """Four directories on this host, as four local repositories."""
        return cls(
            agents=LocalAgentRepository(
                Path(roots.get("agents", Path(roots["skills"]).parent / AGENT_DIRECTORY))
            ),
            # `.get` with a fallback, the same as `agents` above and for the
            # reason written there: a deployment that spelled out the roots it
            # knew about should not stop starting because a fifth kind exists.
            middlewares=LocalMiddlewareRepository(
                Path(roots.get("middlewares", Path(roots["skills"]).parent / "middlewares"))
            ),
            skills=LocalSkillRepository(Path(roots["skills"])),
            subagents=LocalSubagentRepository(Path(roots["subagents"])),
            tools=LocalToolRepository(Path(roots["tools"])),
        )


#: The kinds, taken from the type that already has one field per kind.
DEFINITION_KINDS: tuple[str, ...] = tuple(f.name for f in fields(Definitions))

#: The kinds a *supplied* catalogue has to name and stage itself.
#:
#: `agents` and `middlewares` are both outside it, and for one reason: each
#: arrived after this seam was published, so a deployment that spelled out the
#: kinds it knew about must not stop starting because another exists. Left in,
#: `middlewares` stopped every supplied catalogue in the tree loading at once.
STAGED_KINDS: tuple[str, ...] = tuple(
    k for k in DEFINITION_KINDS if k not in (AGENT_DIRECTORY, "middlewares")
)


def resolve_definitions(
    cfg: Config, supplied: Definitions | Mapping[str, Path] | None = None
) -> Definitions:
    """Where this deployment's definitions are read from, settled once.

    * **Derived from `cfg`** -- kingfisher's own, so they are created. That extends
      to a relocated catalogue what `ensure_layout` does for a workspace: without
      it, `KINGFISHER_SKILLS_DIR` pointing somewhere that does not exist yet yields
      an empty catalogue and a clean start.
    * **Supplied by the caller** -- theirs, so they must already be there.
      Creating one would hide a staging failure behind a catalogue that is
      merely empty, and an agent told about no skills at all is exactly the
      silent-emptiness this module's neighbours keep refusing.
    """
    if supplied is None:
        derived = cfg.catalogue_roots
        for path in derived.values():
            path.mkdir(parents=True, exist_ok=True)
        return Definitions.from_config(cfg)

    # Either shape. A deployment stages directories and hands over a mapping,
    # which is the documented seam; something that already holds a `Definitions`
    # -- another kingfisher, a test fixture -- should not have to take it apart
    # to pass it back.
    if not isinstance(supplied, Definitions):
        if missing := tuple(kind for kind in STAGED_KINDS if kind not in supplied):
            msg = (
                f"catalogue is missing {', '.join(missing)}; it names all of "
                f"{', '.join(STAGED_KINDS)}, since a deployment that leaves one out "
                "means an empty one rather than the configured one"
            )
            raise ConfigError(msg)
        supplied = Definitions.from_roots(supplied)

    # Checked however it arrived, and only where there is something to check: a
    # repository with a `root` of `None` has no directory that could be missing.
    roots = {kind: getattr(supplied, kind).root for kind in STAGED_KINDS}
    if absent := tuple(
        f"{kind} ({path})" for kind, path in roots.items() if path is not None and not path.is_dir()
    ):
        msg = (
            f"catalogue names {', '.join(absent)}, which is not a directory; "
            "a supplied catalogue is staged by whoever supplies it, and kingfisher "
            "will not create one in case the staging is what failed"
        )
        raise ConfigError(msg)
    return supplied
