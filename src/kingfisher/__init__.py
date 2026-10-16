"""kingfisher — a personal, local, general-purpose agent built on deepagents.

Names resolve lazily, through `__getattr__` below. `import deepagents` costs about
1.4s, most of it langchain-anthropic, langchain-openai and langchain-google-genai,
which it imports at module level and which most deployments never call; this front
door is about 1ms. Eager re-exports would put the 1.4s on every consumer --
`kingfisher --help`, a config check, a test that only touches `Request`. *(Measured
2026-09-08.)*
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

__version__ = "0.1.0"

#: Public name -> the module that defines it. The single source for both `__getattr__`
#: and `__all__`, so the two cannot drift.
#:
#: **A caller means a caller outside this wheel**, which is the rule eleven names --
#: `build_agent`, `build_model` and the rest -- were removed under.
#: Every one is live, and every one of their callers already imports it from the module
#: that defines it, so nothing had ever come through the front door for them. The
#: command ships in this distribution and is family: it reaches such a name directly,
#: and still comes through the front door for every name that *is* here.
#:
#: Narrowed rather than deleted. `from kingfisher.core.agent.assembly import
#: build_agent` still works and is what the package itself does. An outside caller on
#: the old spelling changes one import line, which is a real cost measured against users
#: this repository cannot see -- accepted at 0.1.0, and noted here rather than
#: discovered later.
_EXPORTS = {
    "Held": "kingfisher.core.access.source_ids",
    "AccessError": "kingfisher.core.access.source_ids",
    "ArtifactError": "kingfisher.core.result",
    "UNSCOPED": "kingfisher.core.access.source_ids",
    "Capabilities": "kingfisher.core.access.capabilities",
    "CapabilityError": "kingfisher.core.access.capabilities",
    "QuotaExceededError": "kingfisher.core.session.model",
    "SessionBusyError": "kingfisher.core.session.model",
    "SubagentError": "kingfisher.core.definitions.subagents.spec",
    "UnsafeReferenceError": "kingfisher.core.agent.references",
    # What a workspace tool annotates `runtime` with to be handed the turn's backend:
    # `runtime: ToolRuntime[ToolContext]`. A tool file is a caller outside this
    # wheel by construction -- it lives in a deployment's workspace -- and the bare
    # `ToolRuntime` it would otherwise write has pydantic warn on every call that
    # the context it was handed is not the `None` it declared.
    "ToolContext": "kingfisher.core.agent.tool_context",
    # How a tool file says who may use its tool. Called inside a deployment's own
    # `tools/`, for the reason `ToolContext` is imported there.
    "tool": "kingfisher.core.agent.audienced_tool",
    "UnknownSessionError": "kingfisher.core.session.model",
    "Config": "kingfisher.core.config",
    "WorkspacePaths": "kingfisher.core.config",
    "Landing": "kingfisher.core.config",
    "Kingfisher": "kingfisher.core.service",
    "ConfigError": "kingfisher.core.config",
    "Request": "kingfisher.core.request",
    # The four a caller needs to answer a gated call: what it was asked, how
    # to answer, the answer itself, and the refusal for an answer that does
    # not fit. All public for the reason `CapabilityError` is -- a consumer
    # holding a paused session has to be able to branch on this.
    "Resume": "kingfisher.core.request",
    "Decision": "kingfisher.core.request",
    "DecisionError": "kingfisher.core.request",
    "PendingDecision": "kingfisher.core.result",
    # One row of `RunResult.ledger`, which a caller keeping them names.
    "LedgerEntry": "kingfisher.core.result",
    "RunOn": "kingfisher.core.definitions.subagents.spec",
    "RunEvent": "kingfisher.core.result",
    "RunResult": "kingfisher.core.result",
    "SessionInfo": "kingfisher.core.session.model",
    "definitions_source": "kingfisher.core.definitions.seeding",
    "ensure_definitions": "kingfisher.core.workspace",
    "kinds_at": "kingfisher.core.definitions.seeding",
    "seed": "kingfisher.core.definitions.seeding",
    "Seeded": "kingfisher.core.definitions.seeding",
    "inventory": "kingfisher.core.inventory",
    "Inventory": "kingfisher.core.inventory",
    # Where this deployment reads from, as against what it offers. Public
    # because the answer was assembled three times and agreed nowhere: the
    # command printed four of eleven places, `doctor` printed one, and a
    # library caller had no way to ask at all.
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
    "SessionBusyError",
    "SessionInfo",
    "SubagentError",
    "ToolContext",
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
    "tool",
]

if TYPE_CHECKING:
    # So type checkers and IDEs see the real symbols rather than `Any`.
    # Redundant aliases mark these as re-exports; `__all__` is computed, so a
    # checker cannot otherwise tell they are public.
    from kingfisher.core.access.capabilities import Capabilities as Capabilities
    from kingfisher.core.access.capabilities import CapabilityError as CapabilityError
    from kingfisher.core.access.source_ids import UNSCOPED as UNSCOPED
    from kingfisher.core.access.source_ids import AccessError as AccessError
    from kingfisher.core.access.source_ids import Held as Held
    from kingfisher.core.agent.audienced_tool import tool as tool
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
    from kingfisher.core.session.model import QuotaExceededError as QuotaExceededError
    from kingfisher.core.session.model import SessionBusyError as SessionBusyError
    from kingfisher.core.session.model import SessionInfo as SessionInfo
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
    globals()[name] = value  # resolve once; subsequent lookups skip __getattr__
    return value


def __dir__() -> list[str]:
    return __all__
