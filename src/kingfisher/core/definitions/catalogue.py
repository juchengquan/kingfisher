"""What reading a deployment's definitions needs that no one kind owns."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from functools import cached_property
from pathlib import Path
from typing import Any, NamedTuple

from kingfisher.core.access import ALL, SEPARATOR
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
from kingfisher.core.definitions.tools.spec import FoundTool, Offering
from kingfisher.core.layout import BUNDLED_SKILLS_ROUTE, RESERVED_SKILL_FOLDER, SKILLS_ROUTE


class SkillSource(NamedTuple):
    """One place the agent is given a skills index."""

    #: The route the folder is served at.
    route: str
    #: The folder on this host.
    folder: Path
    #: The source in it that deepagents lists.
    source: str


class SkillMount(NamedTuple):
    """One skills directory mounted beneath the catalogue."""

    #: The route it is served at, under `/skills/`.
    route: str
    #: The directory on this host.
    directory: Path


@dataclass(frozen=True)
class Definitions:
    """This deployment's definitions: one repository per kind."""

    agents: LocalAgentRepository
    skills: LocalSkillRepository
    subagents: LocalSubagentRepository
    tools: LocalToolRepository
    middlewares: LocalMiddlewareRepository

    def __post_init__(self) -> None:
        # Here, not where skills are first read: a mount's label becomes a route segment,
        # and a plugin mounting these labels must not be the first to find two collide.
        refuse_unmountable(self.skills.root, self.skills.mounts)

    @cached_property
    def registry(self) -> SkillRegistry:
        """What the agent will actually be told about, asked of deepagents."""
        return skill_registry.read(self.skills)

    def skill_sources(self) -> tuple[SkillSource, ...]:
        """Every place the agent is given a skills index."""
        root = self.skills.root
        return (
            *(SkillSource(SKILLS_ROUTE, root, path) for _, path in skill_registry.sources(root)),
            *(
                SkillSource(route, folder, skill_registry.ROOT)
                for route, folder in self.skill_mounts()
            ),
        )

    def skill_mounts(self) -> tuple[SkillMount, ...]:
        """Every skills directory mounted beneath the catalogue.

        All under `/skills/`: the deny rule and sandbox profile that keep the catalogue
        read-only are scoped to that prefix. A skills directory mounted other than
        through this list escapes the check that a session serves what was read.
        """
        return (
            *(
                SkillMount(*pair)
                for bundle in _bundles_with_skills(self)
                for pair in bundled_skill_mounts(bundle.where, bundle.skills)
            ),
            # The segment the registry lists a mount under, so a skill's deny rule lands here.
            *(
                SkillMount(f"{SKILLS_ROUTE}{label}/", mount)
                for label, mount in self.skills.mounts.items()
            ),
        )

    @cached_property
    def bundled_tools(self) -> Mapping[str, LocalToolRepository | CarriedTools]:
        """Each subagent's own tools, by the name a grant would use."""
        bundles = self.subagents.bundles
        # Asked of the bundle, not rebuilt here: a folder or carried objects back it, and
        # a second reading could disagree with the bundle's about what it holds.
        return {
            name: repository
            for name, bundle in bundles.items()
            if (repository := bundle.tools) is not None
        }

    @property
    def audienced_tools(self) -> Mapping[str, FoundTool]:
        """Every tool that says who it admits, the catalogue's and each bundle's.

        Keyed by where each lives, so a refusal names the file, not a name two may share.
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
        return {
            name: _one_registry(name, bundle.skills)
            for name, bundle in bundles.items()
            if bundle.skills
        }

    @cached_property
    def bundled_misplaced(self) -> Mapping[str, tuple[str, ...]]:
        """Each subagent's skills sitting too deep in its own folder to load."""
        return {
            name: found
            for name, bundle in self.subagents.bundles.items()
            if bundle.skills and (found := _misplaced_in(bundle.skills))
        }

    def warm(self) -> Definitions:
        """Read every kind now, so a broken definition fails here."""
        _ = self.agents.specs, self.subagents.specs, self.tools.found
        # Middleware is Python that has to import, like tools, and its refusals belong at
        # startup rather than at the first turn naming one.
        _ = self.middlewares.classes
        _ = self.registry
        # A bundle's tools too, though only one subagent can call them: otherwise the
        # deployment starts fine and fails on the first request activating that subagent.
        for name, repository in self.bundled_tools.items():
            _ = bundled_tool_names(name, repository)
        # Bundled skills too, but one that will not load is reported in `unloadable`, never
        # fatal: a run works without it.
        _ = self.bundled_skills
        # Where a definition says its tools live is checked now, with both halves in hand,
        # rather than on the first turn that activates the subagent.
        offers = Offering.of(self.tools.found)
        for spec in self.subagents.specs.values():
            offers.refuse_moved(spec.tool_sources, subject=f"subagent {spec.name!r}")
        # Likewise what a definition lists from its own folder, against the folder.
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

        The place is `None` for a definition owning no folder, which is not an empty one.
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
            # `.get` with a fallback, as for `agents`: roots spelled out before a kind
            # existed must still start.
            middlewares=LocalMiddlewareRepository(
                Path(roots.get("middlewares", Path(roots["skills"]).parent / "middlewares"))
            ),
            skills=LocalSkillRepository(Path(roots["skills"]), mounts=skills_mounts),
            subagents=LocalSubagentRepository(Path(roots["subagents"])),
            tools=LocalToolRepository(Path(roots["tools"])),
        )


def _bundles_with_skills(catalogue: Definitions) -> tuple[Any, ...]:
    """Every bundle that has skills to mount, or none."""
    try:
        bundles = catalogue.subagents.bundles
    except SubagentError:
        # Not raised here: `--list` builds a backend on its way to printing this very
        # error, and `warm()` still refuses at startup.
        return ()
    return tuple(bundle for bundle in bundles.values() if bundle.skills)


def bundled_skills_route(where: str) -> str:
    """The route one bundle's skills are mounted at."""
    return f"{BUNDLED_SKILLS_ROUTE}{where}/"


def bundled_skill_mounts(where: str, directories: tuple[Path, ...]) -> tuple[tuple[str, Path], ...]:
    """`(route, directory)` for each of one bundle's skill directories.

    Several are numbered beneath the bundle's route, since a package's are all called
    `skills`; beneath rather than beside, so each inherits the `/skills/` read-only rule.
    """
    if len(directories) == 1:
        return ((bundled_skills_route(where), directories[0]),)
    return tuple(
        (bundled_skills_route(f"{where}/{n}"), directory)
        for n, directory in enumerate(directories, start=1)
    )


#: The kinds, taken from the type that already has one field per kind.
DEFINITION_KINDS: tuple[str, ...] = tuple(f.name for f in fields(Definitions))

def resolve_definitions(cfg: Config) -> Definitions:
    """This deployment's definitions, read from the folder `cfg` names.

    Nothing is created: making a missing folder would hide a failed mount or staging
    behind a catalogue that is merely empty. A missing kind folder means none of that kind.
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
        # A mount's route is longer, so it wins: a namesake in the catalogue would vanish.
        if label in taken:
            msg = f"{where}: the catalogue at {root} has {label!r} too, and this would hide it"
            raise ConfigError(msg)
        # Not created: an empty one would hide a failed staging behind an agent with no skills.
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
    """One subagent's misplaced skills, numbered by folder as its routes are."""
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

    Here rather than in the listing, so the refusal `warm` raises names the subagent too.
    """
    try:
        return tuple(sorted(one.name for one in repository.found))
    except (ToolError, SubagentError) as exc:
        msg = f"subagent {name!r}: {exc}"
        # The original traceback, carried over: a refusal is filed by its deepest frame,
        # and a plain re-raise would file every one under this function.
        raise type(exc)(msg).with_traceback(exc.__traceback__) from exc


def _one_registry(name: str, directories: tuple[Path, ...]) -> SkillRegistry:
    """One bundle's skills, read from each of its directories and held as one."""
    read = [skill_registry.read(LocalSkillRepository(one, flat=True)) for one in directories]
    if len(read) == 1:
        return read[0]
    # Refused, not merged: every directory is mounted under the bundle's one label, so
    # two holding a name would hide one skill behind the other.
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
