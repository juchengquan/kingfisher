"""Where this deployment reads from, as one record."""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from kingfisher.core.definitions.catalogue import DEFINITION_KINDS

if TYPE_CHECKING:
    from kingfisher.core.definitions.catalogue import Definitions

    from .config import Config

__all__ = ["Kind", "Origin", "Origins"]

#: What an entry turned out to be.
Kind = Literal["default", "relocated", "overridden", "supplied", "unset"]


@dataclass(frozen=True)
class Origin:
    """One place, and what kind of place it turned out to be."""

    kind: Kind
    path: Path | None = None

    def spelled(self, workspace: Path, definitions: Path) -> str:
        """This entry as it appears on the startup line."""
        if self.kind == "supplied":
            return "<supplied>"
        if self.path is None:
            return "unset"
        where = _under(self.path, workspace, definitions)
        if self.kind == "unset":
            return f"unset({where})"
        if self.kind == "overridden":
            return f"{where}(overridden)"
        return where


@dataclass(frozen=True)
class Origins:
    """Every place this deployment reads from, settled once."""

    #: What sessions make, and what `./` means on the line. A bare `Path`, as `definitions`
    #: is: each is a setting with no default, so never derived, supplied or unset.
    workspace: Path
    #: What people write, and what `definitions/` means below.
    definitions: Path

    agents: Origin
    middlewares: Origin
    skills: Origin
    subagents: Origin
    tools: Origin

    #: The operator-authored files. `models` is required, so never `unset`; `source_ids`
    #: is optional, and its `unset` is the most useful thing in this record.
    models: Origin
    source_ids: Origin

    #: Where `kingfisher seed` copies *from*. Not `assets`: beside `skills` and `tools`,
    #: that reads like one more place definitions are read from.
    seed: Origin

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
            definitions=cfg.definitions,
            **{
                kind: _catalogue(cfg, kind, catalogue) for kind in DEFINITION_KINDS
            },  # type: ignore[arg-type]
            models=_file(cfg.models.source, cfg.definitions / "models.yaml"),
            source_ids=_source_ids(cfg),
            seed=_configured(cfg.assets),
            sessions=_sessions(cfg, sessions),
        )

    def line(self) -> str:
        """Every entry, on one line, with the two roots factored out.

        Relative, so the entries that are somewhere else are the only absolute paths,
        and stand out.
        """
        pairs = " ".join(
            f"{name}={origin.spelled(self.workspace, self.definitions)}"
            for name, origin in self.entries()
        )
        return f"workspace={self.workspace} definitions={self.definitions} {pairs}"

    def block(self) -> tuple[str, ...]:
        """The same answer as a header, one place per line."""
        names = ("workspace", "definitions", *(name for name, _ in self.entries()))
        width = max(len(name) for name in names)
        return (
            f"{'workspace'.ljust(width)} : {self.workspace}",
            f"{'definitions'.ljust(width)} : {self.definitions}",
            *(
                f"{name.ljust(width)} : {origin.spelled(self.workspace, self.definitions)}"
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


def _under(path: Path, workspace: Path, definitions: Path) -> str:
    """A path inside the workspace as `./name`, one inside the definitions folder as
    `definitions/name`, anything else in full."""
    if path == workspace:
        return "."
    if path.is_relative_to(workspace):
        return f"./{path.relative_to(workspace)}"
    if path == definitions:
        return "definitions"
    if path.is_relative_to(definitions):
        return f"definitions/{path.relative_to(definitions)}"
    return str(path)


def _derived(actual: Path, default: Path) -> Kind:
    """`default` when this is where kingfisher would have put it anyway."""
    return "default" if actual == default else "relocated"


def _file(actual: Path | None, default: Path) -> Origin:
    """A path with a derived fallback: `models.yaml`."""
    if actual is None:
        # A `Models` assembled in code carries no source: no file was read.
        return Origin("supplied")
    return Origin(_derived(actual, default), actual)


def _configured(actual: Path | None) -> Origin:
    """A path with no derived fallback: seeding's source, and the session store."""
    return Origin("relocated", actual) if actual is not None else Origin("unset")


def _catalogue(cfg: Config, kind: str, catalogue: Definitions | None) -> Origin:
    """One definition catalogue, comparing what is read against what is configured."""
    configured = cfg.catalogue_roots[kind]
    if catalogue is None:
        return Origin("default", configured)

    root = getattr(catalogue, kind).root
    if root != configured:
        return Origin("overridden", root)
    return Origin("default", root)


def _source_ids(cfg: Config) -> Origin:
    """The policy file, whose absence is the case worth reporting."""
    if cfg.access_source is None:
        return Origin("supplied") if cfg.access is not None else Origin("unset")
    if cfg.access is None:
        return Origin("unset", cfg.access_source)
    return Origin(
        _derived(cfg.access_source, cfg.definitions / "source_ids.yaml"), cfg.access_source
    )


def kingfishers_own(sessions: object, cfg: Config) -> bool:
    """Whether `sessions` keep them where kingfisher's own would, in the workspace."""
    where = getattr(sessions, "where", None)
    return callable(where) and where(cfg) == cfg.sessions_root


def _sessions(cfg: Config, sessions: object | None) -> Origin:
    """Where sessions are: the workspace's own directory, or a deployment's own backends.

    Asked of the sessions in use, never of the setting that built them, which a
    deployment's own backends may ignore. `None` is answered with the workspace's own.
    """
    if sessions is not None and not kingfishers_own(sessions, cfg):
        return Origin("supplied")
    return Origin("default", cfg.sessions_root)
