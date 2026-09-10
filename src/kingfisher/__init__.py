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
#: `build_agent`, `build_backend`, `build_model` and the rest -- were removed under.
#: Every one is live, and every one of their callers already imports it from the module
#: that defines it, so nothing had ever come through the front door for them. The
#: command ships in this distribution and is family: it reaches such a name directly,
#: and still comes through the front door for every name that *is* here. The service is
#: a distribution of its own and keeps the strict rule, because a promise is what it has
#: instead of a shared wheel.
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
    "UnknownReferenceError": "kingfisher.domain.references",
    "UnsafeReferenceError": "kingfisher.domain.references",
    "LocalFileStore": "kingfisher.infrastructure.workspace.files",
    # The sixth name a consumer has forced public. The service owns
    # `KINGFISHER_SERVICE_FILE_STORE_FACTORY` -- a `FileStore` resolves refs,
    # which is the vocabulary of a caller with no host paths, and the library's
    # own command has no use for one -- so the service is what turns that
    # setting into a store, and it takes `kingfisher` and nothing deeper. A
    # wrapper rather than the generic `wiring.store_named`, which would need
    # `FileStore` exported too for the caller to pass as `port=`.
    "file_store_named": "kingfisher.infrastructure.workspace.files",
    "LocalSessionStore": "kingfisher.infrastructure.session_store",
    # The port's contract, for a deployment checking its own adapter against it.
    # Public because it is the one thing in `testing` anybody outside this
    # repository is meant to import -- and public *here* rather than left to
    # `import kingfisher.testing`, because `READ_ELSEWHERE` says a name this
    # package publishes belongs in `__all__` and reaching for that table instead
    # is how something gets published without saying so.
    "SESSION_STORE_CONTRACT": "kingfisher.testing",
    # The same, for the other port a deployment can now name. `Planted` comes
    # with it and is not optional: `FileStore` has no verb for writing, so a
    # check cannot put the file it then reads there, and the deployment has to
    # hand over what it planted.
    "FILE_STORE_CONTRACT": "kingfisher.testing",
    "Planted": "kingfisher.testing",
    # The other two ports a deployment replaces. Both kits do more than read --
    # one creates directories, one runs commands -- which is a property of the
    # ports rather than of the kits: there is no way to check that a runner runs
    # things without running things.
    "SESSION_ROOT_CONTRACT": "kingfisher.testing",
    "COMMAND_RUNNER_CONTRACT": "kingfisher.testing",
    # The seventh name a consumer has forced public, and the plainest: a
    # `CommandRunner` returns one of these, so a deployment writing a runner
    # cannot write one without it. `docs/guides/ports.md` documents the port and
    # the only way to satisfy it was `from kingfisher.domain.ports import
    # CommandResult` -- reaching past the front door, which the consumer rule
    # forbids. Found by the kit: the reference runner in
    # `test_root_and_runner_contracts` had to import it from somewhere.
    "CommandResult": "kingfisher.domain.ports",
    "UnknownSessionError": "kingfisher.domain.session",
    "UploadError": "kingfisher.infrastructure.workspace.uploads",
    "Config": "kingfisher.config",
    "WorkspacePaths": "kingfisher.config",
    "Kingfisher": "kingfisher.application.service",
    "ConfigError": "kingfisher.config",
    "Request": "kingfisher.domain.request",
    "RunOn": "kingfisher.kinds.subagents.spec",
    "RunEvent": "kingfisher.domain.result",
    "RunResult": "kingfisher.domain.result",
    "SessionInfo": "kingfisher.domain.session",
    "definitions_source": "kingfisher.infrastructure.workspace.seeding",
    "ensure_layout": "kingfisher.infrastructure.workspace.layout",
    "kinds_at": "kingfisher.infrastructure.workspace.seeding",
    "seed": "kingfisher.infrastructure.workspace.seeding",
    "Seeded": "kingfisher.infrastructure.workspace.seeding",
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
    "COMMAND_RUNNER_CONTRACT",
    "FILE_STORE_CONTRACT",
    "SESSION_ROOT_CONTRACT",
    "SESSION_STORE_CONTRACT",
    "UNSCOPED",
    "AccessError",
    "Capabilities",
    "CapabilityError",
    "CommandResult",
    "Config",
    "ConfigError",
    "Held",
    "Inventory",
    "Kingfisher",
    "LocalFileStore",
    "LocalSessionStore",
    "Origin",
    "Origins",
    "Planted",
    "QuotaExceededError",
    "Request",
    "RunEvent",
    "RunOn",
    "RunResult",
    "Seeded",
    "SessionBusyError",
    "SessionInfo",
    "SkillError",
    "SubagentError",
    "UnknownReferenceError",
    "UnknownSessionError",
    "UnsafeReferenceError",
    "UploadError",
    "WorkspacePaths",
    "config_from_env",
    "definitions_source",
    "ensure_layout",
    "file_store_named",
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
    from kingfisher.config import Config as Config
    from kingfisher.config import ConfigError as ConfigError
    from kingfisher.config import WorkspacePaths as WorkspacePaths
    from kingfisher.domain.access import UNSCOPED as UNSCOPED
    from kingfisher.domain.access import AccessError as AccessError
    from kingfisher.domain.access import Held as Held
    from kingfisher.domain.capabilities import Capabilities as Capabilities
    from kingfisher.domain.capabilities import CapabilityError as CapabilityError
    from kingfisher.domain.ports import CommandResult as CommandResult
    from kingfisher.domain.references import (
        UnknownReferenceError as UnknownReferenceError,
    )
    from kingfisher.domain.references import UnsafeReferenceError as UnsafeReferenceError
    from kingfisher.domain.request import Request as Request
    from kingfisher.domain.result import RunEvent as RunEvent
    from kingfisher.domain.result import RunResult as RunResult
    from kingfisher.domain.session import QuotaExceededError as QuotaExceededError
    from kingfisher.domain.session import SessionBusyError as SessionBusyError
    from kingfisher.domain.session import SessionInfo as SessionInfo
    from kingfisher.domain.session import UnknownSessionError as UnknownSessionError
    from kingfisher.infrastructure.session_store import (
        LocalSessionStore as LocalSessionStore,
    )
    from kingfisher.infrastructure.workspace.files import LocalFileStore as LocalFileStore
    from kingfisher.infrastructure.workspace.files import (
        file_store_named as file_store_named,
    )
    from kingfisher.infrastructure.workspace.layout import ensure_layout as ensure_layout
    from kingfisher.infrastructure.workspace.seeding import Seeded as Seeded
    from kingfisher.infrastructure.workspace.seeding import (
        definitions_source as definitions_source,
    )
    from kingfisher.infrastructure.workspace.seeding import kinds_at as kinds_at
    from kingfisher.infrastructure.workspace.seeding import seed as seed
    from kingfisher.infrastructure.workspace.uploads import UploadError as UploadError
    from kingfisher.kinds.skills.spec import SkillError as SkillError
    from kingfisher.kinds.subagents.spec import RunOn as RunOn
    from kingfisher.kinds.subagents.spec import SubagentError as SubagentError
    from kingfisher.testing import COMMAND_RUNNER_CONTRACT as COMMAND_RUNNER_CONTRACT
    from kingfisher.testing import FILE_STORE_CONTRACT as FILE_STORE_CONTRACT
    from kingfisher.testing import SESSION_ROOT_CONTRACT as SESSION_ROOT_CONTRACT
    from kingfisher.testing import SESSION_STORE_CONTRACT as SESSION_STORE_CONTRACT
    from kingfisher.testing import Planted as Planted


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
