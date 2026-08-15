"""kingfisher — a personal, local, general-purpose agent built on deepagents.

    from kingfisher import run
    result = run("Profile /data/sales.csv and report what stands out.")
    print(result.answer, result.run_dir)

Names resolve lazily. Importing anything from `kingfisher.adapters` pulls in
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
    "Config": "kingfisher.config",
    "ConfigError": "kingfisher.config",
    "Request": "kingfisher.domain.request",
    "RunEvent": "kingfisher.domain.result",
    "RunResult": "kingfisher.domain.result",
    "build_agent": "kingfisher.adapters.agent",
    "build_backend": "kingfisher.adapters.backend",
    "build_checkpointer": "kingfisher.adapters.checkpointing",
    "build_model": "kingfisher.adapters.models",
    "ensure_layout": "kingfisher.domain.workspace",
    "from_env": "kingfisher.app.config",
    "normalize_answer": "kingfisher.domain.result",
    "protect_data": "kingfisher.domain.workspace",
    "run": "kingfisher.app.run",
    "shell_env": "kingfisher.adapters.backend",
    "stream": "kingfisher.app.run",
    "system_prompt": "kingfisher.adapters.agent",
    "writable_data": "kingfisher.domain.workspace",
}

__all__ = [
    "Capabilities",
    "Config",
    "ConfigError",
    "Request",
    "RunEvent",
    "RunResult",
    "build_agent",
    "build_backend",
    "build_checkpointer",
    "build_model",
    "ensure_layout",
    "from_env",
    "normalize_answer",
    "protect_data",
    "run",
    "shell_env",
    "stream",
    "system_prompt",
    "writable_data",
]

if TYPE_CHECKING:
    # So type checkers and IDEs see the real symbols rather than `Any`.
    # Redundant aliases mark these as re-exports; `__all__` is computed, so a
    # checker cannot otherwise tell they are public.
    from kingfisher.adapters.agent import build_agent as build_agent
    from kingfisher.adapters.agent import system_prompt as system_prompt
    from kingfisher.adapters.backend import build_backend as build_backend
    from kingfisher.adapters.backend import shell_env as shell_env
    from kingfisher.adapters.checkpointing import build_checkpointer as build_checkpointer
    from kingfisher.adapters.models import build_model as build_model
    from kingfisher.app.config import from_env as from_env
    from kingfisher.app.run import run as run
    from kingfisher.app.run import stream as stream
    from kingfisher.config import Config as Config
    from kingfisher.config import ConfigError as ConfigError
    from kingfisher.domain.capabilities import Capabilities as Capabilities
    from kingfisher.domain.request import Request as Request
    from kingfisher.domain.result import RunEvent as RunEvent
    from kingfisher.domain.result import RunResult as RunResult
    from kingfisher.domain.result import normalize_answer as normalize_answer
    from kingfisher.domain.workspace import ensure_layout as ensure_layout
    from kingfisher.domain.workspace import protect_data as protect_data
    from kingfisher.domain.workspace import writable_data as writable_data


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
