"""What a caller asks for. No knowledge of how kingfisher is wired."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from kingfisher.domain.capabilities import Capabilities


@dataclass(frozen=True)
class Request:
    """One request: the turn boundary made explicit.

    A stateless service receives exactly these five things and passes them
    straight through; `cfg`, `agent` and `checkpointer` stay keyword arguments
    on the entrypoint because they describe how this kingfisher is configured,
    not what is being asked of it.

    `session_id` continues a conversation; omitted, a new one starts.
    `turn_id` should be the caller's own request id where one exists — it makes
    a retry idempotent rather than forking a second turn.
    `inputs` are files supplied with this request. They are copied into the
    turn's `input/` directory, never into `/data`: they arrive fresh each round
    and leave with the turn.
    Wanting files written is one kind of task among many, so there is no field
    for it: a request that wants `report.md`, a CSV, or nothing at all says so
    in `task`, in its own words. Nothing here privileges one convention.
    `capabilities` names the tools, skills and subagents this request activates.
    Unset means everything the workspace offers; a service clamps it with
    `intersect` before running, because authorising the caller is not the
    request's job.
    """

    task: str
    session_id: str | None = None
    turn_id: str | None = None
    inputs: tuple[Path, ...] = ()
    # Provisioning, not activation. These are catalogue ids: they say which
    # definitions to fetch and unpack for this session, while `capabilities`
    # still selects by name. Keeping them apart is what stops a catalogue's
    # identifier scheme leaking into the agent's vocabulary, and the agent's
    # naming rules leaking into the catalogue.
    skill_refs: tuple[str, ...] = ()
    subagent_refs: tuple[str, ...] = ()
    capabilities: Capabilities = field(default_factory=Capabilities)

    def __post_init__(self) -> None:
        if not self.task or not self.task.strip():
            msg = "task must not be empty"
            raise ValueError(msg)
        # Normalise at the edge so everything downstream sees real paths.
        object.__setattr__(self, "inputs", tuple(Path(p) for p in self.inputs))

    @classmethod
    def coerce(cls, value: str | Request) -> Request:
        """Accept a bare task string so `run("do a thing")` still reads well."""
        return value if isinstance(value, Request) else cls(task=value)
