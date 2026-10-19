"""What a caller asks for. No knowledge of how kingfisher is wired."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from kingfisher.core.access import Capabilities
from kingfisher.core.definitions.subagents.spec import RunOn


@dataclass(frozen=True)
class Request:
    """One request: the turn boundary made explicit."""

    task: str
    #: A name from the workspace's `agents/`, never a definition, so an untrusted caller
    #: can activate what exists and invent nothing. Refused downstream rather than
    #: defaulted: the agent decides where every prompt goes and what it costs.
    agent: str | None = None
    session_id: str | None = None
    turn_id: str | None = None
    inputs: tuple[Path, ...] = ()
    capabilities: Capabilities = field(default_factory=Capabilities)
    #: Subagent name -> where this request wants it to run. Not in `capabilities`, and
    #: never narrowed: an assignment is not a permission.
    run_on: Mapping[str, RunOn] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.task or not self.task.strip():
            msg = "task must not be empty"
            raise ValueError(msg)
        # Normalise at the edge so everything downstream sees real paths.
        object.__setattr__(self, "inputs", tuple(Path(p) for p in self.inputs))

    @classmethod
    def coerce(cls, value: str | Request) -> Request:
        """Accept a bare task string, which is a request naming no agent."""
        return value if isinstance(value, Request) else cls(task=value)


class DecisionError(ValueError):
    """A resume that does not answer the turn it was sent to.

    Its own class rather than a bare `ValueError` because the caller can recover: read
    the pending calls again and answer them.
    """


@dataclass(frozen=True)
class Decision:
    """One answer to one gated call."""

    #: Which gated call this answers, as `RunResult.pending` reported it.
    call_id: str
    #: One of `result.DECISIONS`, checked where the answer is translated rather than
    #: here, so there is one list of what is allowed.
    action: str
    #: What the model is told: why the call was refused, for `reject`, or what the
    #: tool is to have returned, for `respond`. Unused by `approve`.
    message: str = ""


@dataclass(frozen=True)
class Resume:
    """An answer to a turn that stopped for one: the other shape a turn starts in.

    No `inputs`: files placed on a turn finishing work already proposed would sit in
    `/inputs` with nothing in the conversation saying where they came from. The
    capabilities are presented again rather than stored with the pause, which would let
    a later caller resume under a grant they never presented.
    """

    session_id: str
    decisions: tuple[Decision, ...] = ()
    agent: str | None = None
    turn_id: str | None = None
    capabilities: Capabilities = field(default_factory=Capabilities)
    run_on: Mapping[str, RunOn] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.session_id or not self.session_id.strip():
            msg = "session_id must not be empty"
            raise ValueError(msg)
        if not self.decisions:
            msg = "a resume must answer at least one pending decision"
            raise ValueError(msg)
        seen = [decision.call_id for decision in self.decisions]
        if len(set(seen)) != len(seen):
            twice = sorted({call for call in seen if seen.count(call) > 1})
            msg = f"two decisions answer the same call: {', '.join(twice)}"
            raise ValueError(msg)
