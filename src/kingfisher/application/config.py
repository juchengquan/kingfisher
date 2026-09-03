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

from kingfisher.config import Config, ConfigError, WorkspacePaths
from kingfisher.infrastructure import access_policy, model_catalogue

# Deliberately narrow: `Config` and friends are imported here to do the work,
# not re-exported. One blessed import path for the record — `kingfisher.config`
# — is the whole point of it sitting where it does.
__all__ = ["config_from_env", "enforce_local_only_tracing"]


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


def paths_from_env(environ: Mapping[str, str] | None = None) -> WorkspacePaths:
    """Where this deployment keeps things, without reading the model catalogue.

    The catalogue lives *inside* the workspace, so a first run cannot load one:
    `config_from_env` raises `ConfigError` before the directory it would seed has been
    created. Seeding a fresh workspace therefore has to run on this much and no
    more -- and it has to be this rather than `KINGFISHER_WORKSPACE` read
    directly, because a deployment that relocated `KINGFISHER_SKILLS_DIR` would
    otherwise be seeded into the directory it stopped reading.

    `KINGFISHER_WORKSPACE` is still required, and still raises `ConfigError`
    when it is missing. That is the one thing no default can supply.
    """
    env = os.environ if environ is None else environ

    def _optional_path(key: str) -> Path | None:
        raw = (env.get(key) or "").strip()
        return Path(raw).expanduser().resolve() if raw else None

    return WorkspacePaths(
        workspace=Path(_require(env, "KINGFISHER_WORKSPACE")).expanduser().resolve(),
        skills_root=_optional_path("KINGFISHER_SKILLS_DIR"),
        subagents_root=_optional_path("KINGFISHER_SUBAGENTS_DIR"),
        tools_root=_optional_path("KINGFISHER_TOOLS_DIR"),
        agents_root=_optional_path("KINGFISHER_AGENTS_DIR"),
        # Read here rather than only in `config_from_env` for the reason the
        # four above are: laying a workspace out places the worked example for
        # each of these, and that happens before a catalogue can be read. A
        # deployment that relocated its catalogue and got the example in the
        # workspace has been handed an annotated file for a path nothing reads.
        models_file=_optional_path("KINGFISHER_MODELS_FILE"),
        groups_file=_optional_path("KINGFISHER_GROUPS_FILE"),
        # Read here rather than at the command that uses it, so that
        # `test_every_variable_read_is_documented` can see it: that rule finds
        # variables by scanning this module alone, and one read at a CLI edge
        # would go undocumented with nothing to notice.
        assets=_optional_path("KINGFISHER_ASSETS"),
    )


def config_from_env(environ: Mapping[str, str] | None = None) -> Config:
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

    paths = paths_from_env(env)
    workspace = paths.workspace
    # Where it defaults and why it relocates is `authored_files_for`, which
    # `paths_from_env` already answered: seeding places this file's worked
    # example and runs before any of this, so the two must not decide separately.
    #
    # One file rather than several, unlike the definition roots: endpoints and
    # models cross-reference, and splitting them would let half a catalogue load.
    models_file = paths.authored_files["models.yaml"]
    catalogue = model_catalogue.load(models_file, env)
    # The group vocabulary, and nothing else: who reaches what is written in
    # the definitions themselves. Defaults and relocates exactly as the
    # catalogue above does, through the same function.
    #
    # Optional, unlike the catalogue -- `load` answers `None` for a file that is
    # not there, which is the whole of what "this deployment controls nothing by
    # group" means. A file that is there and will not parse raises instead: a
    # vocabulary that cannot be read leaves every definition's audience
    # uncheckable, and coming up anyway is how a server serves everyone
    # everything.
    access_file = paths.authored_files["groups.yaml"]

    return Config(
        workspace=workspace,
        models=catalogue,
        access=access_policy.load(access_file),
        # The path as well as what was found there. `load` answers `None` for a
        # file that is not present, which is the ordinary case -- so without
        # this, the one thing worth reporting about a deployment that meant to
        # have a policy is unrecoverable.
        access_source=access_file,
        execution_timeout_s=_int(env, "KINGFISHER_EXECUTION_TIMEOUT_S", 120),
        turn_timeout_s=_int(env, "KINGFISHER_TURN_TIMEOUT_S", 3600),
        session_max_bytes=_optional_int(env, "KINGFISHER_SESSION_MAX_BYTES"),
        session_ttl_s=_int(env, "KINGFISHER_SESSION_TTL_S", 7 * 24 * 3600),
        recursion_limit=_int(env, "KINGFISHER_RECURSION_LIMIT", 150),
        shell_path_extra=path_extra,
        shell_sandbox=env.get("KINGFISHER_SHELL_SANDBOX", "auto"),
        state_root=_optional_path("KINGFISHER_STATE_DIR"),
        scratch_root=_optional_path("KINGFISHER_SCRATCH_DIR"),
        # From `paths`, not read again here: `paths_from_env` is the one reader
        # of these three, so a fresh workspace is seeded into the same
        # directories a configured one is served from.
        skills_root=paths.skills_root,
        subagents_root=paths.subagents_root,
        tools_root=paths.tools_root,
        agents_root=paths.agents_root,
        assets=paths.assets,
        session_store=_optional_path("KINGFISHER_SESSION_STORE"),
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
