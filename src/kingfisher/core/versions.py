"""The definitions a turn runs on, read and checked as one set."""

from __future__ import annotations

from contextlib import suppress
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from .access import ALL
from .agent.assembly import builtin_tool_names
from .config import Config, ConfigError
from .definitions.catalogue import Definitions, resolve_definitions
from .definitions.tools.spec import Offering, select

if TYPE_CHECKING:
    from .definitions.definition import SpecBase


class Versions:
    """The checked definitions turns start on.

    Read and checked once, when kingfisher is built: a catalogue that cannot be read is a
    wiring mistake, to fail at startup rather than serve an agent told about nothing.
    """

    def __init__(self, cfg: Config) -> None:
        self._current = checked(cfg)

    def current(self) -> Definitions:
        """The definitions a turn starting now runs on.

        Asked once per turn, which keeps the answer to its end, subagents included.
        """
        return self._current


def checked(cfg: Config) -> Definitions:
    """Every definition `cfg` names, read and checked together, or the refusal."""
    definitions = resolve_definitions(cfg).warm()
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
    # With the read, so a pause records what was loaded rather than what the folder holds
    # by the time it pauses.
    _ = definitions.digests
    return definitions


def built_from(definitions: Definitions, agent: SpecBase) -> dict[str, str]:
    """The digest of each file a build of `agent` reads: the tools, skills, middleware
    and subagents it names, theirs in turn, and every file in a folder one of them owns.

    Not the agent's own file, which a turn reads from the session's copy.
    """
    places = _named_by(definitions, agent, seen=set())
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
        where = PurePosixPath(subagents.sources[key])
        # A folder named after the subagent is its own, as its bundle is.
        places.add(
            f"subagents/{where.parent}/" if where.parent.name == helper.name
            else f"subagents/{where}"
        )
        places |= _named_by(definitions, helper, seen=seen)
    return places
