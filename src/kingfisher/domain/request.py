"""What a caller asks for. No knowledge of how kingfisher is wired."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from kingfisher.domain.capabilities import Capabilities
from kingfisher.subagents.spec import RunOn


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
    `data` are files supplied to the *session*. They are copied into `/data`,
    where they stay: the next turn still has them without being handed them
    again. That lifetime is the only difference from `inputs`, and it is why
    both exist rather than one flag with a mode.
    Wanting files written is one kind of task among many, so there is no field
    for it: a request that wants `report.md`, a CSV, or nothing at all says so
    in `task`, in its own words. Nothing here privileges one convention.
    `capabilities` names the tools, skills and subagents this request activates.
    Unset means everything the workspace offers; a service clamps it with
    `intersect` before running, because authorising the caller is not the
    request's job.
    """

    task: str
    #: Which agent runs this. A name from the workspace's `agents/`, never a
    #: definition -- the same rule the rest of this record follows, so an
    #: untrusted caller can activate what exists and invent nothing.
    #:
    #: Optional here and refused downstream rather than defaulted, because there
    #: is no honest default: the agent decides where every prompt in the session
    #: goes and what it costs, and a default would put that choice somewhere the
    #: call site never mentions.
    agent: str | None = None
    session_id: str | None = None
    turn_id: str | None = None
    inputs: tuple[Path, ...] = ()
    data: tuple[Path, ...] = ()
    # The same two, for a caller with no host paths. Ids resolved by a
    # `FileStore` the deployment wired, exactly as `skill_refs` are resolved by
    # a `DefinitionStore` -- so a remote caller can send files without
    # kingfisher taking bytes over its own wire.
    #
    # Kept apart from `inputs` and `data` rather than overloading them: one is
    # a path this process can already read, the other is a name only a store can
    # turn into content, and the refusals differ.
    input_refs: tuple[str, ...] = ()
    data_refs: tuple[str, ...] = ()
    # Provisioning, not activation. These are catalogue ids: they say which
    # definitions to fetch and unpack for this session, while `capabilities`
    # still selects by name. Keeping them apart is what stops a catalogue's
    # identifier scheme leaking into the agent's vocabulary, and the agent's
    # naming rules leaking into the catalogue.
    skill_refs: tuple[str, ...] = ()
    subagent_refs: tuple[str, ...] = ()
    capabilities: Capabilities = field(default_factory=Capabilities)
    #: Delegate name -> where this request wants it to run. Empty by default.
    #:
    #: Separate from `capabilities` because the two answer different questions.
    #: `capabilities.models` is the deployment's answer to "which models may
    #: this caller name at all", and it narrows like everything else there.
    #: This is the caller's answer to "which delegate goes on which", and there
    #: is nothing to narrow -- an assignment is not a permission.
    run_on: Mapping[str, RunOn] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.task or not self.task.strip():
            msg = "task must not be empty"
            raise ValueError(msg)
        # Normalise at the edge so everything downstream sees real paths.
        object.__setattr__(self, "inputs", tuple(Path(p) for p in self.inputs))
        object.__setattr__(self, "data", tuple(Path(p) for p in self.data))

    @classmethod
    def coerce(cls, value: str | Request) -> Request:
        """Accept a bare task string, which is now a request naming no agent.

        Kept rather than removed, and it no longer runs: `run("do a thing")`
        builds a request with no `agent` and is refused where the catalogue is
        known, with a message listing what this workspace offers. That is the
        useful failure -- the alternative was refusing here, where there is no
        catalogue to name anything from, and a caller would be told only that
        something was missing.
        """
        return value if isinstance(value, Request) else cls(task=value)
