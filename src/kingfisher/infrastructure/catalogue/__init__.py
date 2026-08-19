"""Where a deployment's definitions are read from.

A package, and the three kinds are the point of it: `skills`, `subagents` and
`tools` are one module each, so the layer's top level names the concepts rather
than the mechanisms it used to spell them with -- `skill_store`,
`subagent_store`, `tool_store`. `documents` reads one definition document,
`layered` puts a session's own definitions over the deployment's, and
`importing` loads a module from a path for the two kinds that need one. Nothing
else in the codebase reaches past this front door: outside these files,
`documents` and `importing` have no callers at all, `layered` has one, and the
three repositories are reached for `ToolError`, `SKILL_LAYOUT` and one function.

Two things that belong to this subject are deliberately elsewhere.
`infrastructure.harness.skill_registry` answers which skills deepagents actually
loaded, which is a different question from what exists to mount -- running the
two together is the bug it was written to end -- and its answer carries
deepagents' own skill objects, so it lives where foreign types may be named.
Moving it here would also spread the swap boundary `harness/` exists to hold,
and cost more watched edges than it saved. `uploads` is request-scoped and
writes what `layered` then reads; splitting the pair costs less than filing a
per-request concern under a per-deployment one.

Split out of `workspace_fs`, which is "the filesystem, doing what
`domain.layout` describes" -- and a catalogue is the one thing here that need
not be in a workspace at all. `KINGFISHER_SKILLS_DIR` and its two siblings exist
so several deployments can share one reviewed set, so these three directories
are as likely to sit somewhere else entirely as inside a workspace. Keeping them
beside `ensure_layout` read as misfiled rather than as a deliberate exception.

What it holds is one repository per kind rather than three paths. A path is what
a *local* catalogue happens to be; what every caller actually wants is the
definitions, and two of the three kinds need no filesystem to supply them.

The module keeps the word and the type does not, and that is the split rather
than an oversight. A *catalogue* is where definitions are kept -- `catalogue_root`
and `Config.catalogue_roots` answer with places, and a deployment may point at
one it shares. `Definitions` is what you get when you read it. The type was
called `Catalogue` too, which made "the catalogue" mean the place, the contents,
and -- one line away in `config.py` -- `models.yaml` as well.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from functools import cached_property
from pathlib import Path

from kingfisher.config import Config, ConfigError
from kingfisher.domain.agent import DIRECTORY as AGENT_DIRECTORY
from kingfisher.domain.ports import (
    AgentRepository,
    SkillRepository,
    SubagentRepository,
    ToolRepository,
)
from kingfisher.domain.tool import Offering
from kingfisher.infrastructure.catalogue.agents import LocalAgentRepository
from kingfisher.infrastructure.catalogue.skills import LocalSkillRepository
from kingfisher.infrastructure.catalogue.subagents import LocalSubagentRepository
from kingfisher.infrastructure.catalogue.tools import LocalToolRepository
from kingfisher.infrastructure.harness import skill_registry
from kingfisher.infrastructure.harness.skill_registry import SkillRegistry


@dataclass(frozen=True)
class Definitions:
    """This deployment's definitions: one repository per kind.

    A type rather than a mapping so the three names are checkable. `.skils` is
    an `unresolved-attribute` before the code runs; `["skils"]` is a `KeyError`
    while it does, and in this codebase a missing key surfaces as an
    empty catalogue -- the silent emptiness this module's neighbours keep
    refusing.

    Repositories rather than paths, which is what changed. A deployment holding
    its definitions somewhere kingfisher did not choose supplies its own, and
    nothing downstream knows: `catalogue.subagents.specs` is the same call
    whether a directory or a service answered it. The three ports are in
    `domain.ports`, and `Local*` are the ones backed by this host.

    One object rather than three constructor arguments on `Kingfisher`. Swapping
    a single seam is `replace(catalogue, subagents=...)` -- it is frozen, so
    that is already free -- where three arguments would have spread one concept
    across the whole call graph, since `build_agent`, `uploads` and `delegation`
    each take exactly one of these today.

    `Config.catalogue_roots` still answers with a mapping of paths and is
    deliberately not this type. `Config` is a record a deployment fills in, and
    it sits above the layers precisely so it never imports one; making it return
    a `Definitions` would have it reach into `infrastructure`.
    """

    agents: AgentRepository
    skills: SkillRepository
    subagents: SubagentRepository
    tools: ToolRepository

    @cached_property
    def registry(self) -> SkillRegistry:
        """What the agent will actually be told about, asked of deepagents.

        Beside `skills` rather than replacing it, because they answer different
        questions and running them together is what let a skill be advertised
        and never loaded. The repository says what files exist to mount; this
        says which of them deepagents kept.

        Cached, and warmed with the rest: listing costs 8 ms at fifty skills and
        the answer cannot change while a deployment runs -- deepagents itself
        loads once per session and checkpoints the result, so a catalogue read
        again per turn would be answering a question nobody re-asks.
        """
        return skill_registry.read(self.skills, root=catalogue_root(self.skills))

    @cached_property
    def bundled_tools(self) -> Mapping[str, ToolRepository]:
        """Each subagent's own tools, by the name a grant would use.

        Deliberately not folded into `tools`. That repository is the shared
        catalogue and `Offering.of` is built from it, so anything added here
        would become a name any request could grant and any agent could hold --
        which is the one thing a bundle exists to prevent. An agent omitting
        `tools:` gets every tool there is; the way to have one it does not get
        is to keep that tool out of this offering entirely.

        Asked of the repository rather than required of the port, the same way
        `_root_of` asks for a root. Only a store backed by a filesystem has
        folders to find a bundle in; a catalogue served over the wire hands over
        subagents by name, and a name has no folder in it. That is not a gap --
        such a deployment has no bundles, correctly.

        One `LocalToolRepository` per bundle rather than a second loader. A
        bundle's tools are tools: the same `TOOLS` export, the same refusal for
        a module that will not import, the same relative sources -- and now
        relative to the bundle, so `probe.py::probe` reads the same whether it
        is written in the catalogue or in `surveyor/tools/`.
        """
        bundles = getattr(self.subagents, "bundles", None)
        if not bundles:
            return {}
        return {
            name: LocalToolRepository(bundle.tools)
            for name, bundle in bundles.items()
            if bundle.tools is not None
        }

    @cached_property
    def bundled_skills(self) -> Mapping[str, SkillRegistry]:
        """Each subagent's own skills, as deepagents will actually load them.

        A registry rather than a repository, which is the opposite choice from
        `bundled_tools` and made for the reason `skill_registry` exists at all:
        kingfisher does not parse skills, so "what is on disk" and "what the
        agent will be told about" are different questions, and running them
        together is what once advertised four skills while three loaded.

        Keyed like `bundled_tools`, so one subagent name reaches both halves of
        what it brings.

        Not merged into `registry`. That one is the shared catalogue, and a
        bundled skill appearing in it would be a skill any request could grant
        and any agent could be told about -- the same reason bundle tools stay
        out of `Offering`.
        """
        bundles = getattr(self.subagents, "bundles", None)
        if not bundles:
            return {}
        return {
            name: skill_registry.read(
                LocalSkillRepository(bundle.skills), root=bundle.skills
            )
            for name, bundle in bundles.items()
            if bundle.skills is not None
        }

    def warm(self) -> Definitions:
        """Read all three now, so a broken definition fails here.

        A repository is lazy, which is right for the fallback in `build_agent`
        -- a caller wanting skills should not pay for importing every tool. It
        is wrong for a deployment: `resolve_definitions` already refuses a
        catalogue that is not a directory because "a catalogue that cannot be
        read is a wiring mistake and this is the last moment it is cheap to say
        so", and a subagent with an unknown field is the same mistake one layer
        in. Touching them here moves that from the first turn to startup.

        It is also what makes the reading happen once. The caching lives in the
        repositories now rather than here, so this asks each of them for its
        payload and they hold it -- and a deployment supplying a repository that
        does not cache gets a read per turn, which is its own choice to make.

        Called by `Kingfisher`, not by `resolve_definitions`, and the difference
        is `--list`. That command exists to be run *because* something is
        wrong, and it catches a loader error and prints it over the rest of
        the inventory rather than dying on it. Warming inside resolution
        raised before it could -- a test caught that. A deployment wants the
        opposite and gets it by construction.

        Returns self, so construction reads as one expression.
        """
        _ = self.agents.specs, self.skills.names, self.subagents.specs, self.tools.found
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
        return self

    @classmethod
    def from_config(cls, cfg: Config) -> Definitions:
        """The deployment's own directories, without staging anything.

        The fallback for a caller that was handed no catalogue -- `build_agent`
        called directly, `--list`, a test.
        """
        return cls.from_roots(cfg.catalogue_roots)

    @classmethod
    def from_roots(cls, roots: Mapping[str, Path]) -> Definitions:
        """Four directories on this host, as four local repositories.

        The shorthand nearly every deployment wants, and the reason
        `Kingfisher(catalogue=...)` takes a mapping as well as a `Definitions`:
        pointing at four directories should not require naming four classes.

        `agents` is read with `.get`, unlike its three siblings. A mapping built
        before this kind existed is a deployment's own dict, not something
        kingfisher generates, and failing it with a `KeyError` would turn adding
        a kind into a breaking change for every caller that spelled the other
        three out. Absent, it lands in the workspace beside them.
        """
        return cls(
            agents=LocalAgentRepository(
                Path(roots.get("agents", Path(roots["skills"]).parent / AGENT_DIRECTORY))
            ),
            skills=LocalSkillRepository(Path(roots["skills"])),
            subagents=LocalSubagentRepository(Path(roots["subagents"])),
            tools=LocalToolRepository(Path(roots["tools"])),
        )


#: The kinds, taken from the type that already has one field per kind.
#:
#: It was written out again here, six lines above a `Definitions` whose fields
#: have exactly these names, with nothing holding the two together. The folder
#: made it worse: `agents.py`, `skills.py`, `subagents.py` and `tools.py` are the
#: same vocabulary again, and the one with no type behind it --
#: `test_the_catalogue_holds_one_module_per_kind` is what binds those.
#:
#: Field order is load-bearing now rather than by coincidence: `seeding` walks
#: this to decide what to copy and in what order, so reordering `Definitions` is
#: a change to seeding rather than a cosmetic edit.
DEFINITION_KINDS: tuple[str, ...] = tuple(f.name for f in fields(Definitions))

#: The kinds a *supplied* catalogue has to name and stage itself.
#:
#: `agents` is deliberately outside it, and not because it matters less. It
#: arrived after this seam was published, and a deployment that spelled out the
#: three kinds it knew about should not stop starting because a fourth exists.
#: Omitted, the directory lands beside the others and holds nothing -- which is
#: the same answer a derived catalogue gives before anyone seeds it.
#:
#: The silence this leaves is covered elsewhere and better: a request names the
#: agent it wants, so an empty `agents/` is reported as "no agent by that name,
#: this workspace has none" rather than as a missing directory. That is the
#: message somebody can act on.
STAGED_KINDS: tuple[str, ...] = tuple(k for k in DEFINITION_KINDS if k != AGENT_DIRECTORY)


def _root_of(repository: object) -> Path | None:
    """The directory behind a repository, when there is one.

    Asked rather than required, because `AssetRepository` deliberately does not
    carry it: only a store backed by a filesystem has a root, and demanding one
    would make the port unimplementable by the stores it exists to allow. What
    this buys is that the staging check below still applies to the local case,
    which is every deployment that hands over directories.
    """
    root = getattr(repository, "root", None)
    return Path(root) if isinstance(root, (str, Path)) else None


def source_of(repository: object) -> str:
    """Where a repository's definitions live, for a message a person reads.

    Only ever interpolated into text -- "rename them in ..." -- so a store with
    no directory is not a failure here, just something to name differently.
    """
    root = _root_of(repository)
    return str(root) if root is not None else "the catalogue"


def catalogue_root(repository: object) -> Path | None:
    """The directory behind a repository, or `None` when there is not one.

    This used to refuse rather than answer `None`, because the `/skills` route
    was a `FilesystemBackend` over a real path and a repository with no path was
    unmountable. `SkillRepository.files` ended that: `skills_backend` mounts
    whatever a repository can hand over, so a missing directory is now a fact
    about *which backend to build*, not a wiring error.

    Two things still follow the directory rather than the repository, and both
    are the shell rather than the agent's file tools: `$KINGFISHER_SKILLS`, which
    a skill's own scripts address, and the sandbox profile's readable root. A
    store has no path for either, so a catalogue held outside the filesystem
    gets skills the agent can *read* and scripts it cannot *run*. That is a real
    limit and it is stated where it bites, in `shell_env`.
    """
    return _root_of(repository)


def resolve_definitions(
    cfg: Config, supplied: Definitions | Mapping[str, Path] | None = None
) -> Definitions:
    """Where this deployment's definitions are read from, settled once.

    Called at construction and nowhere else, so a deployment that stages its
    catalogue from somewhere else pays for that once per `Kingfisher` rather
    than once per turn.

    The two cases differ in who owns the directories, and therefore in what a
    missing one means:

    * **Derived from `cfg`** -- kingfisher's own, so they are created. This is
      what `ensure_layout` already does for a workspace that has not relocated
      them, and doing it here extends that to one that has. `KINGFISHER_SKILLS_DIR`
      pointing somewhere that does not exist yet used to yield an empty
      catalogue and a clean start; only `skills_dir` was ever created, by
      `build_backend`, and its two siblings were not.
    * **Supplied by the caller** -- theirs, so they must already be there.
      Creating one would hide a staging failure behind a catalogue that is
      merely empty, and an agent told about no skills at all is exactly the
      silent-emptiness this module's neighbours keep refusing.

    Raises `ConfigError` either way, because a catalogue that cannot be read is
    a wiring mistake and this is the last moment it is cheap to say so.
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
    # repository backed by a service has no directory that could be missing, so
    # what it holds is its own business.
    roots = {kind: _root_of(getattr(supplied, kind)) for kind in STAGED_KINDS}
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
