"""What reading a deployment's definitions needs that no one kind owns."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from functools import cached_property
from pathlib import Path

from kingfisher.core.access.capabilities import ALL, SEPARATOR
from kingfisher.core.config import NO_MOUNTS, Config, ConfigError, definition_roots_for
from kingfisher.core.definitions.agents.catalogue import LocalAgentRepository
from kingfisher.core.definitions.agents.spec import DIRECTORY as AGENT_DIRECTORY
from kingfisher.core.definitions.middlewares.catalogue import LocalMiddlewareRepository
from kingfisher.core.definitions.skills import registry as skill_registry
from kingfisher.core.definitions.skills.catalogue import LocalSkillRepository
from kingfisher.core.definitions.skills.registry import SkillRegistry
from kingfisher.core.definitions.subagents.catalogue import LocalSubagentRepository
from kingfisher.core.definitions.subagents.rules import refuse_miscounted
from kingfisher.core.definitions.subagents.spec import SubagentError
from kingfisher.core.definitions.tools.catalogue import CarriedTools, LocalToolRepository, ToolError
from kingfisher.core.definitions.tools.spec import Found, Offering
from kingfisher.core.layout import RESERVED_SKILL_FOLDER


@dataclass(frozen=True)
class Definitions:
    """This deployment's definitions: one repository per kind."""

    agents: LocalAgentRepository
    skills: LocalSkillRepository
    subagents: LocalSubagentRepository
    tools: LocalToolRepository
    middlewares: LocalMiddlewareRepository

    @cached_property
    def registry(self) -> SkillRegistry:
        """What the agent will actually be told about, asked of deepagents."""
        refuse_unmountable(self.skills.root, self.skills.mounts)
        return skill_registry.read(self.skills)

    @cached_property
    def bundled_tools(self) -> Mapping[str, LocalToolRepository | CarriedTools]:
        """Each subagent's own tools, by the name a grant would use."""
        bundles = self.subagents.bundles
        if not bundles:
            return {}
        # Asked of the bundle rather than built here. What backs one is the bundle's
        # own business now that there are two backings -- a folder to walk, or the
        # objects a definition carried -- and a second reading of that here would be
        # the place the two could disagree about what a subagent holds.
        return {
            name: repository
            for name, bundle in bundles.items()
            if (repository := bundle.tools) is not None
        }

    @property
    def audienced_tools(self) -> Mapping[str, Found]:
        """Every tool that says who it admits, the catalogue's and each bundle's.

        Keyed by where each lives, so a refusal names the file rather than a name two
        files may share.
        """
        found = (
            *self.tools.found,
            *(one for bundle in self.bundled_tools.values() for one in bundle.found),
        )
        return {one.reference: one for one in found if one.audience != ALL}

    @cached_property
    def bundled_skills(self) -> Mapping[str, SkillRegistry]:
        """Each subagent's own skills, as deepagents will actually load them."""
        bundles = self.subagents.bundles
        if not bundles:
            return {}
        return {
            name: _one_registry(name, bundle.skills)
            for name, bundle in bundles.items()
            if bundle.skills
        }

    @cached_property
    def bundled_misplaced(self) -> Mapping[str, tuple[str, ...]]:
        """Each subagent's skills sitting too deep in its own folder to load, for a
        listing and for the refusal of a definition that lists one.
        """
        return {
            name: found
            for name, bundle in self.subagents.bundles.items()
            if bundle.skills and (found := _misplaced_in(bundle.skills))
        }

    def warm(self) -> Definitions:
        """Read every kind now, so a broken definition fails here."""
        _ = self.agents.specs, self.subagents.specs, self.tools.found
        # The fifth kind, which this read for none of the time it has existed.
        # `middlewares/*.py` is Python that has to import, exactly like `tools/*.py`,
        # and the refusal for a class that is not an `AgentMiddleware` was written
        # to fire "as the directory is read rather than at the first turn" -- true
        # of the refusal and not of anything that read the directory, because
        # nothing did until a definition named one.
        _ = self.middlewares.classes
        _ = self.registry
        # A bundle's tools are imported here for the reason every other kind is,
        # and the reason survives the fact that only one subagent can call them:
        # a private tool is still Python that has to import, and a deployment
        # that starts, reports itself fine, and fails on the first request that
        # happens to activate `surveyor` is the shape of the bug `list` exiting
        # zero over a broken agent catalogue already was. Encapsulation decides
        # who may *call* a tool, not whether it is allowed to be broken.
        for name, repository in self.bundled_tools.items():
            _ = bundled_tool_names(name, repository)
        # Skills are read here too, and the difference from tools is what
        # happens next rather than whether it happens: a skill that will not
        # load is reported by `unloadable` and never fatal, which is the rule
        # `list` already follows -- a broken tool exits 1, a broken skill does
        # not, because a run works without it.
        _ = self.bundled_skills
        # A definition saying where its tools live is checked here for the same
        # reason the reading happens here: it is a claim about this catalogue,
        # both halves are now in hand, and a stale path found on the first turn
        # that activates one subagent is a deployment that started while broken.
        offers = Offering.of(self.tools.found)
        for spec in self.subagents.specs.values():
            offers.refuse_moved(spec.tool_sources, subject=f"subagent {spec.name!r}")
        # And what a definition lists from its own folder, checked here for the same
        # reason and against the two readings above: both halves are now in hand,
        # and neither alone can tell whether the list still matches the folder.
        for name, spec in self.subagents.specs.items():
            where, tools, skills = self.bundled(name)
            refuse_miscounted(
                spec,
                where=where,
                tools=tools,
                skills=skills,
                buried=self.bundled_misplaced.get(name, ()),
            )
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
        return cls.from_roots(cfg.catalogue_roots, skills_mounts=cfg.skills_mounts)

    @classmethod
    def from_roots(
        cls, roots: Mapping[str, Path], *, skills_mounts: Mapping[str, Path] = NO_MOUNTS
    ) -> Definitions:
        """This host's directories, as local repositories."""
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
            skills=LocalSkillRepository(Path(roots["skills"]), mounts=skills_mounts),
            subagents=LocalSubagentRepository(Path(roots["subagents"])),
            tools=LocalToolRepository(Path(roots["tools"])),
        )


#: The kinds, taken from the type that already has one field per kind.
DEFINITION_KINDS: tuple[str, ...] = tuple(f.name for f in fields(Definitions))

def resolve_definitions(cfg: Config) -> Definitions:
    """This deployment's definitions, read from the folder `cfg` names.

    The folder has to exist, and a kind folder missing inside it means none of that
    kind. Nothing is created: the folder may be one several deployments read, and making
    one would hide a mount or a staging that failed behind a catalogue that is merely
    empty. An agent told about no skills at all is exactly the silent-emptiness this
    module's neighbours keep refusing.
    """
    if not cfg.definitions.is_dir():
        msg = (
            f"KINGFISHER_DEFINITIONS_DIR is {cfg.definitions}, which is not a directory. "
            "`kingfisher seed` makes one and fills it"
        )
        raise ConfigError(msg)
    return Definitions.from_roots(
        definition_roots_for(cfg.definitions), skills_mounts=cfg.skills_mounts
    )


def refuse_unmountable(root: Path, mounts: Mapping[str, Path]) -> None:
    """Refuse a skills mount that would hide, repeat or misname a skill."""
    taken = {entry.name for entry in root.iterdir()} if root.is_dir() else set()
    for label, mount in mounts.items():
        where = f"skills mount {label!r} ({mount})"
        if label in {skill_registry.CATALOGUE, RESERVED_SKILL_FOLDER}:
            msg = f"{where}: {label!r} already names part of the catalogue; pick another"
            raise ConfigError(msg)
        if SEPARATOR in label or "/" in label:
            msg = (
                f"{where}: a label is half of a skill's identity and a path segment, "
                f"so it may not contain {SEPARATOR!r} or '/'"
            )
            raise ConfigError(msg)
        # The route for a mount is longer than the catalogue's, so it wins: a folder
        # or skill of the same name in the catalogue would vanish without a word.
        if label in taken:
            msg = f"{where}: the catalogue at {root} has {label!r} too, and this would hide it"
            raise ConfigError(msg)
        # Not created: a mount is somebody else's directory, and making an empty one
        # would hide the failure to stage it behind an agent with no skills.
        if not mount.is_dir():
            msg = f"{where} is not a directory"
            raise ConfigError(msg)
        # Either way round lists the same skills twice, under two labels.
        others = [("the catalogue", root)]
        others += [(f"mount {k!r}", v) for k, v in mounts.items() if k != label]
        for name, other in others:
            one, two = mount.resolve(), other.resolve()
            if one.is_relative_to(two) or two.is_relative_to(one):
                msg = f"{where} overlaps {name} ({other}), so its skills would be listed twice"
                raise ConfigError(msg)


def _misplaced_in(directories: tuple[Path, ...]) -> tuple[str, ...]:
    """One subagent's misplaced skills, relative to the folder each sits in.

    Numbered as the folders' routes are when there are several, so `2/group/lookup`
    names the second -- a package's folders are all called `skills`.
    """
    one = len(directories) == 1
    return tuple(
        deep if one else f"{n}/{deep}"
        for n, directory in enumerate(directories, start=1)
        for deep in LocalSkillRepository(directory, flat=True).misplaced
    )


def bundled_tool_names(
    name: str, repository: LocalToolRepository | CarriedTools
) -> tuple[str, ...]:
    """One subagent's own tool names, with a failure named by whose folder it was in.

    `probe.py: ModuleNotFoundError ...` is a basename in somebody's folder, and which
    somebody is the one thing a reader needs: `doctor` gives that as the reason this
    fault has a row of its own -- "a reader has to be told which subagent to go and
    open" -- and the row did not tell them. The skills half has said it since it was
    written, in `_one_registry` below.

    Here rather than in the listing, because both readers come through this: a
    deployment that will not start refuses from `warm`, and that refusal is what an
    operator sees.
    """
    try:
        return tuple(sorted(one.name for one in repository.found))
    except (ToolError, SubagentError) as exc:
        msg = f"subagent {name!r}: {exc}"
        # The original traceback, carried over: `test_a_defect_reaches_the_refusal_it
        # _is_filed_under` keys a refusal by the deepest frame inside the package, and
        # a plain re-raise files every failure under this function -- so two refusals
        # in `core/definitions/tools/catalogue.py` would have read as reached while nothing
        # reached them.
        raise type(exc)(msg).with_traceback(exc.__traceback__) from exc


def _one_registry(name: str, directories: tuple[Path, ...]) -> SkillRegistry:
    """One bundle's skills, read from each of its directories and held as one."""
    read = [skill_registry.read(LocalSkillRepository(one, flat=True)) for one in directories]
    if len(read) == 1:
        return read[0]
    # Refused rather than merged: every directory is mounted under the bundle's one
    # label, so two holding a name would give the subagent one skill by that name
    # and hide the other -- whichever deepagents happened to list last.
    seen: dict[str, Path] = {}
    for directory, registry in zip(directories, read, strict=True):
        for taken in registry.taken:
            if taken in seen:
                msg = (
                    f"subagent {name!r} carries a skill called {taken!r} in both "
                    f"{seen[taken]} and {directory}; a subagent can hold one skill by "
                    f"a name, so rename one of them"
                )
                raise SubagentError(msg)
            seen[taken] = directory
    return SkillRegistry(
        offered={k: v for registry in read for k, v in registry.offered.items()},
        unloadable=tuple(u for registry in read for u in registry.unloadable),
        misfiled=tuple(m for registry in read for m in registry.misfiled),
        folders=tuple(f for registry in read for f in registry.folders),
    )
