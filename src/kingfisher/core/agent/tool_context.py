"""What a caller's tool is handed for the turn it runs in."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolContext:
    """The run context of every turn kingfisher builds the graph for.

    A tool reads it as `runtime.context`, from a parameter written
    `runtime: ToolRuntime[ToolContext]`.

    **Apart from the backend it carries, and that is what keeps this name cheap.**
    A tool file imports it to write that annotation, and the wrapper the field holds
    subclasses a deepagents class -- defined beside it, naming this type would cost
    every reader the agent runtime.
    """

    #: The turn's filesystem under the turn's permissions: a deepagents
    #: `BackendProtocol`, taking the virtual paths the file tools take. `Any` for the
    #: reason above -- the type cannot be named here without importing it.
    backend: Any
