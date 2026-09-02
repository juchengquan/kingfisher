"""Where this deployment reads from, as one record.

Eleven places: the four definition catalogues, `models.yaml`, `groups.yaml`,
the directory seeding copies from, and the three working roots. Nothing could
say what they were. `kingfisher list` printed four, `doctor` printed one, the
library printed none, and each assembled its own answer -- so the catalogue a
listing named and the one a diagnosis counted were two reads that nobody held
together.

Beside `inventory.py` and deliberately not inside it. The two answer different
questions of the same workspace -- what does it *offer*, and where did that come
from -- and one of them is cheap while the other builds an agent to answer.

Nothing here prints. `Origin` carries a kind rather than a formatted string, for
the reason the kinds exist at all: `--json` and the service read this, and
"nothing is configured" and "you handed me a store" must not arrive as two
spellings a consumer has to match on.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from kingfisher.infrastructure.catalogue import catalogue_root

if TYPE_CHECKING:
    from kingfisher.config import Config
    from kingfisher.infrastructure.catalogue import Definitions

__all__ = ["CATALOGUES", "Kind", "Origin", "Origins"]

#: What an entry turned out to be.
#:
#: `default` is the derived location -- what kingfisher would use having been
#: told nothing. `relocated` is any other configured path, including one a
#: deployment named that happens to be somewhere else entirely; the two are
#: separated because "empty" means opposite things across that line, and a
#: consumer should not have to test whether a string starts with a dot.
#:
#: `overridden` is the one worth being loud about: the configuration says one
#: place and something else is being read. A deployment in that state has a
#: setting that does nothing, and somebody will edit it and watch nothing change.
#:
#: `supplied` is a repository with no directory behind it at all. `unset` is an
#: optional thing nobody configured, and carries where it was looked for when
#: there is such a place.
Kind = Literal["default", "relocated", "overridden", "supplied", "unset"]

#: The four kinds of definition, in the order a listing wants them: an agent is
#: what a request names, and the other three are what it selects from. Shared
#: with `Definitions` and `Config.catalogue_roots` by name rather than by
#: position, which is why this is a tuple of strings and not a shape.
CATALOGUES = ("agents", "skills", "subagents", "tools")


@dataclass(frozen=True)
class Origin:
    """One place, and what kind of place it turned out to be.

    `path` is what was read, except for `unset`, where it is where kingfisher
    *looked* -- which is the whole value of the field there. A `groups.yaml`
    written one directory off is invisible in every other way: the deployment
    comes up, controls nothing by group, and says nothing about it.

    `None` for `supplied`, because there is no path, and for an `unset` thing
    with no default location to have looked in -- the seed directory and the
    session store are both configured or absent, never derived.
    """

    kind: Kind
    path: Path | None = None


@dataclass(frozen=True)
class Origins:
    """Every place this deployment reads from, settled once.

    Two levels of certainty, and which one you get depends on what you hand in.
    Given a `Config` alone this reports what was *configured*, which is all a
    caller outside a running kingfisher can know. Given the resolved catalogue
    as well -- which `Kingfisher` has, warmed, before its constructor returns --
    it reports what is actually being *read*, and the two differ exactly when a
    deployment staged its definitions somewhere itself.

    That difference is the reason this record exists rather than a handful of
    fields on `Config`. `Config.catalogue_roots` is the fallback, not the
    answer: a `Kingfisher` may be handed a mapping or a `Definitions` of its
    own, and a report derived from configuration alone would be right for the
    simple deployment and quietly wrong for the one that moved something.
    """

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
        """Read the configuration, and the collaborators that can override it.

        Deliberately does not call `resolve_definitions`: that creates derived
        roots, and a function whose whole job is to report must not change what
        it is reporting on. Handed nothing, it answers from `cfg` alone.

        `sessions` is the resolved store, which `Kingfisher` builds from
        `cfg.session_store` when nothing was injected. A store whose root is
        what the configuration names is that one; anything else was handed in.
        """
        return cls(
            workspace=cfg.workspace,
            **{
                kind: _catalogue(cfg, kind, catalogue) for kind in CATALOGUES
            },  # type: ignore[arg-type]
            models=_file(cfg.models.source, cfg.workspace / "models.yaml"),
            groups=_groups(cfg),
            seed=_configured(cfg.assets),
            state=_file(cfg.state_dir, cfg.workspace / ".kingfisher"),
            scratch=_file(cfg.scratch_dir, cfg.state_dir / "tmp"),
            sessions=_sessions(cfg, sessions),
        )

    def entries(self) -> tuple[tuple[str, Origin], ...]:
        """Each name and its origin, in declaration order.

        Derived from the dataclass rather than listed beside it, so a field
        added here cannot be one a printer silently omits -- which is how
        `tools` came to be the one catalogue `kingfisher list` never named.
        """
        return tuple(
            (f.name, value)
            for f in fields(self)
            if isinstance(value := getattr(self, f.name), Origin)
        )


def _derived(actual: Path, default: Path) -> Kind:
    """`default` when this is where kingfisher would have put it anyway.

    Compared against the derived location rather than asking whether an
    override was set, and the difference shows on a deployment that names a
    path equal to the default: this calls it `default`, which is what it is.
    The alternative would fire `doctor`'s relocated-and-empty warning on every
    fresh workspace whose operator was explicit.
    """
    return "default" if actual == default else "relocated"


def _file(actual: Path | None, default: Path) -> Origin:
    """A path with a derived fallback: the two catalogues' files, and the roots."""
    if actual is None:
        # Only reachable for a `Models` assembled in code, which carries no
        # source. The file was never read, so there is no path to report.
        return Origin("supplied")
    return Origin(_derived(actual, default), actual)


def _configured(actual: Path | None) -> Origin:
    """A path with no derived fallback: seeding's source, and the session store.

    Never `default`, because there is nowhere kingfisher would look on its own.
    Absent is a legitimate state for both -- a workspace seeded months ago needs
    no source, and a session directory is a perfectly good only copy.
    """
    return Origin("relocated", actual) if actual is not None else Origin("unset")


def _catalogue(cfg: Config, kind: str, catalogue: Definitions | None) -> Origin:
    """One definition catalogue, comparing what is read against what is configured.

    The comparison *is* the report. Nobody needs to know how an override
    happened, and threading a flag out of `resolve_definitions` to say so would
    add a parameter that exists for printing. What a reader needs is that the
    configuration is not what is being read, so they stop editing a variable
    that does nothing.

    A supplied mapping that matches the configuration reads as configured. The
    two are indistinguishable and equivalent, so there is nothing to report.
    """
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
    """The policy file, whose absence is the case worth reporting.

    Three states rather than two. A file that was read; no file, and here is
    where it was looked for; and a `Groups` assembled in code, which has no file
    behind it at all. The middle one is why `Config` carries the path rather
    than `Groups` carrying its own -- with no policy there is no record to ask.
    """
    if cfg.access_source is None:
        return Origin("supplied") if cfg.access is not None else Origin("unset")
    if cfg.access is None:
        return Origin("unset", cfg.access_source)
    return Origin(_derived(cfg.access_source, cfg.workspace / "groups.yaml"), cfg.access_source)


def _sessions(cfg: Config, store: object | None) -> Origin:
    """Where a session's files are kept when the machine may not keep them.

    A store is asked for its root the way a repository is, rather than being
    required to have one: `SessionStore` is a port, and a deployment keeping
    sessions somewhere that is not a directory satisfies it without a path.
    """
    if store is None:
        return _configured(cfg.session_store)
    root = getattr(store, "root", None)
    if not isinstance(root, (str, Path)) or cfg.session_store is None:
        return Origin("supplied")
    if Path(root) != cfg.session_store:
        return Origin("overridden", Path(root))
    return _configured(cfg.session_store)
