"""Reading configuration out of the environment.

The `Config` record itself lives in `domain/`. What stays here is the part with
a foreign system on the other side of it — the process environment — and the
policy that goes with it.

`api_style` is required and has no default (Q25): the Anthropic-compatible and
OpenAI-compatible endpoints of the same gateway do not behave identically, so a
default would silently pick the wrong shape the first time kingfisher is pointed
somewhere new.

Which variables a style reads is not written here. `adapters.models.PROVIDERS`
holds it, alongside the builder that consumes it, so adding a provider is one
record rather than an edit in two files that must agree.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from kingfisher.adapters.models import PROVIDERS
from kingfisher.domain.config import API_STYLES, ROLES, Config, ConfigError

# Deliberately narrow: `Config` and friends are imported here to do the work,
# not re-exported. One blessed import path for the record — `domain.config` —
# is the whole point of it living there.
__all__ = ["enforce_local_only_tracing", "from_env"]


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

    provider = PROVIDERS.get(style)
    if provider is None:  # pragma: no cover -- bound to API_STYLES by test_models.py
        msg = f"no provider registered for api_style {style!r}"
        raise ConfigError(msg)

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
        base_url=_require(env, provider.url_env),
        api_key=_require(env, provider.key_env),
        model=_require(env, "KINGFISHER_MODEL"),
        max_tokens=_int(env, "KINGFISHER_MAX_TOKENS", 4096),
        timeout_s=_int(env, "KINGFISHER_TIMEOUT_S", 120),
        keep_runs=_int(env, "KINGFISHER_KEEP_RUNS", 20),
        recursion_limit=_int(env, "KINGFISHER_RECURSION_LIMIT", 150),
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
