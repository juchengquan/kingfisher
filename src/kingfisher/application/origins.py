"""Where this deployment reads from, as one record.

The four definition catalogues, `models.yaml`, `groups.yaml`, the directory seeding
copies from, and the three working roots -- each with what kind of place it turned
out to be, under the workspace they resolve against. Nothing could say what they
were. `kingfisher list` printed four, `doctor` printed one, the library printed none,
and each assembled its own answer -- so the catalogue a listing named and the one a
diagnosis counted were two reads that nobody held together.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from kingfisher.infrastructure.catalogue import DEFINITION_KINDS, catalogue_root

if TYPE_CHECKING:
    from kingfisher.config import Config
    from kingfisher.infrastructure.catalogue import Definitions

__all__ = ["Kind", "Origin", "Origins"]

#: What an entry turned out to be.
Kind = Literal["default", "relocated", "overridden", "supplied", "unset"]



@dataclass(frozen=True)
class Origin:
    """One place, and what kind of place it turned out to be."""

    kind: Kind
    path: Path | None = None

    def spelled(self, workspace: Path) -> str:
        """This entry as it appears on the startup line."""
        if self.kind == "supplied":
            return "<supplied>"
        if self.path is None:
            return "unset"
        where = _under(self.path, workspace)
        if self.kind == "unset":
            return f"unset({where})"
        if self.kind == "overridden":
            return f"{where}(overridden)"
        return where


@dataclass(frozen=True)
class Origins:
    """Every place this deployment reads from, settled once."""

    #: Everything else is relative to this, which is why it is a bare `Path`
    #: rather than an `Origin`: a workspace is never derived, never supplied and
    #: never unset -- `KINGFISHER_WORKSPACE` is the one setting with no default.
    workspace: Path

    agents: Origin
    skills: Origin
    subagents: Origin
    tools: Origin

    #: The two operator-authored files. `models` is required, so it is never
    #: `unset`; `groups` is optional, and its `unset` is the most useful thing
    #: in this record.
    models: Origin
    groups: Origin

    #: Where `kingfisher seed` copies *from*, which is the opposite direction to
    #: the four catalogues above. Named `seed` rather than `assets` for that
    #: reason: beside `skills` and `tools`, a fifth noun reads like a fifth
    #: place definitions are read from.
    seed: Origin

    state: Origin
    scratch: Origin
    sessions: Origin

    @classmethod
    def of(
        cls,
        cfg: Config,
        *,
        catalogue: Definitions | None = None,
        sessions: object | None = None,
    ) -> Origins:
        """Read the configuration, and the collaborators that can override it."""
        return cls(
            workspace=cfg.workspace,
            **{
                kind: _catalogue(cfg, kind, catalogue) for kind in DEFINITION_KINDS
            },  # type: ignore[arg-type]
            models=_file(cfg.models.source, cfg.workspace / "models.yaml"),
            groups=_groups(cfg),
            seed=_configured(cfg.assets),
            state=_file(cfg.state_dir, cfg.workspace / ".kingfisher"),
            scratch=_file(cfg.scratch_dir, cfg.state_dir / "tmp"),
            sessions=_sessions(cfg, sessions),
        )

    def line(self) -> str:
        """Every entry, on one line, with the workspace factored out.

        Spelled out in full this is about 450 characters, nine of eleven values
        sharing one prefix -- and the entries worth noticing, the ones that are
        somewhere else, are buried in the repetition. Relative brings it to about 240
        *and* leaves the relocated ones as the only absolute paths, so the eye finds
        them without reading.
        """
        pairs = " ".join(
            f"{name}={origin.spelled(self.workspace)}" for name, origin in self.entries()
        )
        return f"workspace={self.workspace} {pairs}"
    def block(self) -> tuple[str, ...]:
        """The same answer as a header, one place per line."""
        names = ("workspace", *(name for name, _ in self.entries()))
        width = max(len(name) for name in names)
        return (
            f"{'workspace'.ljust(width)} : {self.workspace}",
            *(
                f"{name.ljust(width)} : {origin.spelled(self.workspace)}"
                for name, origin in self.entries()
            ),
        )

    def entries(self) -> tuple[tuple[str, Origin], ...]:
        """Each name and its origin, in declaration order."""
        return tuple(
            (f.name, value)
            for f in fields(self)
            if isinstance(value := getattr(self, f.name), Origin)
        )


def _under(path: Path, workspace: Path) -> str:
    """A path inside the workspace as `./name`, anything else in full."""
    if path == workspace:
        return "."
    if path.is_relative_to(workspace):
        return f"./{path.relative_to(workspace)}"
    return str(path)


def _derived(actual: Path, default: Path) -> Kind:
    """`default` when this is where kingfisher would have put it anyway."""
    return "default" if actual == default else "relocated"


def _file(actual: Path | None, default: Path) -> Origin:
    """A path with a derived fallback: the two catalogues' files, and the roots."""
    if actual is None:
        # Only reachable for a `Models` assembled in code, which carries no
        # source. The file was never read, so there is no path to report.
        return Origin("supplied")
    return Origin(_derived(actual, default), actual)


def _configured(actual: Path | None) -> Origin:
    """A path with no derived fallback: seeding's source, and the session store."""
    return Origin("relocated", actual) if actual is not None else Origin("unset")


def _catalogue(cfg: Config, kind: str, catalogue: Definitions | None) -> Origin:
    """One definition catalogue, comparing what is read against what is configured."""
    configured = cfg.catalogue_roots[kind]
    if catalogue is None:
        return Origin(_derived(configured, cfg.workspace / kind), configured)

    root = catalogue_root(getattr(catalogue, kind))
    if root is None:
        return Origin("supplied")
    if root != configured:
        return Origin("overridden", root)
    return Origin(_derived(root, cfg.workspace / kind), root)


def _groups(cfg: Config) -> Origin:
    """The policy file, whose absence is the case worth reporting."""
    if cfg.access_source is None:
        return Origin("supplied") if cfg.access is not None else Origin("unset")
    if cfg.access is None:
        return Origin("unset", cfg.access_source)
    return Origin(_derived(cfg.access_source, cfg.workspace / "groups.yaml"), cfg.access_source)


def _sessions(cfg: Config, store: object | None) -> Origin:
    """Where a session's files are kept when the machine may not keep them."""
    if store is None:
        return _configured(cfg.session_store)
    root = getattr(store, "root", None)
    if not isinstance(root, (str, Path)) or cfg.session_store is None:
        return Origin("supplied")
    if Path(root) != cfg.session_store:
        return Origin("overridden", Path(root))
    return _configured(cfg.session_store)
