"""Reading configuration out of the environment."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from kingfisher.core.access import load as load_access_policy

from . import model_catalogue
from .config import (
    DEFAULT_THREAD_POOL_SIZE,
    FOLDED_SETTINGS,
    REMOVED_SETTINGS,
    RENAMED_SETTINGS,
    Config,
    ConfigError,
    WorkspacePaths,
)

# `Config` and the rest are imported to use, not re-exported: their one import path is
# `kingfisher.core.config`.
__all__ = ["config_from_env", "enforce_local_only_tracing"]


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
        """One capability switch, under the only name that means it.

        The names these replaced are in `health.RETIRED`, never here: a rule reads this
        module's text for the variables it reads, so spelling a dead one makes it look alive.
        """
        raw = (self.values.get(key) or "").strip().lower()
        if not raw:
            return default
        return raw in {"1", "true", "yes", "on"}

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

    def mounts(self, key: str) -> dict[str, Path]:
        """`label=path` pairs separated by `:`, the separator `PATH` already uses."""
        found: dict[str, Path] = {}
        for part in (self.values.get(key) or "").split(":"):
            if not part.strip():
                continue
            label, equals, raw = part.partition("=")
            label, raw = label.strip(), raw.strip()
            if not equals or not label or not raw:
                msg = f"{key} takes label=path pairs separated by ':', got {part!r}"
                raise ConfigError(msg)
            # Refused rather than letting the later win: the earlier would vanish unreported.
            if label in found:
                msg = f"{key} names {label!r} twice"
                raise ConfigError(msg)
            found[label] = Path(raw).expanduser().resolve()
        return found

    def optional_text(self, key: str) -> str | None:
        """A string this deployment may or may not have set."""
        raw = (self.values.get(key) or "").strip()
        return raw or None

    def paths(self) -> WorkspacePaths:
        """Where this deployment keeps things, without reading the model catalogue.

        The workspace is optional here, for seeding, which writes only into the
        definitions folder.
        """
        # Here rather than in `config`, so that seeding, which copied into these, refuses
        # them too.
        for old, now in FOLDED_SETTINGS.items():
            if self.optional_text(old):
                msg = (
                    f"{old} was removed: {now} is read from KINGFISHER_DEFINITIONS_DIR "
                    f"now, as {now} inside it. Move it there and unset {old}"
                )
                raise ConfigError(msg)
        return WorkspacePaths(
            definitions=Path(self.require("KINGFISHER_DEFINITIONS_DIR")).expanduser().resolve(),
            workspace=self.optional_path("KINGFISHER_WORKSPACE"),
            skills_mounts=self.mounts("KINGFISHER_SKILLS_MOUNTS"),
            # Here, not at the command that uses it, so the rule scanning this module
            # sees it and holds it to being documented.
            assets=self.optional_path("KINGFISHER_ASSETS"),
        )

    def config(self) -> Config:
        """Build a `Config` from environment variables."""
        for removed, instead in REMOVED_SETTINGS.items():
            if self.optional_text(removed):
                msg = f"{removed} was removed: {instead}"
                raise ConfigError(msg)
        for old, new in RENAMED_SETTINGS.items():
            if self.optional_text(old):
                msg = f"{old} was renamed {new}; set that instead"
                raise ConfigError(msg)
        paths = self.paths()
        # `require` only ever raises here, in the words every required setting gets.
        workspace = paths.workspace or Path(self.require("KINGFISHER_WORKSPACE"))
        # Before the catalogue is read, which would say only that `models.yaml` is
        # missing. Never created here: several deployments may read the folder, and
        # making it would hide a failed mount behind an empty one.
        if not paths.definitions.is_dir():
            msg = (
                f"KINGFISHER_DEFINITIONS_DIR is {paths.definitions}, which is not a "
                "directory. `kingfisher seed` makes one and fills it"
            )
            raise ConfigError(msg)
        # One file, unlike the definition roots: endpoints and models cross-reference,
        # and a split would let half a catalogue load.
        models_file = paths.authored_files["models.yaml"]
        # The source-id vocabulary only: who reaches what is in the definitions.
        access_file = paths.authored_files["source_ids.yaml"]

        return Config(
            workspace=workspace,
            definitions=paths.definitions,
            # Wire formats resolved here, not handed to `Kingfisher`: `api` is checked as
            # the file loads, and `doctor` and `run` build their own with nowhere to pass one.
            models=model_catalogue.load(
                models_file,
                self.values,
                model_catalogue.wire_formats(self.optional_text("KINGFISHER_WIRE_FORMATS")),
                keys_file=self.optional_path("KINGFISHER_MODEL_KEYS_FILE"),
            ),
            access=load_access_policy(access_file),
            # The path too: `load` answers `None` for a missing file, and a deployment
            # that meant to have a policy needs to hear where it was looked for.
            access_source=access_file,
            execution_timeout_s=self.number("KINGFISHER_EXECUTION_TIMEOUT_S", 120),
            turn_timeout_s=self.number("KINGFISHER_TURN_TIMEOUT_S", 3600),
            session_max_bytes=self.optional_number("KINGFISHER_SESSION_MAX_BYTES"),
            session_ttl_s=self.number("KINGFISHER_SESSION_TTL_S", 7 * 24 * 3600),
            recursion_limit=self.number("KINGFISHER_RECURSION_LIMIT", 150),
            thread_pool_size=self.number("KINGFISHER_THREAD_POOL_SIZE", DEFAULT_THREAD_POOL_SIZE),
            skills_mounts=paths.skills_mounts,
            assets=paths.assets,
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
