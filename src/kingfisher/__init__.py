"""kingfisher — a personal, local, general-purpose agent built on deepagents.

    from kingfisher import run
    result = run("Profile /data/sales.csv and report what stands out.")
    print(result.answer, result.run_dir)
"""

from kingfisher.agent import build_agent, system_prompt
from kingfisher.backend import build_backend, shell_env
from kingfisher.checkpointing import build_checkpointer
from kingfisher.config import Config, ConfigError, from_env
from kingfisher.models import build_model
from kingfisher.run import RunEvent, RunResult, normalize_answer, run, stream
from kingfisher.workspace import ensure_layout, protect_data, writable_data

__version__ = "0.1.0"

__all__ = [
    "Config",
    "ConfigError",
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
