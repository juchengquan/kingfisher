"""Configuration, resolved from the environment.

`api_style` is required and has no default (Q25): the Anthropic-compatible and
OpenAI-compatible endpoints of the same gateway do not behave identically, so a
default would silently pick the wrong shape the first time kingfisher is pointed
somewhere new.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

ApiStyle = Literal["anthropic", "openai"]

API_STYLES: tuple[str, ...] = ("anthropic", "openai")
ROLES: tuple[str, ...] = ("main", "subagent", "summarizer")

_CREDENTIALS_BY_STYLE: Mapping[str, tuple[str, str]] = {
    "anthropic": ("ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY"),
    "openai": ("OPENAI_BASE_URL", "OPENAI_API_KEY"),
}


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Config:
    """Everything kingfisher needs to build an agent for one workspace."""

    workspace: Path
    api_style: ApiStyle
    base_url: str
    api_key: str
    model: str
    max_tokens: int = 4096
    timeout_s: int = 120
    keep_runs: int = 20
    recursion_limit: int = 60
    shell_path_extra: tuple[str, ...] = ()
    role_models: Mapping[str, str] = field(default_factory=dict)
    # M2 capabilities. Off by default: a self-editing prompt makes runs
    # non-reproducible, and reproducibility is what the smoke task depends on.
    # Each flag gates both the middleware and its prompt section, so the agent
    # is never told about a capability it does not have.
    skills_enabled: bool = False
    memory_enabled: bool = False

    def model_for(self, role: str) -> str:
        """Per-role model, falling back to the main model.

        The seam exists from day one so per-role cost routing is a config
        change rather than a refactor through every construction site.
        """
        return self.role_models.get(role, self.model)


def _require(environ: Mapping[str, str], key: str) -> str:
    value = (environ.get(key) or "").strip()
    if not value:
        msg = f"{key} is required but not set"
        raise ConfigError(msg)
    return value


def _bool(environ: Mapping[str, str], key: str, default: bool = False) -> bool:
    raw = (environ.get(key) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _int(environ: Mapping[str, str], key: str, default: int) -> int:
    raw = (environ.get(key) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        msg = f"{key} must be an integer, got {raw!r}"
        raise ConfigError(msg) from exc


def from_env(environ: Mapping[str, str] | None = None) -> Config:
    """Build a `Config` from environment variables.

    Required: `KINGFISHER_WORKSPACE`, `KINGFISHER_API_STYLE`, `KINGFISHER_MODEL`,
    plus the base URL and key for the chosen style.
    """
    env = os.environ if environ is None else environ

    style = _require(env, "KINGFISHER_API_STYLE").lower()
    if style not in API_STYLES:
        msg = f"KINGFISHER_API_STYLE must be one of {API_STYLES}, got {style!r}"
        raise ConfigError(msg)

    url_key, api_key_key = _CREDENTIALS_BY_STYLE[style]

    role_models = {
        role: value
        for role in ROLES
        if (value := (env.get(f"KINGFISHER_MODEL_{role.upper()}") or "").strip())
    }

    path_extra = tuple(
        part for part in (env.get("KINGFISHER_SHELL_PATH_EXTRA") or "").split(":") if part
    )

    return Config(
        workspace=Path(_require(env, "KINGFISHER_WORKSPACE")).expanduser().resolve(),
        api_style=style,  # type: ignore[arg-type]
        base_url=_require(env, url_key),
        api_key=_require(env, api_key_key),
        model=_require(env, "KINGFISHER_MODEL"),
        max_tokens=_int(env, "KINGFISHER_MAX_TOKENS", 4096),
        timeout_s=_int(env, "KINGFISHER_TIMEOUT_S", 120),
        keep_runs=_int(env, "KINGFISHER_KEEP_RUNS", 20),
        recursion_limit=_int(env, "KINGFISHER_RECURSION_LIMIT", 60),
        shell_path_extra=path_extra,
        role_models=role_models,
        skills_enabled=_bool(env, "KINGFISHER_SKILLS"),
        memory_enabled=_bool(env, "KINGFISHER_MEMORY"),
    )


def enforce_local_only_tracing() -> None:
    """Disable hosted tracing explicitly rather than relying on it being unset.

    A stray `LANGSMITH_TRACING` left over from another project would otherwise
    start exporting prompts and file contents off this machine (Q13).
    """
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
