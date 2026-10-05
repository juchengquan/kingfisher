"""The directory a deployment runs out of: laying it out and filling it.

Lazily, because the submodules differ in weight and callers reach for one at a
time: `seeding` loads `yaml` and the catalogue, and a caller that only wants
`permissions` or `sessions` should not pay for them just by naming the package.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

_EXPORTS = {
    "MemoryBacking": "kingfisher.infrastructure.workspace.backing",
    "memory_backing": "kingfisher.infrastructure.workspace.backing",
    "EXAMPLE": "kingfisher.infrastructure.workspace.layout",
    "EXAMPLES": "kingfisher.infrastructure.workspace.layout",
    "TEMPLATES": "kingfisher.infrastructure.workspace.layout",
    "ensure_layout": "kingfisher.infrastructure.workspace.layout",
    "is_new_workspace": "kingfisher.infrastructure.workspace.layout",
    "protect_data": "kingfisher.infrastructure.workspace.permissions",
    "writable_data": "kingfisher.infrastructure.workspace.permissions",
    "DESTINATION": "kingfisher.infrastructure.workspace.seeding",
    "SEED_HINT": "kingfisher.infrastructure.workspace.seeding",
    "STARTER_AGENT": "kingfisher.infrastructure.workspace.seeding",
    "SUGGESTION": "kingfisher.infrastructure.workspace.seeding",
    "Destination": "kingfisher.infrastructure.workspace.seeding",
    "Seeded": "kingfisher.infrastructure.workspace.seeding",
    "Skipped": "kingfisher.infrastructure.workspace.seeding",
    "Source": "kingfisher.infrastructure.workspace.seeding",
    "definitions_source": "kingfisher.infrastructure.workspace.seeding",
    "destination_hint": "kingfisher.infrastructure.workspace.seeding",
    "kinds_at": "kingfisher.infrastructure.workspace.seeding",
    "middleware_named": "kingfisher.infrastructure.workspace.seeding",
    "seed": "kingfisher.infrastructure.workspace.seeding",
    "source_ids_named": "kingfisher.infrastructure.workspace.seeding",
    "LocalSessionDirs": "kingfisher.infrastructure.workspace.sessions",
    "ensure_session_layout": "kingfisher.infrastructure.workspace.sessions",
    "scaffold_memory": "kingfisher.infrastructure.workspace.sessions",
    "session_bytes": "kingfisher.infrastructure.workspace.sessions",
}

__all__ = [
    "DESTINATION",
    "EXAMPLE",
    "EXAMPLES",
    "SEED_HINT",
    "STARTER_AGENT",
    "SUGGESTION",
    "TEMPLATES",
    "Destination",
    "LocalSessionDirs",
    "MemoryBacking",
    "Seeded",
    "Skipped",
    "Source",
    "definitions_source",
    "destination_hint",
    "ensure_layout",
    "ensure_session_layout",
    "is_new_workspace",
    "kinds_at",
    "memory_backing",
    "middleware_named",
    "protect_data",
    "scaffold_memory",
    "seed",
    "session_bytes",
    "source_ids_named",
    "writable_data",
]

if TYPE_CHECKING:
    from kingfisher.infrastructure.workspace.backing import MemoryBacking as MemoryBacking
    from kingfisher.infrastructure.workspace.backing import memory_backing as memory_backing
    from kingfisher.infrastructure.workspace.layout import EXAMPLE as EXAMPLE
    from kingfisher.infrastructure.workspace.layout import EXAMPLES as EXAMPLES
    from kingfisher.infrastructure.workspace.layout import TEMPLATES as TEMPLATES
    from kingfisher.infrastructure.workspace.layout import ensure_layout as ensure_layout
    from kingfisher.infrastructure.workspace.layout import is_new_workspace as is_new_workspace
    from kingfisher.infrastructure.workspace.permissions import protect_data as protect_data
    from kingfisher.infrastructure.workspace.permissions import writable_data as writable_data
    from kingfisher.infrastructure.workspace.seeding import DESTINATION as DESTINATION
    from kingfisher.infrastructure.workspace.seeding import SEED_HINT as SEED_HINT
    from kingfisher.infrastructure.workspace.seeding import STARTER_AGENT as STARTER_AGENT
    from kingfisher.infrastructure.workspace.seeding import SUGGESTION as SUGGESTION
    from kingfisher.infrastructure.workspace.seeding import Destination as Destination
    from kingfisher.infrastructure.workspace.seeding import Seeded as Seeded
    from kingfisher.infrastructure.workspace.seeding import Skipped as Skipped
    from kingfisher.infrastructure.workspace.seeding import Source as Source
    from kingfisher.infrastructure.workspace.seeding import (
        definitions_source as definitions_source,
    )
    from kingfisher.infrastructure.workspace.seeding import destination_hint as destination_hint
    from kingfisher.infrastructure.workspace.seeding import kinds_at as kinds_at
    from kingfisher.infrastructure.workspace.seeding import middleware_named as middleware_named
    from kingfisher.infrastructure.workspace.seeding import seed as seed
    from kingfisher.infrastructure.workspace.seeding import source_ids_named as source_ids_named
    from kingfisher.infrastructure.workspace.sessions import LocalSessionDirs as LocalSessionDirs
    from kingfisher.infrastructure.workspace.sessions import (
        ensure_session_layout as ensure_session_layout,
    )
    from kingfisher.infrastructure.workspace.sessions import scaffold_memory as scaffold_memory
    from kingfisher.infrastructure.workspace.sessions import session_bytes as session_bytes


def __getattr__(name: str) -> Any:
    """PEP 562 lazy re-export."""
    try:
        module = _EXPORTS[name]
    except KeyError:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg) from None

    value = getattr(import_module(module), name)
    globals()[name] = value  # resolve once; subsequent lookups skip __getattr__
    return value


def __dir__() -> list[str]:
    return __all__
