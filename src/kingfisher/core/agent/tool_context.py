"""What a caller's tool is handed for the turn it runs in."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolContext:
    """The run context of every turn kingfisher builds the graph for.

    A tool reads it as `runtime.context`, from a parameter written
    `runtime: ToolRuntime[ToolContext]`. Kept apart from the backend class it carries,
    which subclasses a deepagents one, so importing it for that annotation stays cheap.
    """

    #: The turn's filesystem under its permissions: a deepagents `BackendProtocol` taking
    #: the file tools' virtual paths. `Any`, since naming that type would import it.
    backend: Any
