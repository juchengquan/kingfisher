"""Agent definitions held in a directory on this host.

`domain.agent` owns the format -- what a definition means and what makes one
malformed. Turning a document into one and finding the documents are both here,
which is the arrangement the other kinds have inside their own packages: a
kind's reader sits with its catalogue. An agent has no package of its own,
because it is selected by name rather than registered, so this file is where
that rule lands for it.

The subagent repository's shape, minus a half it does not need. There is no
Python-declared form here: a compiled subagent exists because a workspace may
want to hand deepagents a graph it assembled itself, and the thing that *runs*
that graph is the agent -- which is the one position where kingfisher must
still build the surrounding harness. So an agent is a document, always.

Folders are organisation, exactly as they are for subagents and tools:
`agents/support/triage.yaml` is `triage`, because `name:` is the identity and
the path is not.
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
    """One agent definition. Raises `AgentError` on anything malformed.

    The same steps a subagent takes, and deliberately not a shared function
    taking a parser: what differs is the exception, and that is the one thing a
    caller reading a traceback needs to be right. `AgentError` and
    `SubagentError` are not interchangeable to someone finding out which of two
    folders holds the broken file.

    It sat beside `subagents.reading.read` in `infrastructure` while both were
    one file, which made the pair visible at the cost of that file knowing two
    kinds. The pair is now a sentence in each docstring instead, and the two
    checks it shares -- `decode` and `require_literal_prompt` -- are still one
    implementation, called rather than copied.
    """
    document = documents.decode(text)
    if isinstance(document, str):
        msg = f"{source.name}: cannot read definition ({document})"
        raise AgentError(msg)
    documents.require_literal_prompt(text, source, AgentError)
    return agent.parse(document, source)


def _definitions_in(directory: Path) -> list[Path]:
    """Every agent document below `directory`, at any depth, in a stable order.

    Hidden directories and `__pycache__` are skipped for the reason the module
    loader skips them: a one-level scan could never reach whatever a person left
    lying under the catalogue, and a recursive one can.

    `.yml` is named rather than ignored, the same way it is for subagents. It is
    valid YAML everywhere else, so a file spelled that way is a definition
    somebody wrote and kingfisher silently would not read.
    """
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
    """The agents defined in one directory.

    Given the directory itself rather than a workspace to derive one from: the
    catalogue can be deployed outside any workspace and shared by all of them.

    Unlike the other three kinds there is no session layer over this one, and
    that is not an oversight. A request may upload its own skills and subagents
    because those are the caller's own text; an agent decides where every prompt
    in a session goes, and it is pinned for that session's whole life. Adding a
    layer for symmetry would advertise a capability that does not exist.
    """

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
        """The text each agent was parsed from, by name.

        For the snapshot a session takes when it opens. A session runs one agent
        for its whole life, and *this document* is what it runs -- so keeping the
        text is what lets a later turn be built from what the session started
        with rather than from whatever the file says by then.

        The document rather than the parsed spec, because there is already a
        reader for one and there would have to be a writer for the other. It is
        also the thing a person can look at and compare with the file.

        Not on `AgentRepository`, for the reason `sources` is not: a repository
        backed by a service can answer with specs and need not have a document
        to hand back. A deployment supplying one gets no snapshot and the
        behaviour it had before -- see `workspace.snapshots.agent_snapshot`, which is
        where the text is kept, and `agent_started_with`, which reads it back.
        """
        return {name: text for name, (_, _, text) in self._defined.items()}

    @cached_property
    def sources(self) -> dict[str, str]:
        """Where each agent is defined, by name, relative to the catalogue.

        For `--list`, and for the reason the other loaders have one: a folder
        exists so a person can find a file, and a bare name does not help them.
        """
        return {name: where for name, (_, where, _) in self._defined.items()}
