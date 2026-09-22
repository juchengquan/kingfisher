"""What a caller asks for. No knowledge of how kingfisher is wired."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from kingfisher.domain.capabilities import Capabilities
from kingfisher.kinds.subagents.spec import RunOn


@dataclass(frozen=True)
class Request:
    """One request: the turn boundary made explicit."""

    task: str
    #: Which agent runs this. A name from the workspace's `agents/`, never a
    #: definition, so an untrusted caller can activate what exists and invent
    #: nothing.
    #:
    #: Optional here and refused downstream rather than defaulted, because there
    #: is no honest default: the agent decides where every prompt in the session
    #: goes and what it costs.
    agent: str | None = None
    session_id: str | None = None
    turn_id: str | None = None
    data: tuple[Path, ...] = ()
    capabilities: Capabilities = field(default_factory=Capabilities)
    #: Delegate name -> where this request wants it to run. Empty by default.
    #:
    #: Separate from `capabilities.models`, which is the deployment's answer to
    #: "which models may this caller name at all". This is the caller's answer to
    #: "which delegate goes on which", and there is nothing to narrow -- an
    #: assignment is not a permission.
    run_on: Mapping[str, RunOn] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.task or not self.task.strip():
            msg = "task must not be empty"
            raise ValueError(msg)
        # Normalise at the edge so everything downstream sees real paths.
        object.__setattr__(self, "data", tuple(Path(p) for p in self.data))

    @classmethod
    def coerce(cls, value: str | Request) -> Request:
        """Accept a bare task string, which is a request naming no agent."""
        return value if isinstance(value, Request) else cls(task=value)


class DecisionError(ValueError):
    """A resume that does not answer the turn it was sent to.

    Separate from the refusals in `_admit`, which are about who is calling. This one
    is about *what* is being answered: an id nothing is waiting on, a decision the
    gate does not take, or a pending call left unanswered. Named rather than left as
    a bare `ValueError` because a caller holding a paused session has somewhere to go
    with it -- read the pending calls again and answer them.
    """


@dataclass(frozen=True)
class Decision:
    """One answer to one gated call."""

    #: Which gated call this answers, as `RunResult.pending` reported it.
    call_id: str
    #: One of `result.DECISIONS`. Checked against that tuple where the answer is
    #: translated rather than here, so the domain keeps one list of what is allowed.
    action: str
    #: What the model is told: why the call was refused, for `reject`, or what the
    #: tool is to have returned, for `respond`. Unused by `approve`.
    message: str = ""


@dataclass(frozen=True)
class Resume:
    """An answer to a turn that stopped for one: the other shape a turn starts in.

    Not a `Request` with the task left out. A resume is not asking for anything, which
    is why there is no `data` here -- placing files on a turn that is finishing work
    already proposed would put them in `/data` with nothing in the conversation saying
    where they came from.

    What it does carry is what `Request` carries to build a graph with, because the
    graph has to be built again: the process may have restarted since the pause. The
    capabilities are re-presented every time and narrowed the ordinary way -- storing
    the grant beside the checkpoint and restoring it would let a later caller resume
    under one they never presented.
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
