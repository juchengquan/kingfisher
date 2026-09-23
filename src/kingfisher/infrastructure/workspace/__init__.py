"""The directory a deployment runs out of: laying it out, filling it, adding to it.

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
    "DataError": "kingfisher.infrastructure.workspace.placement",
    "DataPlacement": "kingfisher.infrastructure.workspace.placement",
    "place_data": "kingfisher.infrastructure.workspace.placement",
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
    "LocalSessionRoot": "kingfisher.infrastructure.workspace.sessions",
    "claim_path": "kingfisher.infrastructure.workspace.sessions",
    "collect_artifacts": "kingfisher.infrastructure.workspace.sessions",
    "ensure_session_layout": "kingfisher.infrastructure.workspace.sessions",
    "make_session_dirs": "kingfisher.infrastructure.workspace.sessions",
    "scaffold_memory": "kingfisher.infrastructure.workspace.sessions",
    "session_bytes": "kingfisher.infrastructure.workspace.sessions",
    "AGENT_SNAPSHOT": "kingfisher.infrastructure.workspace.snapshots",
    "agent_snapshot": "kingfisher.infrastructure.workspace.snapshots",
    "agent_started_with": "kingfisher.infrastructure.workspace.snapshots",
    "remember_agent": "kingfisher.infrastructure.workspace.snapshots",
}

__all__ = [
    "AGENT_SNAPSHOT",
    "DESTINATION",
    "EXAMPLE",
    "EXAMPLES",
    "SEED_HINT",
    "STARTER_AGENT",
    "SUGGESTION",
    "TEMPLATES",
    "DataError",
    "DataPlacement",
    "Destination",
    "LocalSessionDirs",
    "LocalSessionRoot",
    "MemoryBacking",
    "Seeded",
    "Skipped",
    "Source",
    "agent_snapshot",
    "agent_started_with",
    "claim_path",
    "collect_artifacts",
    "definitions_source",
    "destination_hint",
    "ensure_layout",
    "ensure_session_layout",
    "is_new_workspace",
    "kinds_at",
    "make_session_dirs",
    "memory_backing",
    "middleware_named",
    "place_data",
    "protect_data",
    "remember_agent",
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
    from kingfisher.infrastructure.workspace.placement import DataError as DataError
    from kingfisher.infrastructure.workspace.placement import DataPlacement as DataPlacement
    from kingfisher.infrastructure.workspace.placement import place_data as place_data
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
    from kingfisher.infrastructure.workspace.sessions import LocalSessionRoot as LocalSessionRoot
    from kingfisher.infrastructure.workspace.sessions import claim_path as claim_path
    from kingfisher.infrastructure.workspace.sessions import (
        collect_artifacts as collect_artifacts,
    )
    from kingfisher.infrastructure.workspace.sessions import (
        ensure_session_layout as ensure_session_layout,
    )
    from kingfisher.infrastructure.workspace.sessions import (
        make_session_dirs as make_session_dirs,
    )
    from kingfisher.infrastructure.workspace.sessions import scaffold_memory as scaffold_memory
    from kingfisher.infrastructure.workspace.sessions import session_bytes as session_bytes
    from kingfisher.infrastructure.workspace.snapshots import AGENT_SNAPSHOT as AGENT_SNAPSHOT
    from kingfisher.infrastructure.workspace.snapshots import agent_snapshot as agent_snapshot
    from kingfisher.infrastructure.workspace.snapshots import (
        agent_started_with as agent_started_with,
    )
    from kingfisher.infrastructure.workspace.snapshots import remember_agent as remember_agent


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
