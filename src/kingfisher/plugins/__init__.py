"""What a deployment writes a plugin against, and every plugin kingfisher ships.

The interfaces and the records they speak in, beside the implementations a deployment
names as it would its own -- `LocalSessions` among them, which a `Kingfisher` is handed
unless it is handed another. Lazily, as the package root is: naming this address loads
nothing, and each name loads its module the first time it is asked for.

The core never names this package. Inside kingfisher only the command imports from it.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

_EXPORTS = {
    "Sessions": "kingfisher.core.interfaces",
    "RunEvents": "kingfisher.core.interfaces",
    "Instructions": "kingfisher.core.interfaces",
    "CommandRunner": "kingfisher.core.interfaces",
    "CommandResult": "kingfisher.core.interfaces",
    "WireFormat": "kingfisher.core.config",
    "Plugins": "kingfisher.core.plugins",
    "ShellPrompt": "kingfisher.core.agent.prompting",
    "Layout": "kingfisher.core.layout",
    "DEFAULT_LAYOUT": "kingfisher.core.layout",
    "HostPathError": "kingfisher.core.agent.host_paths",
    "LocalSessions": "kingfisher.plugins.sessions.backend",
    "backend_at": "kingfisher.plugins.sessions.backend",
    "JsonlRunEvents": "kingfisher.plugins.events",
    "ShellSettings": "kingfisher.plugins.sessions.sandbox.confinement",
    "InstructionsFile": "kingfisher.core.agent.prompting",
    "WIRE_FORMATS": "kingfisher.core.agent.models",
}

__all__ = [
    "DEFAULT_LAYOUT",
    "WIRE_FORMATS",
    "CommandResult",
    "CommandRunner",
    "HostPathError",
    "Instructions",
    "InstructionsFile",
    "JsonlRunEvents",
    "Layout",
    "LocalSessions",
    "Plugins",
    "RunEvents",
    "Sessions",
    "ShellPrompt",
    "ShellSettings",
    "WireFormat",
    "backend_at",
]

if TYPE_CHECKING:
    from kingfisher.core.agent.host_paths import HostPathError as HostPathError
    from kingfisher.core.agent.models import WIRE_FORMATS as WIRE_FORMATS
    from kingfisher.core.agent.prompting import InstructionsFile as InstructionsFile
    from kingfisher.core.agent.prompting import ShellPrompt as ShellPrompt
    from kingfisher.core.config import WireFormat as WireFormat
    from kingfisher.core.interfaces import CommandResult as CommandResult
    from kingfisher.core.interfaces import CommandRunner as CommandRunner
    from kingfisher.core.interfaces import Instructions as Instructions
    from kingfisher.core.interfaces import RunEvents as RunEvents
    from kingfisher.core.interfaces import Sessions as Sessions
    from kingfisher.core.layout import DEFAULT_LAYOUT as DEFAULT_LAYOUT
    from kingfisher.core.layout import Layout as Layout
    from kingfisher.core.plugins import Plugins as Plugins
    from kingfisher.plugins.events import JsonlRunEvents as JsonlRunEvents
    from kingfisher.plugins.sessions.backend import LocalSessions as LocalSessions
    from kingfisher.plugins.sessions.backend import backend_at as backend_at
    from kingfisher.plugins.sessions.sandbox.confinement import ShellSettings as ShellSettings


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
