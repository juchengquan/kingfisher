"""Reading configuration out of the environment."""

from __future__ import annotations

import os
import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from kingfisher.config import Config, ConfigError, WorkspacePaths
from kingfisher.infrastructure import access_policy, model_catalogue

# Deliberately narrow: `Config` and friends are imported here to do the work,
# not re-exported. One blessed import path for the record — `kingfisher.config`
# — is the whole point of it sitting where it does.
__all__ = ["config_from_env", "enforce_local_only_tracing"]

#: What each capability flag used to be called, new name to old. Read for the length of
#: a deprecation, warned about once per read, and then removed.
RENAMED = {
    "KINGFISHER_SKILLS_ENABLED": "KINGFISHER_SKILLS",
    "KINGFISHER_MEMORY_ENABLED": "KINGFISHER_MEMORY",
    "KINGFISHER_INTERPRETER_ENABLED": "KINGFISHER_INTERPRETER",
    "KINGFISHER_CONVERSATION_ENABLED": "KINGFISHER_CONVERSATION",
}


@dataclass(frozen=True)
class Environment:
    """One environment mapping, bound once, with the readings this format needs."""

    #: What was read. `os.environ` by default, a dict in every test.
    values: Mapping[str, str]

    @classmethod
    def current(cls, environ: Mapping[str, str] | None = None) -> Environment:
        """This process's environment, or the one a caller supplied."""
        return cls(os.environ if environ is None else environ)

    def require(self, key: str) -> str:
        value = (self.values.get(key) or "").strip()
        if not value:
            msg = f"{key} is required but not set"
            raise ConfigError(msg)
        return value

    def flag(self, key: str, default: bool = False) -> bool:
        raw = self._renamed(key).lower()
        if not raw:
            return default
        return raw in {"1", "true", "yes", "on"}

    def _renamed(self, key: str) -> str:
        """This setting's value, under its name or the one it used to have."""
        if value := (self.values.get(key) or "").strip():
            return value
        was = RENAMED.get(key)
        if was and (value := (self.values.get(was) or "").strip()):
            warnings.warn(
                f"{was} is the old name for {key} and is still read; rename it, "
                f"since the old one will stop being read.",
                DeprecationWarning,
                stacklevel=3,
            )
            return value
        return ""

    def number(self, key: str, default: int) -> int:
        raw = (self.values.get(key) or "").strip()
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError as exc:
            msg = f"{key} must be an integer, got {raw!r}"
            raise ConfigError(msg) from exc

    def optional_number(self, key: str) -> int | None:
        """An integer, or `None` when the deployment did not set one."""
        raw = (self.values.get(key) or "").strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError as exc:
            msg = f"{key} must be an integer, got {raw!r}"
            raise ConfigError(msg) from exc

    def optional_path(self, key: str) -> Path | None:
        """A path this deployment may or may not have relocated."""
        raw = (self.values.get(key) or "").strip()
        return Path(raw).expanduser().resolve() if raw else None

    def optional_text(self, key: str) -> str | None:
        """A string this deployment may or may not have set."""
        raw = (self.values.get(key) or "").strip()
        return raw or None

    def paths(self) -> WorkspacePaths:
        """Where this deployment keeps things, without reading the model catalogue."""
        return WorkspacePaths(
            workspace=Path(self.require("KINGFISHER_WORKSPACE")).expanduser().resolve(),
            skills_root=self.optional_path("KINGFISHER_SKILLS_DIR"),
            subagents_root=self.optional_path("KINGFISHER_SUBAGENTS_DIR"),
            tools_root=self.optional_path("KINGFISHER_TOOLS_DIR"),
            agents_root=self.optional_path("KINGFISHER_AGENTS_DIR"),
            # Read here rather than only in `config` below for the reason the
            # definition roots are: laying a workspace out places the worked example
            # for each of these, and that happens before a catalogue can be
            # read. A deployment that relocated its catalogue and got the
            # example in the workspace has been handed an annotated file for a
            # path nothing reads.
            models_file=self.optional_path("KINGFISHER_MODELS_FILE"),
            groups_file=self.optional_path("KINGFISHER_GROUPS_FILE"),
            # Read here rather than at the command that uses it, so that a rule
            # scanning this module can see it: one read at a CLI edge would go
            # undocumented with nothing to notice.
            assets=self.optional_path("KINGFISHER_ASSETS"),
        )

    def config(self) -> Config:
        """Build a `Config` from environment variables."""
        path_extra = tuple(
            part
            for part in (self.values.get("KINGFISHER_SHELL_PATH_EXTRA") or "").split(":")
            if part
        )
        paths = self.paths()
        # Where it defaults and why it relocates is `authored_files_for`, which
        # `paths` already answered: seeding places this file's worked example
        # and runs before any of this, so the two must not decide separately.
        #
        # One file rather than several, unlike the definition roots: endpoints
        # and models cross-reference, and splitting them would let half a
        # catalogue load.
        models_file = paths.authored_files["models.yaml"]
        # The group vocabulary, and nothing else: who reaches what is written in the
        # definitions themselves. Defaults and relocates exactly as the catalogue above
        # does, through the same function.
        access_file = paths.authored_files["groups.yaml"]

        return Config(
            workspace=paths.workspace,
            models=model_catalogue.load(models_file, self.values),
            access=access_policy.load(access_file),
            # The path as well as what was found there. `load` answers `None`
            # for a file that is not present, which is the ordinary case -- so
            # without this, the one thing worth reporting about a deployment
            # that meant to have a policy is unrecoverable.
            access_source=access_file,
            execution_timeout_s=self.number("KINGFISHER_EXECUTION_TIMEOUT_S", 120),
            turn_timeout_s=self.number("KINGFISHER_TURN_TIMEOUT_S", 3600),
            session_max_bytes=self.optional_number("KINGFISHER_SESSION_MAX_BYTES"),
            session_ttl_s=self.number("KINGFISHER_SESSION_TTL_S", 7 * 24 * 3600),
            recursion_limit=self.number("KINGFISHER_RECURSION_LIMIT", 150),
            shell_path_extra=path_extra,
            shell_sandbox=self.values.get("KINGFISHER_SHELL_SANDBOX", "auto"),
            # From `paths`, not read again here: it is the one reader of these
            # three, so a fresh workspace is seeded into the same directories a
            # configured one is served from.
            skills_root=paths.skills_root,
            subagents_root=paths.subagents_root,
            tools_root=paths.tools_root,
            agents_root=paths.agents_root,
            assets=paths.assets,
            session_store=self.optional_path("KINGFISHER_SESSION_STORE"),
            session_store_factory=self.optional_text("KINGFISHER_SESSION_STORE_FACTORY"),
            skills_enabled=self.flag("KINGFISHER_SKILLS_ENABLED"),
            memory_enabled=self.flag("KINGFISHER_MEMORY_ENABLED"),
            interpreter_enabled=self.flag("KINGFISHER_INTERPRETER_ENABLED"),
            conversation_enabled=self.flag("KINGFISHER_CONVERSATION_ENABLED", default=True),
        )


def paths_from_env(environ: Mapping[str, str] | None = None) -> WorkspacePaths:
    """`Environment.paths`, at the name callers already import."""
    return Environment.current(environ).paths()


def config_from_env(environ: Mapping[str, str] | None = None) -> Config:
    """`Environment.config`, at the name callers already import."""
    return Environment.current(environ).config()


def enforce_local_only_tracing() -> None:
    """Disable hosted tracing explicitly rather than relying on it being unset."""
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
