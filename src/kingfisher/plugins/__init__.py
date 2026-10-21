"""What a deployment writes a plugin against, and every plugin kingfisher ships.

Lazy, as the package root is: naming this address loads nothing. The core never names
this package; inside kingfisher only the command imports from it.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

_EXPORTS = {
    "Sessions": "kingfisher.core.interfaces",
    "RunEvents": "kingfisher.core.interfaces",
    "Instructions": "kingfisher.core.interfaces",
    "SessionInstructions": "kingfisher.core.interfaces",
    "UnusedResults": "kingfisher.core.interfaces",
    "PdfAttachments": "kingfisher.core.interfaces",
    "ReturnedPdf": "kingfisher.core.interfaces",
    "CommandRunner": "kingfisher.core.interfaces",
    "CommandResult": "kingfisher.core.interfaces",
    "ScratchRun": "kingfisher.core.interfaces",
    "Definitions": "kingfisher.core.definitions.catalogue",
    "list_skills": "kingfisher.core.definitions.skills.registry",
    "WireFormat": "kingfisher.core.config",
    "Plugins": "kingfisher.core.plugins",
    "ShellPrompt": "kingfisher.core.agent.prompting",
    "Layout": "kingfisher.core.layout",
    "DEFAULT_LAYOUT": "kingfisher.core.layout",
    "HostPathError": "kingfisher.core.agent.host_paths",
    "reject_host_path": "kingfisher.core.agent.host_paths",
    "why_no_shell": "kingfisher.core.agent.usable_backend",
    "unrouted_denials": "kingfisher.core.agent.usable_backend",
    "host_path": "kingfisher.core.agent.permitted_backend",
    "scratch_runs_of": "kingfisher.core.agent.permitted_backend",
    "LocalSessions": "kingfisher.plugins.sessions.backend",
    "backend_at": "kingfisher.plugins.sessions.backend",
    "JsonlRunEvents": "kingfisher.plugins.events",
    "ShellSettings": "kingfisher.plugins.sessions.sandbox.confinement",
    "InstructionsFile": "kingfisher.core.agent.prompting",
    "DeleteUnusedResults": "kingfisher.core.session.unused",
    "UnusedFiles": "kingfisher.core.session.unused",
    "UnusedFile": "kingfisher.core.session.unused",
    "WIRE_FORMATS": "kingfisher.core.agent.models",
}

__all__ = [
    "DEFAULT_LAYOUT",
    "WIRE_FORMATS",
    "CommandResult",
    "CommandRunner",
    "Definitions",
    "DeleteUnusedResults",
    "HostPathError",
    "Instructions",
    "InstructionsFile",
    "JsonlRunEvents",
    "Layout",
    "LocalSessions",
    "PdfAttachments",
    "Plugins",
    "ReturnedPdf",
    "RunEvents",
    "ScratchRun",
    "SessionInstructions",
    "Sessions",
    "ShellPrompt",
    "ShellSettings",
    "UnusedFile",
    "UnusedFiles",
    "UnusedResults",
    "WireFormat",
    "backend_at",
    "host_path",
    "list_skills",
    "reject_host_path",
    "scratch_runs_of",
    "unrouted_denials",
    "why_no_shell",
]

if TYPE_CHECKING:
    from kingfisher.core.agent.host_paths import HostPathError as HostPathError
    from kingfisher.core.agent.host_paths import reject_host_path as reject_host_path
    from kingfisher.core.agent.models import WIRE_FORMATS as WIRE_FORMATS
    from kingfisher.core.agent.permitted_backend import host_path as host_path
    from kingfisher.core.agent.permitted_backend import scratch_runs_of as scratch_runs_of
    from kingfisher.core.agent.prompting import InstructionsFile as InstructionsFile
    from kingfisher.core.agent.prompting import ShellPrompt as ShellPrompt
    from kingfisher.core.agent.usable_backend import unrouted_denials as unrouted_denials
    from kingfisher.core.agent.usable_backend import why_no_shell as why_no_shell
    from kingfisher.core.config import WireFormat as WireFormat
    from kingfisher.core.definitions.catalogue import Definitions as Definitions
    from kingfisher.core.definitions.skills.registry import list_skills as list_skills
    from kingfisher.core.interfaces import CommandResult as CommandResult
    from kingfisher.core.interfaces import CommandRunner as CommandRunner
    from kingfisher.core.interfaces import Instructions as Instructions
    from kingfisher.core.interfaces import PdfAttachments as PdfAttachments
    from kingfisher.core.interfaces import ReturnedPdf as ReturnedPdf
    from kingfisher.core.interfaces import RunEvents as RunEvents
    from kingfisher.core.interfaces import ScratchRun as ScratchRun
    from kingfisher.core.interfaces import SessionInstructions as SessionInstructions
    from kingfisher.core.interfaces import Sessions as Sessions
    from kingfisher.core.interfaces import UnusedResults as UnusedResults
    from kingfisher.core.layout import DEFAULT_LAYOUT as DEFAULT_LAYOUT
    from kingfisher.core.layout import Layout as Layout
    from kingfisher.core.plugins import Plugins as Plugins
    from kingfisher.core.session.unused import DeleteUnusedResults as DeleteUnusedResults
    from kingfisher.core.session.unused import UnusedFile as UnusedFile
    from kingfisher.core.session.unused import UnusedFiles as UnusedFiles
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
    globals()[name] = value  # resolved once: later lookups skip __getattr__
    return value


def __dir__() -> list[str]:
    return __all__
