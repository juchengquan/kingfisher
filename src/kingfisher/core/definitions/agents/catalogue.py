"""Agent definitions held in a directory on this host."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from kingfisher.core.definitions.agents import reading
from kingfisher.core.definitions.agents.spec import AgentError, AgentSpec

# Shared with subagents, which read the same YAML from the same shape of directory.
from kingfisher.core.definitions.documents import DefinitionText, documents_in


@dataclass(frozen=True)
class LocalAgentRepository:
    """The agents defined in one directory."""

    root: Path

    @cached_property
    def _defined(self) -> dict[str, tuple[AgentSpec, str, str]]:
        """Every definition below `root`, parsed once, with its source and text."""
        directory = Path(self.root)
        if not directory.is_dir():
            return {}

        read: list[tuple[AgentSpec, str, str]] = []
        for path in documents_in(directory, error=AgentError):
            where = str(path.relative_to(directory))
            definition = DefinitionText.at(path)
            read.append((reading.read(definition), where, definition.text))

        # Refused, not reported as subagents are: a request names one agent, and two of a
        # name would give it whichever the walk reached last.
        seen: dict[str, str] = {}
        for spec, where, _ in read:
            if (first := seen.get(spec.name)) is not None:
                msg = (
                    f"two agents are called {spec.name!r} -- {first} and {where}. A "
                    f"request names one agent and there is nothing to tell them "
                    f"apart, so rename one of them"
                )
                raise AgentError(msg)
            seen[spec.name] = where
        return {spec.name: (spec, where, text) for spec, where, text in read}

    @cached_property
    def specs(self) -> dict[str, AgentSpec]:
        """Every agent defined here, by name."""
        return {name: spec for name, (spec, _, _) in self._defined.items()}

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._defined)

    @cached_property
    def documents(self) -> dict[str, str]:
        """The text each agent was parsed from, by name."""
        return {name: text for name, (_, _, text) in self._defined.items()}

    @cached_property
    def sources(self) -> dict[str, str]:
        """Where each agent is defined, by name, relative to the catalogue."""
        return {name: where for name, (_, where, _) in self._defined.items()}
