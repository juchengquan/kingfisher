"""kingfisher — a personal, local, general-purpose agent built on deepagents.

    from kingfisher import run
    result = run("Profile /data/sales.csv and report what stands out.")
    print(result.answer, result.run_dir)
"""

from kingfisher.adapters.agent import build_agent, system_prompt
from kingfisher.adapters.backend import build_backend, shell_env
from kingfisher.adapters.checkpointing import build_checkpointer
from kingfisher.adapters.models import build_model
from kingfisher.app.config import from_env
from kingfisher.app.run import (
    Request,
    RunEvent,
    RunResult,
    normalize_answer,
    run,
    stream,
)
from kingfisher.domain.capabilities import Capabilities
from kingfisher.domain.config import Config, ConfigError
from kingfisher.domain.workspace import ensure_layout, protect_data, writable_data

__version__ = "0.1.0"

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
