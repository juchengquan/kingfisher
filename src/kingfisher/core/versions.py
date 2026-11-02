"""The definitions a turn runs on, read and checked as one set."""

from __future__ import annotations

from contextlib import suppress

from .agent.assembly import builtin_tool_names
from .config import Config, ConfigError
from .definitions.catalogue import Definitions, resolve_definitions


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
    return definitions
