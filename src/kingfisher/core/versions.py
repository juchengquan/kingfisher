"""The definitions a turn runs on, read and checked as one set."""

from __future__ import annotations

from contextlib import suppress
from pathlib import PurePosixPath
from threading import Lock
from typing import TYPE_CHECKING

from .access import ALL
from .agent.assembly import builtin_tool_names
from .config import Config, ConfigError
from .definitions.catalogue import Definitions, resolve_definitions
from .definitions.tools.spec import Offering, select
from .logged import handed, warning

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .definitions.definition import SpecBase
    from .interfaces import RunEvents


class Versions:
    """The checked definitions turns start on, read again whenever the folder changes.

    The first read refuses as kingfisher is built: a catalogue that cannot be read then is
    a wiring mistake, to fail at startup rather than serve an agent told about nothing.
    """

    def __init__(self, cfg: Config, sink: RunEvents) -> None:
        self._cfg = cfg
        # The deployment's own, not a turn's: a refusal found outside any turn is said too.
        self._sink = sink
        # One turn reads a changed folder while the others wait for what it read.
        self._lock = Lock()
        fresh = resolve_definitions(cfg)
        self._seen = _signature(fresh)
        self._current = _checked(cfg, fresh)
        #: What the folder held when it was last refused, so a refusal is said once.
        self._refused: object = None

    def current(self) -> Definitions:
        """The definitions a turn starting now runs on: the set that loaded last, or the
        folder read again if it changed since and passes its checks.

        Asked once per turn, which keeps the answer to its end, subagents included.
        """
        with self._lock:
            try:
                fresh = resolve_definitions(self._cfg)
                seen = _signature(fresh)
                if seen == self._seen:
                    return self._current
                found = dict(fresh.digests)
            except ConfigError as exc:
                self._refuse(str(exc), exc)
                return self._current
            except OSError:
                # A file moved while it was read: the next turn looks again.
                return self._current
            self._seen = seen
            if found in (self._current.digests, self._refused):
                return self._current
            try:
                self._current = _checked(self._cfg, fresh)
            # Whatever a changed folder raises, turns keep a set that loaded.
            except Exception as exc:  # noqa: BLE001
                self._refuse(found, exc)
            return self._current

    def _refuse(self, held: object, exc: Exception) -> None:
        """Say once that the folder, as it now holds `held`, was not taken."""
        if held == self._refused:
            return
        self._refused = held
        # With its message: the operator wrote the definitions, and is the one reading.
        handed(
            self._sink,
            warning(
                "definitions_refused",
                "the definitions changed and the change was refused, so turns run on the "
                f"set that loaded last: {type(exc).__name__}: {exc}",
                error_type=type(exc).__name__,
            ),
        )


def _signature(definitions: Definitions) -> Mapping[str, tuple[int, int]]:
    """Each file's size and time of change: what a turn compares before reading any.

    An edit that keeps both is noticed with the next one.
    """
    stats = {key: path.stat() for key, path in definitions.files.items()}
    return {key: (stat.st_size, stat.st_mtime_ns) for key, stat in stats.items()}


def _checked(cfg: Config, definitions: Definitions) -> Definitions:
    """`definitions` read and checked, or the refusal."""
    # Before the read, so an edit landing during it leaves the record behind the files,
    # and the next turn reads them again. A pause records what was loaded.
    _ = definitions.digests
    definitions.warm()
    # Whether an offered tool wears a built-in's name: only an assembled graph can say,
    # so `warm` cannot. A subagent's own tools as well as the catalogue's: a bundled
    # `read_file` replaced deepagents' own in that subagent and crashed every model call
    # it made, and nothing said so -- `doctor` called the definitions healthy.
    if definitions.tools.found or any(
        repository.found for repository in definitions.bundled_tools.values()
    ):
        # Only `ConfigError`: the deployment could not be assembled, which the turn and
        # `doctor` report, and raised here it would stop the `sessions` and `reap`
        # commands, which need no model. `CapabilityError` still refuses.
        with suppress(ConfigError):
            builtin_tool_names(cfg, definitions)
    return definitions


def built_from(definitions: Definitions, agent: SpecBase) -> dict[str, str]:
    """The digest of each file a build of `agent` reads: its own, those behind the
    tools, skills, middleware and subagents it names, theirs in turn, and every file in a
    folder one of them owns."""
    places = _named_by(definitions, agent, seen=set())
    if (own := definitions.agents.sources.get(agent.name)) is not None:
        places.add(f"agents/{own}")
    folders = tuple(place for place in places if place.endswith("/"))
    return {
        path: digest
        for path, digest in definitions.digests.items()
        if path in places or path.startswith(folders)
    }


def _named_by(definitions: Definitions, spec: SpecBase, *, seen: set[str]) -> set[str]:
    """Where each thing `spec` names is kept: a file, or a folder ending in `/`."""
    found = definitions.tools.found
    places = {
        f"tools/{one.source}" for one in select(Offering.of(found).spelt(spec.tools), found)
    }
    registry = definitions.registry
    skills = (
        tuple(registry.offered)
        if spec.skills == ALL
        else tuple(registry.identity(str(written)) for written in spec.skills or ())
    )
    places |= {
        f"skills{PurePosixPath(registry.offered[key].path).parent}/"
        for key in skills
        if key is not None and key in registry.offered
    }
    # A name no file defines is a middleware the deployment registered, which is its code.
    files = {one.name: one.source for one in definitions.middlewares.found}
    named = tuple(files) if spec.middlewares == ALL else spec.middlewares or ()
    places |= {f"middlewares/{files[name]}" for name in named if name in files}
    subagents = definitions.subagents
    for key in tuple(subagents.specs) if spec.subagents == ALL else spec.subagents or ():
        if key in seen or key not in subagents.specs:
            continue
        seen.add(key)
        helper = subagents.specs[key]
        source = subagents.sources[key]
        where = PurePosixPath(source)
        # A folder named after the subagent is its own, as its bundle is, and holds any
        # package defining it there, so it is asked first.
        if where.parent.name == helper.name:
            places.add(f"subagents/{where.parent}/")
        # A package is its own folder too, whatever it is called: from the source as
        # written, since a path drops the `/` that marks one.
        elif source.endswith("/"):
            places.add(f"subagents/{source}")
        else:
            places.add(f"subagents/{where}")
        places |= _named_by(definitions, helper, seen=seen)
    return places
