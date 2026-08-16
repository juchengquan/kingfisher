"""Reading configuration out of the environment.

The `Config` record itself lives at the package root, belonging to no layer,
for the reasons its own docstring gives. What stays here is the part with a
foreign system on the other side of it — the process environment — and the
policy that goes with it.

Where prompts go is no longer read from here at all. `KINGFISHER_API_STYLE`,
`KINGFISHER_MODEL` and `KINGFISHER_MAX_TOKENS` are gone, replaced by
`models.yaml` -- one reviewed file naming every endpoint and every model, which
`infrastructure.model_catalogue` reads. What is left in the environment is the
workspace, one API key per endpoint, and operational flags.

The file is required and has no default, for the reason `api_style` was required
and had none: a default would silently pick a destination nobody chose the first
time kingfisher is pointed somewhere new.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from kingfisher.config import Config, ConfigError
from kingfisher.infrastructure import model_catalogue

# Deliberately narrow: `Config` and friends are imported here to do the work,
# not re-exported. One blessed import path for the record — `kingfisher.config`
# — is the whole point of it sitting where it does.
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


def _optional_int(environ: Mapping[str, str], key: str) -> int | None:
    """An integer, or `None` when the deployment did not set one.

    Distinct from `_int`: for a bound, "unset" is a meaningful value -- it
    means unbounded -- and is not the same as any number this could default to.
    """
    raw = (environ.get(key) or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        msg = f"{key} must be an integer, got {raw!r}"
        raise ConfigError(msg) from exc


def from_env(environ: Mapping[str, str] | None = None) -> Config:
    """Build a `Config` from environment variables.

    Required: `KINGFISHER_WORKSPACE`, a readable model catalogue, and the key
    named by whichever endpoint in it the default model runs on.
    """
    env = os.environ if environ is None else environ

    path_extra = tuple(
        part for part in (env.get("KINGFISHER_SHELL_PATH_EXTRA") or "").split(":") if part
    )

    def _optional_path(key: str) -> Path | None:
        raw = (env.get(key) or "").strip()
        return Path(raw).expanduser().resolve() if raw else None

    workspace = Path(_require(env, "KINGFISHER_WORKSPACE")).expanduser().resolve()
    # Defaults inside the workspace, like every other catalogue root, and
    # relocatable for the same reason: it holds content a person authored and
    # reviewed, so several deployments sharing one file is the point rather than
    # an accident. Unlike the others it is a file, not a directory -- endpoints
    # and models cross-reference, and splitting them across files would let half
    # a catalogue load.
    models_file = _optional_path("KINGFISHER_MODELS_FILE") or workspace / "models.yaml"
    endpoints, models, default_model = model_catalogue.load(models_file, env)

    return Config(
        workspace=workspace,
        models=models,
        endpoints=endpoints,
        default_model=default_model,
        models_file=models_file,
        execution_timeout_s=_int(env, "KINGFISHER_EXECUTION_TIMEOUT_S", 120),
        turn_timeout_s=_int(env, "KINGFISHER_TURN_TIMEOUT_S", 3600),
        session_max_bytes=_optional_int(env, "KINGFISHER_SESSION_MAX_BYTES"),
        session_ttl_s=_int(env, "KINGFISHER_SESSION_TTL_S", 7 * 24 * 3600),
        recursion_limit=_int(env, "KINGFISHER_RECURSION_LIMIT", 150),
        shell_path_extra=path_extra,
        shell_sandbox=env.get("KINGFISHER_SHELL_SANDBOX", "auto"),
        state_root=_optional_path("KINGFISHER_STATE_DIR"),
        scratch_root=_optional_path("KINGFISHER_SCRATCH_DIR"),
        skills_root=_optional_path("KINGFISHER_SKILLS_DIR"),
        subagents_root=_optional_path("KINGFISHER_SUBAGENTS_DIR"),
        tools_root=_optional_path("KINGFISHER_TOOLS_DIR"),
        skills_enabled=_bool(env, "KINGFISHER_SKILLS"),
        memory_enabled=_bool(env, "KINGFISHER_MEMORY"),
        interpreter_enabled=_bool(env, "KINGFISHER_INTERPRETER"),
        conversation_enabled=_bool(env, "KINGFISHER_CONVERSATION", default=True),
    )


def enforce_local_only_tracing() -> None:
    """Disable hosted tracing explicitly rather than relying on it being unset.

    A stray `LANGSMITH_TRACING` left over from another project would otherwise
    start exporting prompts and file contents off this machine (Q13).
    """
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
