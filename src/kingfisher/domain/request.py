"""What a caller asks for. No knowledge of how kingfisher is wired."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Request:
    """One request: the turn boundary made explicit.

    A stateless service receives exactly these four things and passes them
    straight through; `cfg`, `agent` and `checkpointer` stay keyword arguments
    on the entrypoint because they describe how this kingfisher is configured,
    not what is being asked of it.

    `session_id` continues a conversation; omitted, a new one starts.
    `turn_id` should be the caller's own request id where one exists — it makes
    a retry idempotent rather than forking a second turn.
    `inputs` are files supplied with this request. They are copied into the
    turn's `input/` directory, never into `/data`: they arrive fresh each round
    and leave with the turn.
    """

    task: str
    session_id: str | None = None
    turn_id: str | None = None
    inputs: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        if not self.task or not self.task.strip():
            msg = "task must not be empty"
            raise ValueError(msg)
        # Normalise at the edge so everything downstream sees real paths.
        object.__setattr__(self, "inputs", tuple(Path(p) for p in self.inputs))

    @classmethod
    def coerce(cls, value: str | Request) -> Request:
        """Accept a bare task string so `run("do a thing")` still reads well."""
        return value if isinstance(value, cls) else cls(task=value)
