"""Configuration, in kingfisher's own vocabulary.

`Config` is the frozen record every layer reads, so it lives in `domain/` where
both `app/` and `adapters/` can depend on it without either depending on the
other. Reading it out of the environment is a separate job and stays in
`app/config.py`: the provider table that knows which variables to read lives in
`adapters/`, and `domain/` may not reach outward.

`ApiStyle` is the single source of truth for which endpoint styles exist.
`API_STYLES` is derived from it rather than written twice, so the runtime gate
and the static type cannot drift apart. The set of styles that can actually be
*constructed* is `adapters.models.PROVIDERS`; a test binds the two together.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, get_args

ApiStyle = Literal["anthropic", "openai"]

#: Derived from `ApiStyle`. Adding a style means editing the `Literal` and
#: adding a `Provider`; nothing else needs to learn the name.
API_STYLES: tuple[ApiStyle, ...] = get_args(ApiStyle)

ROLES: tuple[str, ...] = ("main", "subagent", "summarizer")


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Config:
    """Everything kingfisher needs to build an agent for one workspace.

    Frozen but *not* hashable — `role_models` is a real `dict`, so `hash(cfg)`
    raises. Nothing may key a cache on a `Config`; build per role instead and
    let the composition root hold the instances.
    """

    workspace: Path
    api_style: ApiStyle
    base_url: str
    api_key: str
    model: str
    max_tokens: int = 4096
    timeout_s: int = 120
    keep_runs: int = 20
    # Each agent turn costs 2-3 graph steps, so this is roughly a turn budget
    # divided by three. 60 was sized against a toy task and cut a real
    # 1,000-row analysis off mid-step at 20 turns.
    recursion_limit: int = 150
    shell_path_extra: tuple[str, ...] = ()
    role_models: Mapping[str, str] = field(default_factory=dict)
    # Overrides for the two host-side roots. `None` means "derive from the
    # workspace", which is what keeps a workspace self-contained and copyable
    # by default. Read them through `state_dir` / `scratch_dir`, never directly.
    state_root: Path | None = None
    scratch_root: Path | None = None
    # M2 capabilities. Off by default: a self-editing prompt makes runs
    # non-reproducible, and reproducibility is what the smoke task depends on.
    # Each flag gates both the middleware and its prompt section, so the agent
    # is never told about a capability it does not have.
    skills_enabled: bool = False
    memory_enabled: bool = False

    @property
    def state_dir(self) -> Path:
        """Where harness state lives: run logs and the thread database.

        Host-side only — the agent never addresses these by path, which is why
        they can be relocated at all. Anything the agent reaches with *both*
        the shell and a file tool has to stay under `workspace`: file tools are
        routed, `execute` is not, so a split root would silently give the two
        different views of the same name.
        """
        return self.state_root or self.workspace / ".kingfisher"

    @property
    def scratch_dir(self) -> Path:
        """Where the agent's shell puts temporary files (`TMPDIR`).

        Defaults inside the workspace so scratch is disposed of with it. Point
        it at `/tmp` for one fixed location per machine — but see
        `adapters.backend.prepare_scratch`: `/tmp` is world-writable, so the
        directory is created private and checked before use.
        """
        return self.scratch_root or self.state_dir / "tmp"

    def model_for(self, role: str) -> str:
        """Per-role model, falling back to the main model.

        The seam exists from day one so per-role cost routing is a config
        change rather than a refactor through every construction site.

        Returns a *string*. It must reach a model through `build_model`, never
        through deepagents — see `adapters/models.py`.
        """
        return self.role_models.get(role, self.model)
