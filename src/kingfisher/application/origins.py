"""Where this deployment reads from, as one record."""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from kingfisher.infrastructure.catalogue import DEFINITION_KINDS

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

    #: What sessions make, and what `./` means below. A bare `Path` rather than an
    #: `Origin`, as is the one after it: neither is ever derived, supplied or unset,
    #: since each is a setting with no default.
    workspace: Path
    #: What people write, and what `definitions/` means below.
    definitions: Path

    agents: Origin
    middlewares: Origin
    skills: Origin
    subagents: Origin
    tools: Origin

    #: The two operator-authored files. `models` is required, so it is never
    #: `unset`; `source_ids` is optional, and its `unset` is the most useful thing
    #: in this record.
    models: Origin
    source_ids: Origin

    #: Where `kingfisher seed` copies *from*, which is the opposite direction to
    #: the catalogues above. Named `seed` rather than `assets` for that reason:
    #: beside `skills` and `tools`, that noun reads like one more place
    #: definitions are read from.
    #:
    #: It said "the four catalogues" until there were five, which is the count
    #: this file now declines to keep -- the fields are the count.
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

        Spelled out in full this is about 450 characters, most of its values
        sharing one prefix or the other -- and the entries worth noticing, the ones
        that are somewhere else, are buried in the repetition. Relative brings it to
        about 240 *and* leaves the relocated ones as the only absolute paths, so the
        eye finds them without reading.
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
        return Origin("default", configured)

    root = getattr(catalogue, kind).root
    if root is None:
        return Origin("supplied")
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


def _sessions(cfg: Config, backends: object | None) -> Origin:
    """Where sessions are: the workspace's own directory, or a deployment's own backends.

    Two ways of asking, because the callers have different things to ask with.
    `Kingfisher` hands over the object it was built with, tested by identity against
    `DefaultBackends` -- a subclass may return its backend with anything changed. A
    surface holding only a `Config` has no object, so it reads the setting that would
    have built one.

    `inventory` had nothing to pass and got the derived default, so `kingfisher doctor`
    printed `sessions : ./sessions` three rows above its own `sessions survive` check,
    which reads this same setting and knew better. Both now answer `<supplied>`, which
    is what the library answers for the same deployment.
    """
    if backends is not None or cfg.session_backends_factory is not None:
        return Origin("supplied")
    return Origin("default", cfg.workspace / "sessions")
