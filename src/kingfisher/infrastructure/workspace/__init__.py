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
    "ensure_definitions": "kingfisher.infrastructure.workspace.layout",
    "ensure_layout": "kingfisher.infrastructure.workspace.layout",
    "is_new_workspace": "kingfisher.infrastructure.workspace.layout",
    "SEED_HINT": "kingfisher.core.definitions.seeding",
    "Destination": "kingfisher.core.definitions.seeding",
    "Seeded": "kingfisher.core.definitions.seeding",
    "Skipped": "kingfisher.core.definitions.seeding",
    "Source": "kingfisher.core.definitions.seeding",
    "definitions_source": "kingfisher.core.definitions.seeding",
    "kinds_at": "kingfisher.core.definitions.seeding",
    "middleware_named": "kingfisher.core.definitions.seeding",
    "seed": "kingfisher.core.definitions.seeding",
    "source_ids_named": "kingfisher.core.definitions.seeding",
    "starter_agent": "kingfisher.core.definitions.seeding",
}

__all__ = [
    "EXAMPLE",
    "EXAMPLES",
    "SEED_HINT",
    "TEMPLATES",
    "Destination",
    "MemoryBacking",
    "Seeded",
    "Skipped",
    "Source",
    "definitions_source",
    "ensure_definitions",
    "ensure_layout",
    "is_new_workspace",
    "kinds_at",
    "memory_backing",
    "middleware_named",
    "seed",
    "source_ids_named",
    "starter_agent",
]

if TYPE_CHECKING:
    from kingfisher.core.definitions.seeding import SEED_HINT as SEED_HINT
    from kingfisher.core.definitions.seeding import Destination as Destination
    from kingfisher.core.definitions.seeding import Seeded as Seeded
    from kingfisher.core.definitions.seeding import Skipped as Skipped
    from kingfisher.core.definitions.seeding import Source as Source
    from kingfisher.core.definitions.seeding import (
        definitions_source as definitions_source,
    )
    from kingfisher.core.definitions.seeding import kinds_at as kinds_at
    from kingfisher.core.definitions.seeding import middleware_named as middleware_named
    from kingfisher.core.definitions.seeding import seed as seed
    from kingfisher.core.definitions.seeding import source_ids_named as source_ids_named
    from kingfisher.core.definitions.seeding import starter_agent as starter_agent
    from kingfisher.infrastructure.workspace.backing import MemoryBacking as MemoryBacking
    from kingfisher.infrastructure.workspace.backing import memory_backing as memory_backing
    from kingfisher.infrastructure.workspace.layout import EXAMPLE as EXAMPLE
    from kingfisher.infrastructure.workspace.layout import EXAMPLES as EXAMPLES
    from kingfisher.infrastructure.workspace.layout import TEMPLATES as TEMPLATES
    from kingfisher.infrastructure.workspace.layout import (
        ensure_definitions as ensure_definitions,
    )
    from kingfisher.infrastructure.workspace.layout import ensure_layout as ensure_layout
    from kingfisher.infrastructure.workspace.layout import is_new_workspace as is_new_workspace


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
