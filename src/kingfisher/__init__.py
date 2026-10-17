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
    "AccessError": "kingfisher.core.access.source_ids",
    "ArtifactError": "kingfisher.core.result",
    "UNSCOPED": "kingfisher.core.access.source_ids",
    # How a tool file says who may use its tool, or gives it another name.
    "ToolSpec": "kingfisher.core.definitions.tools.spec",
    "Capabilities": "kingfisher.core.access.capabilities",
    "CapabilityError": "kingfisher.core.access.capabilities",
    "QuotaExceededError": "kingfisher.core.session.model",
    "SessionBusyError": "kingfisher.core.session.model",
    "SubagentError": "kingfisher.core.definitions.subagents.spec",
    "UnsafeReferenceError": "kingfisher.core.agent.references",
    # For a workspace tool file, outside the wheel by construction, to annotate
    # `runtime: ToolRuntime[ToolContext]`: a bare `ToolRuntime` has pydantic warn per call.
    "ToolContext": "kingfisher.core.agent.tool_context",
    "UnknownSessionError": "kingfisher.core.session.model",
    "Config": "kingfisher.core.config",
    "WorkspacePaths": "kingfisher.core.config",
    "Landing": "kingfisher.core.config",
    "Kingfisher": "kingfisher.core.service",
    "ConfigError": "kingfisher.core.config",
    "Request": "kingfisher.core.request",
    # What a caller holding a paused session needs to answer the gated call.
    "Resume": "kingfisher.core.request",
    "Decision": "kingfisher.core.request",
    "DecisionError": "kingfisher.core.request",
    "PendingDecision": "kingfisher.core.result",
    # One row of `RunResult.ledger`, for a caller that keeps them.
    "LedgerEntry": "kingfisher.core.result",
    "RunOn": "kingfisher.core.definitions.subagents.spec",
    "RunEvent": "kingfisher.core.result",
    "RunResult": "kingfisher.core.result",
    "Session": "kingfisher.core.session.handle",
    "definitions_source": "kingfisher.core.definitions.seeding",
    "ensure_definitions": "kingfisher.core.workspace",
    "kinds_at": "kingfisher.core.definitions.seeding",
    "seed": "kingfisher.core.definitions.seeding",
    "Seeded": "kingfisher.core.definitions.seeding",
    "inventory": "kingfisher.core.inventory",
    "Inventory": "kingfisher.core.inventory",
    # Where this deployment reads from, so a library caller can ask what `doctor` prints.
    "Origin": "kingfisher.core.origins",
    "Origins": "kingfisher.core.origins",
    "config_from_env": "kingfisher.core.environment",
    "paths_from_env": "kingfisher.core.environment",
    "run": "kingfisher.core.run",
    "stream": "kingfisher.core.run",
}

__all__ = [
    "UNSCOPED",
    "AccessError",
    "ArtifactError",
    "Capabilities",
    "CapabilityError",
    "Config",
    "ConfigError",
    "Decision",
    "DecisionError",
    "Held",
    "Inventory",
    "Kingfisher",
    "Landing",
    "LedgerEntry",
    "Origin",
    "Origins",
    "PendingDecision",
    "QuotaExceededError",
    "Request",
    "Resume",
    "RunEvent",
    "RunOn",
    "RunResult",
    "Seeded",
    "Session",
    "SessionBusyError",
    "SubagentError",
    "ToolContext",
    "ToolSpec",
    "UnknownSessionError",
    "UnsafeReferenceError",
    "WorkspacePaths",
    "config_from_env",
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
    from kingfisher.core.access.source_ids import AccessError as AccessError
    from kingfisher.core.access.source_ids import Held as Held
    from kingfisher.core.agent.references import UnsafeReferenceError as UnsafeReferenceError
    from kingfisher.core.agent.tool_context import ToolContext as ToolContext
    from kingfisher.core.config import Config as Config
    from kingfisher.core.config import ConfigError as ConfigError
    from kingfisher.core.config import Landing as Landing
    from kingfisher.core.config import WorkspacePaths as WorkspacePaths
    from kingfisher.core.definitions.seeding import Seeded as Seeded
    from kingfisher.core.definitions.seeding import definitions_source as definitions_source
    from kingfisher.core.definitions.seeding import kinds_at as kinds_at
    from kingfisher.core.definitions.seeding import seed as seed
    from kingfisher.core.definitions.subagents.spec import RunOn as RunOn
    from kingfisher.core.definitions.subagents.spec import SubagentError as SubagentError
    from kingfisher.core.definitions.tools.spec import ToolSpec as ToolSpec
    from kingfisher.core.environment import config_from_env as config_from_env
    from kingfisher.core.environment import paths_from_env as paths_from_env
    from kingfisher.core.inventory import Inventory as Inventory
    from kingfisher.core.inventory import inventory as inventory
    from kingfisher.core.origins import Origin as Origin
    from kingfisher.core.origins import Origins as Origins
    from kingfisher.core.request import Decision as Decision
    from kingfisher.core.request import DecisionError as DecisionError
    from kingfisher.core.request import Request as Request
    from kingfisher.core.request import Resume as Resume
    from kingfisher.core.result import ArtifactError as ArtifactError
    from kingfisher.core.result import LedgerEntry as LedgerEntry
    from kingfisher.core.result import PendingDecision as PendingDecision
    from kingfisher.core.result import RunEvent as RunEvent
    from kingfisher.core.result import RunResult as RunResult
    from kingfisher.core.run import run as run
    from kingfisher.core.run import stream as stream
    from kingfisher.core.service import Kingfisher as Kingfisher
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
