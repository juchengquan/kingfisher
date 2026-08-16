"""Where a deployment's definitions are read from.

Split out of `workspace_fs`, which is "the filesystem, doing what
`domain.layout` describes" -- and a catalogue is the one thing here that need
not be in a workspace at all. `KINGFISHER_SKILLS_DIR` and its two siblings exist
so several deployments can share one reviewed set, so these three directories
are as likely to sit somewhere else entirely as inside a workspace. Keeping them
beside `ensure_layout` read as misfiled rather than as a deliberate exception.

It also needs less: `Config` and a `Path`, where its former neighbours all need
`domain.layout`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from kingfisher.config import Config, ConfigError

CATALOGUE_KINDS: tuple[str, ...] = ("skills", "subagents", "tools")


@dataclass(frozen=True)
class Catalogue:
    """Where this deployment's definitions are read from: three directories.

    A type rather than a mapping so the three names are checkable. `.skils` is
    an `unresolved-attribute` before the code runs; `["skils"]` is a `KeyError`
    while it does, and in this codebase a missing catalogue key surfaces as an
    empty catalogue -- the silent emptiness this module's neighbours keep
    refusing.

    It is not fewer lookups. `catalogue or Catalogue.from_config(cfg)` appears
    once in each of the four entry points that accept an optional one, exactly
    as the mapping did, and `build_agent` still resolves once and passes the
    result down. The count was never the problem; the anonymity was.

    Thin on purpose. It holds the directories and does not read them --
    `skill_store`, `subagent_store` and `tool_store` still take a `Path`, and
    still do the reading. Making it read as well would leave `load_all` public
    regardless, because a request's *uploaded* subagents come from the session
    rather than from here, and two ways to load a subagent that differ only in
    where they look is worse than one function called twice.

    `Config.catalogue_roots` still answers with a mapping and is deliberately
    not this type. `Config` is a record a deployment fills in, and it sits above
    the layers precisely so it never imports one; making it return a
    `Catalogue` would have it reach into `infrastructure`.
    """

    skills: Path
    subagents: Path
    tools: Path

    @classmethod
    def from_config(cls, cfg: Config) -> Catalogue:
        """The deployment's own directories, without staging anything.

        The fallback for a caller that was handed no catalogue -- `build_agent`
        called directly, `--list`, a test. One construction where there used to
        be a dict lookup per kind per call site.
        """
        roots = cfg.catalogue_roots
        return cls(skills=roots["skills"], subagents=roots["subagents"], tools=roots["tools"])


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
    # to pass it back. Both are validated the same way below.
    roots = (
        {"skills": supplied.skills, "subagents": supplied.subagents, "tools": supplied.tools}
        if isinstance(supplied, Catalogue)
        else supplied
    )

    if missing := tuple(kind for kind in CATALOGUE_KINDS if kind not in roots):
        msg = (
            f"catalogue_roots is missing {', '.join(missing)}; it names all of "
            f"{', '.join(CATALOGUE_KINDS)}, since a deployment that leaves one out "
            "means an empty one rather than the configured one"
        )
        raise ConfigError(msg)

    roots = {kind: Path(roots[kind]) for kind in CATALOGUE_KINDS}
    if absent := tuple(f"{kind} ({path})" for kind, path in roots.items() if not path.is_dir()):
        msg = (
            f"catalogue_roots names {', '.join(absent)}, which is not a directory; "
            "a supplied catalogue is staged by whoever supplies it, and kingfisher "
            "will not create one in case the staging is what failed"
        )
        raise ConfigError(msg)
    return Catalogue(**roots)
