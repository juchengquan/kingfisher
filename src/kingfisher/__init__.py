"""kingfisher — a personal, local, general-purpose agent built on deepagents.

Names resolve lazily, through `__getattr__`: `import deepagents` takes most of a second,
mostly provider SDKs most deployments never call, and an eager re-export would put that on
every consumer. `test_a_public_name_that_runs_no_agent_loads_no_runtime` holds it.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

__version__ = "0.1.0"

#: Public name -> the module that defines it. A name is here for a caller outside this
#: wheel; the command ships in it, and imports anything else from where it is defined.
_EXPORTS = {
    "Held": "kingfisher.core.access.source_ids",
    "CallerError": "kingfisher.core.access.source_ids",
    "ArtifactError": "kingfisher.core.result",
    "MemoryEntryError": "kingfisher.core.result",
    "UNSCOPED": "kingfisher.core.access.source_ids",
    # How a tool file says who may use its tool, or gives it another name.
    "ToolSpec": "kingfisher.core.definitions.tools.spec",
    "Capabilities": "kingfisher.core.access.capabilities",
    "CapabilityError": "kingfisher.core.access.capabilities",
    "InputsError": "kingfisher.core.session.files",
    "QuotaExceededError": "kingfisher.core.session.model",
    "SessionBusyError": "kingfisher.core.session.model",
    "SubagentError": "kingfisher.core.definitions.subagents.spec",
    # For an offered tool file, outside the wheel by construction, to annotate
    # `runtime: ToolRuntime[ToolContext]`: a bare `ToolRuntime` has pydantic warn per call.
    "ToolContext": "kingfisher.core.agent.tool_context",
    # What `ToolContext.run` hands a tool back.
    "ScratchRun": "kingfisher.core.interfaces",
    "UnknownSessionError": "kingfisher.core.session.model",
    "Config": "kingfisher.core.config",
    # What a `Config` built in code is made of: its catalogue, and its vocabulary.
    "Models": "kingfisher.core.config",
    "ModelProfile": "kingfisher.core.config",
    "Endpoint": "kingfisher.core.config",
    "WorkspacePaths": "kingfisher.core.config",
    "Landing": "kingfisher.core.config",
    "Kingfisher": "kingfisher.core.service",
    "ConfigError": "kingfisher.core.config",
    "Request": "kingfisher.core.request",
    # Which graph a tool's call is made in, for a tool or a middleware to read.
    "current_graph": "kingfisher.core.agent.tool_context",
    # What a tool reads off `runtime.context.caller`.
    "Caller": "kingfisher.core.agent.tool_context",
    # What a tool reads off `runtime.context.reachable`, and what its `listing` answers.
    "Reachable": "kingfisher.core.reachable",
    "Listing": "kingfisher.core.reachable",
    # What a caller holding a paused session needs to answer the gated call.
    "Resume": "kingfisher.core.request",
    "Decision": "kingfisher.core.request",
    "DecisionError": "kingfisher.core.request",
    "PendingDecision": "kingfisher.core.result",
    # One row of `RunResult.ledger`, for a caller that keeps them.
    "LedgerEntry": "kingfisher.core.result",
    "MemoryChanges": "kingfisher.core.result",
    "MemoryEntry": "kingfisher.core.result",
    "RunOn": "kingfisher.core.definitions.subagents.spec",
    # For a module's `SUBAGENTS` to be checked against, imported under `TYPE_CHECKING`.
    "PortableSubagent": "kingfisher.core.definitions.subagents.spec",
    "CompiledSubagent": "kingfisher.core.definitions.subagents.spec",
    "RunEvent": "kingfisher.core.result",
    "RunResult": "kingfisher.core.result",
    # What `Kingfisher.sent` answers: the first model call of a turn, as it would be sent.
    "Sent": "kingfisher.core.result",
    "SentBlock": "kingfisher.core.result",
    "SentTool": "kingfisher.core.result",
    "SENT_SOURCES": "kingfisher.core.result",
    # Every `kind` a `RunEvent` can carry, and every `stop_reason` a `RunResult` can.
    "KINDS": "kingfisher.core.result",
    "STOP_REASONS": "kingfisher.core.result",
    "Session": "kingfisher.core.session.handle",
    "definitions_source": "kingfisher.core.definitions.seeding",
    "ensure_definitions": "kingfisher.core.workspace",
    "kinds_at": "kingfisher.core.definitions.seeding",
    "seed": "kingfisher.core.definitions.seeding",
    "Seeded": "kingfisher.core.definitions.seeding",
    "inventory": "kingfisher.core.inventory",
    "Inventory": "kingfisher.core.inventory",
    # What `Kingfisher.opened_by` answers: which of a caller's names open anything.
    "Opened": "kingfisher.core.access.source_ids",
    "Opening": "kingfisher.core.access.source_ids",
    # Where this deployment reads from, so a library caller can ask what `doctor` prints.
    "Origin": "kingfisher.core.origins",
    "Origins": "kingfisher.core.origins",
    "config_from_env": "kingfisher.core.environment",
    "paths_from_env": "kingfisher.core.environment",
    "run": "kingfisher.core.run",
    "stream": "kingfisher.core.run",
}

__all__ = [
    "KINDS",
    "SENT_SOURCES",
    "STOP_REASONS",
    "UNSCOPED",
    "ArtifactError",
    "Caller",
    "CallerError",
    "Capabilities",
    "CapabilityError",
    "CompiledSubagent",
    "Config",
    "ConfigError",
    "Decision",
    "DecisionError",
    "Endpoint",
    "Held",
    "InputsError",
    "Inventory",
    "Kingfisher",
    "Landing",
    "LedgerEntry",
    "Listing",
    "MemoryChanges",
    "MemoryEntry",
    "MemoryEntryError",
    "ModelProfile",
    "Models",
    "Opened",
    "Opening",
    "Origin",
    "Origins",
    "PendingDecision",
    "PortableSubagent",
    "QuotaExceededError",
    "Reachable",
    "Request",
    "Resume",
    "RunEvent",
    "RunOn",
    "RunResult",
    "ScratchRun",
    "Seeded",
    "Sent",
    "SentBlock",
    "SentTool",
    "Session",
    "SessionBusyError",
    "SubagentError",
    "ToolContext",
    "ToolSpec",
    "UnknownSessionError",
    "WorkspacePaths",
    "config_from_env",
    "current_graph",
    "definitions_source",
    "ensure_definitions",
    "inventory",
    "kinds_at",
    "paths_from_env",
    "run",
    "seed",
    "stream",
]

if TYPE_CHECKING:
    # So a checker sees the real types, not `__getattr__`'s `Any`. `X as X` is what marks
    # a re-export (PEP 484), not a redundancy.
    from kingfisher.core.access.capabilities import Capabilities as Capabilities
    from kingfisher.core.access.capabilities import CapabilityError as CapabilityError
    from kingfisher.core.access.source_ids import UNSCOPED as UNSCOPED
    from kingfisher.core.access.source_ids import CallerError as CallerError
    from kingfisher.core.access.source_ids import Held as Held
    from kingfisher.core.access.source_ids import Opened as Opened
    from kingfisher.core.access.source_ids import Opening as Opening
    from kingfisher.core.agent.tool_context import Caller as Caller
    from kingfisher.core.agent.tool_context import ToolContext as ToolContext
    from kingfisher.core.agent.tool_context import current_graph as current_graph
    from kingfisher.core.config import Config as Config
    from kingfisher.core.config import ConfigError as ConfigError
    from kingfisher.core.config import Endpoint as Endpoint
    from kingfisher.core.config import Landing as Landing
    from kingfisher.core.config import ModelProfile as ModelProfile
    from kingfisher.core.config import Models as Models
    from kingfisher.core.config import WorkspacePaths as WorkspacePaths
    from kingfisher.core.definitions.seeding import Seeded as Seeded
    from kingfisher.core.definitions.seeding import definitions_source as definitions_source
    from kingfisher.core.definitions.seeding import kinds_at as kinds_at
    from kingfisher.core.definitions.seeding import seed as seed
    from kingfisher.core.definitions.subagents.spec import CompiledSubagent as CompiledSubagent
    from kingfisher.core.definitions.subagents.spec import PortableSubagent as PortableSubagent
    from kingfisher.core.definitions.subagents.spec import RunOn as RunOn
    from kingfisher.core.definitions.subagents.spec import SubagentError as SubagentError
    from kingfisher.core.definitions.tools.spec import ToolSpec as ToolSpec
    from kingfisher.core.environment import config_from_env as config_from_env
    from kingfisher.core.environment import paths_from_env as paths_from_env
    from kingfisher.core.interfaces import ScratchRun as ScratchRun
    from kingfisher.core.inventory import Inventory as Inventory
    from kingfisher.core.inventory import inventory as inventory
    from kingfisher.core.origins import Origin as Origin
    from kingfisher.core.origins import Origins as Origins
    from kingfisher.core.reachable import Listing as Listing
    from kingfisher.core.reachable import Reachable as Reachable
    from kingfisher.core.request import Decision as Decision
    from kingfisher.core.request import DecisionError as DecisionError
    from kingfisher.core.request import Request as Request
    from kingfisher.core.request import Resume as Resume
    from kingfisher.core.result import KINDS as KINDS
    from kingfisher.core.result import SENT_SOURCES as SENT_SOURCES
    from kingfisher.core.result import STOP_REASONS as STOP_REASONS
    from kingfisher.core.result import ArtifactError as ArtifactError
    from kingfisher.core.result import LedgerEntry as LedgerEntry
    from kingfisher.core.result import MemoryChanges as MemoryChanges
    from kingfisher.core.result import MemoryEntry as MemoryEntry
    from kingfisher.core.result import MemoryEntryError as MemoryEntryError
    from kingfisher.core.result import PendingDecision as PendingDecision
    from kingfisher.core.result import RunEvent as RunEvent
    from kingfisher.core.result import RunResult as RunResult
    from kingfisher.core.result import Sent as Sent
    from kingfisher.core.result import SentBlock as SentBlock
    from kingfisher.core.result import SentTool as SentTool
    from kingfisher.core.run import run as run
    from kingfisher.core.run import stream as stream
    from kingfisher.core.service import Kingfisher as Kingfisher
    from kingfisher.core.session.files import InputsError as InputsError
    from kingfisher.core.session.handle import Session as Session
    from kingfisher.core.session.model import QuotaExceededError as QuotaExceededError
    from kingfisher.core.session.model import SessionBusyError as SessionBusyError
    from kingfisher.core.session.model import UnknownSessionError as UnknownSessionError
    from kingfisher.core.workspace import ensure_definitions as ensure_definitions


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
