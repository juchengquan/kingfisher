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
#: `build_agent`, `default_backend`, `build_model` and the rest -- were removed under.
#: Every one is live, and every one of their callers already imports it from the module
#: that defines it, so nothing had ever come through the front door for them. The
#: command ships in this distribution and is family: it reaches such a name directly,
#: and still comes through the front door for every name that *is* here.
#:
#: Narrowed rather than deleted. `from kingfisher.infrastructure.harness.agent import
#: build_agent` still works and is what the package itself does. An outside caller on
#: the old spelling changes one import line, which is a real cost measured against users
#: this repository cannot see -- accepted at 0.1.0, and noted here rather than
#: discovered later.
_EXPORTS = {
    "Held": "kingfisher.domain.access",
    "AccessError": "kingfisher.domain.access",
    "UNSCOPED": "kingfisher.domain.access",
    "Capabilities": "kingfisher.domain.capabilities",
    "CapabilityError": "kingfisher.domain.capabilities",
    "QuotaExceededError": "kingfisher.domain.session",
    "SessionBusyError": "kingfisher.domain.session",
    "SkillError": "kingfisher.kinds.skills.spec",
    "SubagentError": "kingfisher.kinds.subagents.spec",
    "UnsafeReferenceError": "kingfisher.domain.references",
    "HostPathError": "kingfisher.infrastructure.harness.host_paths",
    "LocalSessionStore": "kingfisher.infrastructure.session_store",
    # The port's contract, for a deployment checking its own adapter against it.
    # Public because it is the one thing in `testing` anybody outside this
    # repository is meant to import -- and public *here* rather than left to
    # `import kingfisher.testing`, because `READ_ELSEWHERE` says a name this
    # package publishes belongs in `__all__` and reaching for that table instead
    # is how something gets published without saying so.
    "SESSION_STORE_CONTRACT": "kingfisher.testing",
    # The other two ports a deployment replaces. Both kits do more than read --
    # one creates directories, one runs commands -- which is a property of the
    # ports rather than of the kits: there is no way to check that a runner runs
    # things without running things.
    "SESSION_ROOT_CONTRACT": "kingfisher.testing",
    "COMMAND_RUNNER_CONTRACT": "kingfisher.testing",
    # The fourth kit, and the one not in `testing`. Its sharpest check asks whether
    # deepagents will recognise a backend as running commands, which needs the
    # package -- and `testing` is permitted nothing foreign, an entry covering
    # `config` and `layout` too. So it lives where the backend it describes is
    # built, and comes through the front door from there.
    "BACKEND_CONTRACT": "kingfisher.infrastructure.harness.backend_contract",
    # One of the eleven, back. The rule those left under still holds -- a caller
    # means a caller outside this wheel -- and what changed is that every such
    # caller now has to name this one: `Kingfisher` no longer picks the filesystem
    # its agents run on, so a deployment keeping the one it always had writes
    # `backend=default_backend` and has to be able to say it without reaching past
    # the door for an infrastructure path.
    "default_backend": "kingfisher.infrastructure.harness.backend",
    # The seventh name a consumer has forced public, and the plainest: a
    # `CommandRunner` returns one of these, so a deployment writing a runner
    # cannot write one without it. `docs/guides/ports.md` documents the port and
    # the only way to satisfy it was `from kingfisher.domain.ports import
    # CommandResult` -- reaching past the front door, which the consumer rule
    # forbids. Found by the kit: the reference runner in
    # `test_root_and_runner_contracts` had to import it from somewhere.
    "CommandResult": "kingfisher.domain.ports",
    "UnknownSessionError": "kingfisher.domain.session",
    "Config": "kingfisher.config",
    "WorkspacePaths": "kingfisher.config",
    # What a `KINGFISHER_ADAPTERS_FACTORY` returns rows of, so a deployment adding
    # a wire format cannot write one without both.
    "Adapter": "kingfisher.config",
    "Landing": "kingfisher.config",
    "Kingfisher": "kingfisher.application.service",
    "ConfigError": "kingfisher.config",
    "Request": "kingfisher.domain.request",
    # The four a caller needs to answer a gated call: what it was asked, how
    # to answer, the answer itself, and the refusal for an answer that does
    # not fit. All public for the reason `CapabilityError` is -- a consumer
    # holding a paused session has to be able to branch on this.
    "Resume": "kingfisher.domain.request",
    "Decision": "kingfisher.domain.request",
    "DecisionError": "kingfisher.domain.request",
    "PendingDecision": "kingfisher.domain.result",
    "RunOn": "kingfisher.kinds.subagents.spec",
    "RunEvent": "kingfisher.domain.result",
    "RunResult": "kingfisher.domain.result",
    "SessionInfo": "kingfisher.domain.session",
    "definitions_source": "kingfisher.infrastructure.workspace",
    "ensure_layout": "kingfisher.infrastructure.workspace",
    "kinds_at": "kingfisher.infrastructure.workspace",
    "seed": "kingfisher.infrastructure.workspace",
    "Seeded": "kingfisher.infrastructure.workspace",
    "inventory": "kingfisher.application.inventory",
    "Inventory": "kingfisher.application.inventory",
    # Where this deployment reads from, as against what it offers. Public
    # because the answer was assembled three times and agreed nowhere: the
    # command printed four of eleven places, `doctor` printed one, and a
    # library caller had no way to ask at all.
    "Origin": "kingfisher.application.origins",
    "Origins": "kingfisher.application.origins",
    "config_from_env": "kingfisher.application.config",
    "paths_from_env": "kingfisher.application.config",
    "run": "kingfisher.application.run",
    "stream": "kingfisher.application.run",
}

__all__ = [
    "BACKEND_CONTRACT",
    "COMMAND_RUNNER_CONTRACT",
    "SESSION_ROOT_CONTRACT",
    "SESSION_STORE_CONTRACT",
    "UNSCOPED",
    "AccessError",
    "Adapter",
    "Capabilities",
    "CapabilityError",
    "CommandResult",
    "Config",
    "ConfigError",
    "Decision",
    "DecisionError",
    "Held",
    "HostPathError",
    "Inventory",
    "Kingfisher",
    "Landing",
    "LocalSessionStore",
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
    "SkillError",
    "SubagentError",
    "UnknownSessionError",
    "UnsafeReferenceError",
    "WorkspacePaths",
    "config_from_env",
    "default_backend",
    "definitions_source",
    "ensure_layout",
    "inventory",
    "kinds_at",
    "paths_from_env",
    "run",
    "seed",
    "stream",
]

if TYPE_CHECKING:
    # So type checkers and IDEs see the real symbols rather than `Any`.
    # Redundant aliases mark these as re-exports; `__all__` is computed, so a
    # checker cannot otherwise tell they are public.
    from kingfisher.application.config import config_from_env as config_from_env
    from kingfisher.application.config import paths_from_env as paths_from_env
    from kingfisher.application.inventory import Inventory as Inventory
    from kingfisher.application.inventory import inventory as inventory
    from kingfisher.application.origins import Origin as Origin
    from kingfisher.application.origins import Origins as Origins
    from kingfisher.application.run import run as run
    from kingfisher.application.run import stream as stream
    from kingfisher.application.service import Kingfisher as Kingfisher
    from kingfisher.config import Adapter as Adapter
    from kingfisher.config import Config as Config
    from kingfisher.config import ConfigError as ConfigError
    from kingfisher.config import Landing as Landing
    from kingfisher.config import WorkspacePaths as WorkspacePaths
    from kingfisher.domain.access import UNSCOPED as UNSCOPED
    from kingfisher.domain.access import AccessError as AccessError
    from kingfisher.domain.access import Held as Held
    from kingfisher.domain.capabilities import Capabilities as Capabilities
    from kingfisher.domain.capabilities import CapabilityError as CapabilityError
    from kingfisher.domain.ports import CommandResult as CommandResult
    from kingfisher.domain.references import UnsafeReferenceError as UnsafeReferenceError
    from kingfisher.domain.request import Decision as Decision
    from kingfisher.domain.request import DecisionError as DecisionError
    from kingfisher.domain.request import Request as Request
    from kingfisher.domain.request import Resume as Resume
    from kingfisher.domain.result import PendingDecision as PendingDecision
    from kingfisher.domain.result import RunEvent as RunEvent
    from kingfisher.domain.result import RunResult as RunResult
    from kingfisher.domain.session import QuotaExceededError as QuotaExceededError
    from kingfisher.domain.session import SessionBusyError as SessionBusyError
    from kingfisher.domain.session import SessionInfo as SessionInfo
    from kingfisher.domain.session import UnknownSessionError as UnknownSessionError
    from kingfisher.infrastructure.harness.backend import (
        default_backend as default_backend,
    )
    from kingfisher.infrastructure.harness.backend_contract import (
        BACKEND_CONTRACT as BACKEND_CONTRACT,
    )
    from kingfisher.infrastructure.harness.host_paths import HostPathError as HostPathError
    from kingfisher.infrastructure.session_store import (
        LocalSessionStore as LocalSessionStore,
    )
    from kingfisher.infrastructure.workspace import Seeded as Seeded
    from kingfisher.infrastructure.workspace import (
        definitions_source as definitions_source,
    )
    from kingfisher.infrastructure.workspace import ensure_layout as ensure_layout
    from kingfisher.infrastructure.workspace import kinds_at as kinds_at
    from kingfisher.infrastructure.workspace import seed as seed
    from kingfisher.kinds.skills.spec import SkillError as SkillError
    from kingfisher.kinds.subagents.spec import RunOn as RunOn
    from kingfisher.kinds.subagents.spec import SubagentError as SubagentError
    from kingfisher.testing import COMMAND_RUNNER_CONTRACT as COMMAND_RUNNER_CONTRACT
    from kingfisher.testing import SESSION_ROOT_CONTRACT as SESSION_ROOT_CONTRACT
    from kingfisher.testing import SESSION_STORE_CONTRACT as SESSION_STORE_CONTRACT


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
