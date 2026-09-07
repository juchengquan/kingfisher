"""Agent definitions held in a directory on this host.

`domain.agent` owns the format -- what a definition means and what makes one
malformed. Turning a document into one and finding the documents are both here, which
is the arrangement the other kinds have inside their own packages: a kind's reader
sits with its catalogue. An agent has no package of its own, because it is selected
by name rather than registered, so this file is where that rule lands for it.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from kingfisher.domain import agent
from kingfisher.domain.agent import AgentError, AgentSpec
from kingfisher.infrastructure import documents
from kingfisher.infrastructure.importing import skipped

# `SUFFIX` comes from the format that already names it rather than being
# restated here: both are YAML documents kingfisher reads, and a second copy of
# the extension is a second thing to keep in step.
from kingfisher.subagents.reading import NEAR_MISS, SUFFIX


def read_agent(text: str, source: Path) -> AgentSpec:
    """One agent definition. Raises `AgentError` on anything malformed."""
    document = documents.decode(text)
    if isinstance(document, str):
        msg = f"{source.name}: cannot read definition ({document})"
        raise AgentError(msg)
    documents.require_literal_prompt(text, source, AgentError)
    return agent.parse(document, source)


def _definitions_in(directory: Path) -> list[Path]:
    """Every agent document below `directory`, at any depth, in a stable order."""
    found: list[Path] = []
    for entry in sorted(directory.iterdir()):
        if skipped(entry.name):
            continue
        if entry.is_dir():
            found.extend(_definitions_in(entry))
        elif entry.name.endswith(SUFFIX):
            found.append(entry)
        elif entry.suffix == NEAR_MISS:
            msg = (
                f"{entry.name}: kingfisher reads {SUFFIX!r} here, so this file is "
                f"not loaded -- rename it to {entry.stem}{SUFFIX}"
            )
            raise AgentError(msg)
    return found


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
        for path in _definitions_in(directory):
            where = str(path.relative_to(directory))
            text = path.read_text(encoding="utf-8")
            read.append((read_agent(text, path), where, text))

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
