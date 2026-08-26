"""kingfisher — a personal, local, general-purpose agent built on deepagents.

    from kingfisher import run
    result = run("Profile /data/sales.csv and report what stands out.")
    print(result.answer, result.run_dir)

Names resolve lazily. Importing anything from `kingfisher.infrastructure` pulls in
deepagents, which imports langchain-anthropic, langchain-openai *and*
langchain-google-genai at module level -- about 1.1s, most of it provider SDKs
this deployment will never call. Eager re-exports here made every consumer pay
that: `--help`, a config check, or a test that only touches `Request`.

The names and their spelling are unchanged; only the moment of import moved.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

__version__ = "0.1.0"

#: Public name -> the module that defines it. The single source for both
#: `__getattr__` and `__all__`, so the two cannot drift.
_EXPORTS = {
    "Capabilities": "kingfisher.domain.capabilities",
    "CapabilityError": "kingfisher.domain.capabilities",
    "QuotaExceededError": "kingfisher.domain.session",
    "SessionBusyError": "kingfisher.domain.session",
    "SkillError": "kingfisher.domain.skill",
    "SubagentError": "kingfisher.domain.subagent",
    "UnknownReferenceError": "kingfisher.domain.references",
    "UnsafeReferenceError": "kingfisher.domain.references",
    "LocalFileStore": "kingfisher.infrastructure.files",
    "LocalSessionStore": "kingfisher.infrastructure.session_store",
    "UnknownSessionError": "kingfisher.domain.session",
    "UploadError": "kingfisher.infrastructure.uploads",
    "async_checkpointer": "kingfisher.infrastructure.harness.checkpointing",
    "Config": "kingfisher.config",
    # The fourth name the consumer rule has forced public, and the first
    # that improved the library on its own: `resolve` takes six arguments
    # and two callers were assembling them from the same `Config`.
    "Confinement": "kingfisher.infrastructure.confinement",
    "bubblewrap_available": "kingfisher.infrastructure.bubblewrap",
    "landlock_abi": "kingfisher.infrastructure.confinement",
    "shell_confinement": "kingfisher.infrastructure.confinement",
    "WorkspacePaths": "kingfisher.config",
    "Kingfisher": "kingfisher.application.service",
    "ConfigError": "kingfisher.config",
    "Request": "kingfisher.domain.request",
    "RunOn": "kingfisher.domain.subagent",
    "RunEvent": "kingfisher.domain.result",
    "RunResult": "kingfisher.domain.result",
    "SessionInfo": "kingfisher.domain.session",
    "build_agent": "kingfisher.infrastructure.harness.agent",
    "build_backend": "kingfisher.infrastructure.harness.backend",
    "build_checkpointer": "kingfisher.infrastructure.harness.checkpointing",
    "build_model": "kingfisher.infrastructure.harness.models",
    "definitions_source": "kingfisher.infrastructure.seeding",
    "ensure_layout": "kingfisher.infrastructure.workspace_fs",
    "kinds_at": "kingfisher.infrastructure.seeding",
    "memory_backing": "kingfisher.infrastructure.workspace_fs",
    "seed": "kingfisher.infrastructure.seeding",
    "Seeding": "kingfisher.infrastructure.seeding",
    "inventory": "kingfisher.application.inventory",
    # The fifth name a consumer has forced public. A purpose-built answer
    # rather than `model_for` itself, which the design named: `doctor` wants
    # *which definitions cannot run*, and exporting the two calls it would
    # need to combine would promise a recipe instead of an answer.
    "unrunnable_delegates": "kingfisher.infrastructure.harness.agent",
    # Reached for by `kingfisher.presentation.cli`, and public because it
    # reached. A
    # renderer in the domain looks odd until you see what it is for: the
    # block a *refusal* prints is the block a listing prints, so a name two
    # files define reads the same in both. A consumer rendering its own
    # would be the drift that rule exists to stop.
    "offered": "kingfisher.domain.tool",
    # The third name a consumer turned out to need, and it arrived the same
    # way: two folders may each define a `surveyor`, so a listing has to tell
    # a bare name from a `where::what` reference before deciding whether to
    # print the file it came from.
    "split_reference": "kingfisher.domain.tool",
    # Said once "so callers can quote it without knowing the filename
    # themselves", by its own comment. It was `skill_store.LAYOUT` with one
    # caller; renamed because a bare `LAYOUT` at the top level sits next to
    # `LAYOUT_DIRS` and means something else.
    "DEFINITION_KINDS": "kingfisher.infrastructure.catalogue",
    "SEED_HINT": "kingfisher.infrastructure.seeding",
    "SKILL_LAYOUT": "kingfisher.infrastructure.catalogue.skills",
    "Inventory": "kingfisher.application.inventory",
    "from_env": "kingfisher.application.config",
    "paths_from_env": "kingfisher.application.config",
    "normalize_answer": "kingfisher.domain.result",
    "protect_data": "kingfisher.infrastructure.workspace_fs",
    "run": "kingfisher.application.run",
    "shell_env": "kingfisher.infrastructure.harness.backend",
    "stream": "kingfisher.application.run",
    "system_prompt": "kingfisher.infrastructure.prompting",
    "writable_data": "kingfisher.infrastructure.workspace_fs",
}

__all__ = [
    "DEFINITION_KINDS",
    "SEED_HINT",
    "SKILL_LAYOUT",
    "Capabilities",
    "CapabilityError",
    "Config",
    "ConfigError",
    "Confinement",
    "Inventory",
    "Kingfisher",
    "LocalFileStore",
    "LocalSessionStore",
    "QuotaExceededError",
    "Request",
    "RunEvent",
    "RunOn",
    "RunResult",
    "Seeding",
    "SessionBusyError",
    "SessionInfo",
    "SkillError",
    "SubagentError",
    "UnknownReferenceError",
    "UnknownSessionError",
    "UnsafeReferenceError",
    "UploadError",
    "WorkspacePaths",
    "async_checkpointer",
    "bubblewrap_available",
    "build_agent",
    "build_backend",
    "build_checkpointer",
    "build_model",
    "definitions_source",
    "ensure_layout",
    "from_env",
    "inventory",
    "kinds_at",
    "landlock_abi",
    "memory_backing",
    "normalize_answer",
    "offered",
    "paths_from_env",
    "protect_data",
    "run",
    "seed",
    "shell_confinement",
    "shell_env",
    "split_reference",
    "stream",
    "system_prompt",
    "unrunnable_delegates",
    "writable_data",
]

if TYPE_CHECKING:
    # So type checkers and IDEs see the real symbols rather than `Any`.
    # Redundant aliases mark these as re-exports; `__all__` is computed, so a
    # checker cannot otherwise tell they are public.
    from kingfisher.application.config import from_env as from_env
    from kingfisher.application.config import paths_from_env as paths_from_env
    from kingfisher.application.inventory import Inventory as Inventory
    from kingfisher.application.inventory import inventory as inventory
    from kingfisher.application.run import run as run
    from kingfisher.application.run import stream as stream
    from kingfisher.application.service import Kingfisher as Kingfisher
    from kingfisher.config import Config as Config
    from kingfisher.config import ConfigError as ConfigError
    from kingfisher.config import WorkspacePaths as WorkspacePaths
    from kingfisher.domain.capabilities import Capabilities as Capabilities
    from kingfisher.domain.capabilities import CapabilityError as CapabilityError
    from kingfisher.domain.references import (
        UnknownReferenceError as UnknownReferenceError,
    )
    from kingfisher.domain.references import UnsafeReferenceError as UnsafeReferenceError
    from kingfisher.domain.request import Request as Request
    from kingfisher.domain.result import RunEvent as RunEvent
    from kingfisher.domain.result import RunResult as RunResult
    from kingfisher.domain.result import normalize_answer as normalize_answer
    from kingfisher.domain.session import QuotaExceededError as QuotaExceededError
    from kingfisher.domain.session import SessionBusyError as SessionBusyError
    from kingfisher.domain.session import UnknownSessionError as UnknownSessionError
    from kingfisher.domain.skill import SkillError as SkillError
    from kingfisher.domain.subagent import RunOn as RunOn
    from kingfisher.domain.subagent import SubagentError as SubagentError
    from kingfisher.domain.tool import offered as offered
    from kingfisher.domain.tool import split_reference as split_reference
    from kingfisher.infrastructure.bubblewrap import (
        bubblewrap_available as bubblewrap_available,
    )
    from kingfisher.infrastructure.catalogue import DEFINITION_KINDS as DEFINITION_KINDS
    from kingfisher.infrastructure.catalogue.skills import SKILL_LAYOUT as SKILL_LAYOUT
    from kingfisher.infrastructure.confinement import Confinement as Confinement
    from kingfisher.infrastructure.confinement import landlock_abi as landlock_abi
    from kingfisher.infrastructure.confinement import (
        shell_confinement as shell_confinement,
    )
    from kingfisher.infrastructure.files import LocalFileStore as LocalFileStore
    from kingfisher.infrastructure.harness.agent import build_agent as build_agent
    from kingfisher.infrastructure.harness.agent import (
        unrunnable_delegates as unrunnable_delegates,
    )
    from kingfisher.infrastructure.harness.backend import build_backend as build_backend
    from kingfisher.infrastructure.harness.backend import shell_env as shell_env
    from kingfisher.infrastructure.harness.checkpointing import (
        async_checkpointer as async_checkpointer,
    )
    from kingfisher.infrastructure.harness.checkpointing import (
        build_checkpointer as build_checkpointer,
    )
    from kingfisher.infrastructure.harness.models import build_model as build_model
    from kingfisher.infrastructure.prompting import system_prompt as system_prompt
    from kingfisher.infrastructure.seeding import SEED_HINT as SEED_HINT
    from kingfisher.infrastructure.seeding import Seeding as Seeding
    from kingfisher.infrastructure.seeding import (
        definitions_source as definitions_source,
    )
    from kingfisher.infrastructure.seeding import kinds_at as kinds_at
    from kingfisher.infrastructure.seeding import seed as seed
    from kingfisher.infrastructure.session_store import (
        LocalSessionStore as LocalSessionStore,
    )
    from kingfisher.infrastructure.uploads import UploadError as UploadError
    from kingfisher.infrastructure.workspace_fs import ensure_layout as ensure_layout
    from kingfisher.infrastructure.workspace_fs import memory_backing as memory_backing
    from kingfisher.infrastructure.workspace_fs import protect_data as protect_data
    from kingfisher.infrastructure.workspace_fs import writable_data as writable_data


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
