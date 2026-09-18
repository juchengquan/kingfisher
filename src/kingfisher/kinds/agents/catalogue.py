"""Agent definitions held in a directory on this host.

`spec` is the format and `reading` parses one; this finds the documents. Why the
package exists, and why `harness/agent.py` is not in it, is `__init__`.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from kingfisher.kinds.agents import reading
from kingfisher.kinds.agents.spec import AgentError, AgentSpec

# The walk comes from the format that already names what a document is called,
# rather than being restated here: both kinds read the same YAML out of the same
# shape of directory, and a second copy is a second thing to keep in step.
from kingfisher.kinds.documents import documents_in


@dataclass(frozen=True)
class LocalAgentRepository:
    """The agents defined in one directory."""

    root: Path

    @cached_property
    def _defined(self) -> dict[str, tuple[AgentSpec, str, str]]:
        """Every definition below `root`, parsed once, with where it came from
        and the document it was parsed from."""
        directory = Path(self.root)
        if not directory.is_dir():
            return {}

        read: list[tuple[AgentSpec, str, str]] = []
        for path in documents_in(directory, error=AgentError):
            where = str(path.relative_to(directory))
            text = path.read_text(encoding="utf-8")
            read.append((reading.read(text, path), where, text))

        # Two of a name is refused here rather than reported, which is the one
        # place this differs from subagents. A request names exactly one agent,
        # so there is no roster for a reference to disambiguate within -- two
        # files claiming `assistant` means a request for `assistant` gets
        # whichever the walk reached last, and nothing anywhere says which.
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
