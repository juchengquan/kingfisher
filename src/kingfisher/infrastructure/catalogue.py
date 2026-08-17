"""Where a deployment's definitions are read from.

Split out of `workspace_fs`, which is "the filesystem, doing what
`domain.layout` describes" -- and a catalogue is the one thing here that need
not be in a workspace at all. `KINGFISHER_SKILLS_DIR` and its two siblings exist
so several deployments can share one reviewed set, so these three directories
are as likely to sit somewhere else entirely as inside a workspace. Keeping them
beside `ensure_layout` read as misfiled rather than as a deliberate exception.

What it holds is one repository per kind rather than three paths. A path is what
a *local* catalogue happens to be; what every caller actually wants is the
definitions, and two of the three kinds need no filesystem to supply them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from kingfisher.config import Config, ConfigError
from kingfisher.domain.ports import SkillRepository, SubagentRepository, ToolRepository
from kingfisher.domain.tool import Offering
from kingfisher.infrastructure.skill_store import LocalSkillRepository
from kingfisher.infrastructure.subagent_store import LocalSubagentRepository
from kingfisher.infrastructure.tool_store import LocalToolRepository

CATALOGUE_KINDS: tuple[str, ...] = ("skills", "subagents", "tools")


@dataclass(frozen=True)
class Catalogue:
    """This deployment's definitions: one repository per kind.

    A type rather than a mapping so the three names are checkable. `.skils` is
    an `unresolved-attribute` before the code runs; `["skils"]` is a `KeyError`
    while it does, and in this codebase a missing catalogue key surfaces as an
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
    a `Catalogue` would have it reach into `infrastructure`.
    """

    skills: SkillRepository
    subagents: SubagentRepository
    tools: ToolRepository

    def warm(self) -> Catalogue:
        """Read all three now, so a broken definition fails here.

        A repository is lazy, which is right for the fallback in `build_agent`
        -- a caller wanting skills should not pay for importing every tool. It
        is wrong for a deployment: `resolve_catalogue` already refuses a
        catalogue that is not a directory because "a catalogue that cannot be
        read is a wiring mistake and this is the last moment it is cheap to say
        so", and a subagent with an unknown field is the same mistake one layer
        in. Touching them here moves that from the first turn to startup.

        It is also what makes the reading happen once. The caching lives in the
        repositories now rather than here, so this asks each of them for its
        payload and they hold it -- and a deployment supplying a repository that
        does not cache gets a read per turn, which is its own choice to make.

        Called by `Kingfisher`, not by `resolve_catalogue`, and the difference
        is `--list`. That command exists to be run *because* something is
        wrong, and it catches a loader error and prints it over the rest of
        the inventory rather than dying on it. Warming inside resolution
        raised before it could -- a test caught that. A deployment wants the
        opposite and gets it by construction.

        Returns self, so construction reads as one expression.
        """
        _ = self.skills.names, self.subagents.specs, self.tools.found
        # A definition saying where its tools live is checked here for the same
        # reason the reading happens here: it is a claim about this catalogue,
        # both halves are now in hand, and a stale path found on the first turn
        # that activates one delegate is a deployment that started while broken.
        offers = Offering.of(self.tools.found)
        for spec in self.subagents.specs.values():
            offers.refuse_moved(spec.tool_sources, subject=f"subagent {spec.name!r}")
        return self

    @classmethod
    def from_config(cls, cfg: Config) -> Catalogue:
        """The deployment's own directories, without staging anything.

        The fallback for a caller that was handed no catalogue -- `build_agent`
        called directly, `--list`, a test.
        """
        return cls.from_roots(cfg.catalogue_roots)

    @classmethod
    def from_roots(cls, roots: Mapping[str, Path]) -> Catalogue:
        """Three directories on this host, as three local repositories.

        The shorthand nearly every deployment wants, and the reason
        `Kingfisher(catalogue=...)` takes a mapping as well as a `Catalogue`:
        pointing at three directories should not require naming three classes.
        """
        return cls(
            skills=LocalSkillRepository(Path(roots["skills"])),
            subagents=LocalSubagentRepository(Path(roots["subagents"])),
            tools=LocalToolRepository(Path(roots["tools"])),
        )


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


def local_root(repository: object, kind: str) -> Path:
    """The directory behind a repository, or a refusal explaining why one is needed.

    Two things genuinely cannot work without a host directory, and both are
    about the agent rather than about kingfisher reading a definition: the
    `/skills` route is a `FilesystemBackend` over a real path, and
    `$KINGFISHER_SKILLS` is that path handed to a skill's own scripts.

    So this is the edge of the abstraction, and it says so rather than mounting
    an empty directory and letting the agent be told about nothing -- the
    silent-emptiness failure the rest of this module keeps refusing. A repository
    backed by something else would need to reach the agent as a `BackendProtocol`
    instead, which `SkillRepository` cannot supply today: it answers with names,
    and a route needs file contents.
    """
    root = _root_of(repository)
    if root is None:
        msg = (
            f"the {kind} repository is not backed by a directory, and the agent reads "
            f"{kind} through a filesystem route that needs one. Supply a directory-backed "
            f"repository for {kind}, or stage its definitions to a directory first"
        )
        raise ConfigError(msg)
    return root


def resolve_catalogue(
    cfg: Config, supplied: Catalogue | Mapping[str, Path] | None = None
) -> Catalogue:
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
        return Catalogue.from_config(cfg)

    # Either shape. A deployment stages directories and hands over a mapping,
    # which is the documented seam; something that already holds a `Catalogue`
    # -- another kingfisher, a test fixture -- should not have to take it apart
    # to pass it back.
    if not isinstance(supplied, Catalogue):
        if missing := tuple(kind for kind in CATALOGUE_KINDS if kind not in supplied):
            msg = (
                f"catalogue is missing {', '.join(missing)}; it names all of "
                f"{', '.join(CATALOGUE_KINDS)}, since a deployment that leaves one out "
                "means an empty one rather than the configured one"
            )
            raise ConfigError(msg)
        supplied = Catalogue.from_roots(supplied)

    # Checked however it arrived, and only where there is something to check: a
    # repository backed by a service has no directory that could be missing, so
    # what it holds is its own business.
    roots = {kind: _root_of(getattr(supplied, kind)) for kind in CATALOGUE_KINDS}
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
