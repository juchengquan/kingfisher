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

    A stateless service receives exactly these fields and passes them straight
    through; `cfg`, `graph`, `checkpointer` and `dirs` stay keyword arguments on
    the entrypoint, because they describe how this kingfisher is configured rather
    than what is being asked of it.

    `session_id` continues a conversation; omitted, a new one starts.
    `turn_id` should be the caller's own request id where one exists -- it makes a
    retry idempotent rather than forking a second turn.
    `inputs` are files supplied with this request, copied into the turn's `input/`
    directory and never into `/data`: they arrive fresh each round and leave with
    the turn.
    `data` are files supplied to the *session*, copied into `/data` where they
    stay. That lifetime is the only difference between the two, and why both exist
    rather than one flag with a mode.
    `capabilities` names the tools, skills and subagents this request activates.
    Unset means everything the workspace offers; a service clamps it with
    `intersect` before running, because authorising the caller is not the
    request's job.

    Wanting files written is one kind of task among many, so there is no field for
    it: a request says so in `task`, in its own words.
    """

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
    inputs: tuple[Path, ...] = ()
    data: tuple[Path, ...] = ()
    # The same two, for a caller with no host paths: ids resolved by a `FileStore`
    # the deployment wired, so a remote caller can send files without kingfisher
    # taking bytes over its own wire. Kept apart from `inputs` and `data` rather
    # than overloading them -- one is a path this process can already read, the
    # other a name only a store can turn into content, and the refusals differ.
    input_refs: tuple[str, ...] = ()
    data_refs: tuple[str, ...] = ()
    # Provisioning, not activation. These are catalogue ids saying which
    # definitions to fetch for this session, while `capabilities` still selects by
    # name. Keeping them apart stops a catalogue's identifier scheme leaking into
    # the agent's vocabulary, and the agent's naming rules into the catalogue.
    skill_refs: tuple[str, ...] = ()
    subagent_refs: tuple[str, ...] = ()
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
        object.__setattr__(self, "inputs", tuple(Path(p) for p in self.inputs))
        object.__setattr__(self, "data", tuple(Path(p) for p in self.data))

    @classmethod
    def coerce(cls, value: str | Request) -> Request:
        """Accept a bare task string, which is a request naming no agent.

        It is refused where the catalogue is known, with a message listing what
        this workspace offers. Refusing *here* instead would tell a caller only
        that something was missing, with no catalogue to name anything from.
        """
        return value if isinstance(value, Request) else cls(task=value)
