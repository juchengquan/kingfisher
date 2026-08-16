"""Deployment configuration. Belongs to no layer, which is why it sits here.

`Config` lived in `domain/` for a while, on the reasoning that both `application/` and
`infrastructure/` could then read it without depending on each other. That reasoning
is about import direction, not about modelling: no domain rule reads a `Config`,
and `base_url`, `api_key` and `timeout_s` are not kingfisher's vocabulary. It
was the innermost layer holding a record for the outer ones.

So it lives at the package root, above the layers and outside them. `application/` and
`infrastructure/` may read it; `domain/` may not, and a test enforces that -- a domain
rule that needs a value takes the value, not the record.

Reading it out of the environment is a separate job and stays in `application/config.py`:
the provider table that knows which variables to read lives in `infrastructure/`.

`ApiStyle` is the single source of truth for which endpoint styles exist.
`API_STYLES` is derived from it rather than written twice, so the runtime gate
and the static type cannot drift apart. The set of styles that can actually be
*constructed* is `infrastructure.models.PROVIDERS`; a test binds the two together.
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

@dataclass(frozen=True)
class Endpoint:
    """One place to send a model call, and the credentials for it.

    A *style* is an endpoint in this deployment's terms: `anthropic` names the
    gateway path, `openai` names OpenAI proper on the Responses API. They are
    not two dialects of one destination -- see `.env.example` -- so naming a
    style names where the traffic goes.
    """

    api_style: ApiStyle
    base_url: str
    api_key: str


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Config:
    """Everything kingfisher needs to build an agent for one workspace.

    Frozen but *not* hashable — `endpoints` is a real `dict`, so `hash(cfg)`
    raises. Nothing may key a cache on a `Config`; build the model once and let
    the composition root hold the instance.
    """

    workspace: Path
    api_style: ApiStyle
    base_url: str
    api_key: str
    model: str
    max_tokens: int = 4096
    timeout_s: int = 120
    # What one session may consume. Session-scoped because that is what
    # kingfisher can see: it is tenant-blind by design (T1), so bounding a
    # *caller* is the job of whatever knows who is calling. These protect the
    # process from one runaway session, not one caller from another.
    #
    # `recursion_limit` bounds graph steps and `timeout_s` bounds a single
    # model call or shell command; nothing bounded their product, so a turn
    # could hold a process for 150 x 120s ~= 5 hours. An hour is far past any
    # real turn -- a 1,000-row analysis is ~20 turns of seconds each -- so this
    # only ever fires on the pathological case.
    turn_timeout_s: int = 3600
    # Unset by default: workspaces vary by orders of magnitude, and refusing a
    # turn over a number nobody chose is worse than not bounding it. Checked
    # before a turn starts, never during -- `execute` writes without any file
    # tool seeing it, so there is nothing to intercept.
    session_max_bytes: int | None = None
    # How long an idle session survives, when a janitor calls `reap` without
    # saying. Age rather than a count of sessions kept: how long one has been
    # idle is a property of that session, where "newest twenty" compares every
    # caller's against each other.
    session_ttl_s: int = 7 * 24 * 3600
    # Each agent turn costs 2-3 graph steps, so this is roughly a turn budget
    # divided by three. 60 was sized against a toy task and cut a real
    # 1,000-row analysis off mid-step at 20 turns.
    recursion_limit: int = 150
    shell_path_extra: tuple[str, ...] = ()
    # Who keeps `execute` out of the rest of the host. `auto` uses whatever the
    # platform offers, `external` says a container already does it, `off` opts
    # out and is warned about on every start. On by default because an exposure
    # nobody opted into is one nobody knows they have: measured unconfined, the
    # shell could read this deployment's own API keys, `~/.aws` and the GitHub
    # CLI's token, and `http_fetch` is one tool call away from sending them.
    shell_sandbox: str = "auto"
    #: Every *other* style this deployment has credentials for. The one named
    #: by `api_style` is not in here -- it is the three fields above, which is
    #: what everything reads when nothing says otherwise. Populated by
    #: `from_env` from whichever `*_BASE_URL` / `*_API_KEY` pairs are set, so a
    #: deployment that filled in both already has two endpoints.
    endpoints: Mapping[str, Endpoint] = field(default_factory=dict)
    # Overrides for the two host-side roots. `None` means "derive from the
    # workspace", which is what keeps a workspace self-contained and copyable
    # by default. Read them through `state_dir` / `scratch_dir`, never directly.
    state_root: Path | None = None
    scratch_root: Path | None = None
    # Where the definitions live. Also `None` for "derive from the workspace",
    # but for a different reason than the two above: these hold *content a
    # person authored*, and pointing several deployments at one directory is
    # how a reviewed catalogue serves all of them instead of each keeping a
    # copy nobody can audit centrally.
    skills_root: Path | None = None
    subagents_root: Path | None = None
    tools_root: Path | None = None
    # What this deployment *wires*. Distinct from `Capabilities`, which is what
    # a single request may *use* of it -- and the distinction is not stylistic:
    # these two flags shape `render_system_prompt`, which is the cached prefix
    # every turn is compared against. Varying them per request would trade a
    # measured ~90% cache hit for a per-caller prompt.
    #
    # So: wiring is deployment-stable and lives here; narrowing is per-turn and
    # lives on the request. Narrowing may only subtract -- a request asking for
    # memory this deployment never wired does not get it.
    #
    # Off by default: a self-editing prompt makes runs non-reproducible, and
    # reproducibility is what the smoke task depends on.
    skills_enabled: bool = False
    memory_enabled: bool = False
    # A JavaScript sandbox the agent can compute in: no filesystem, no network,
    # capped memory and time, and reachable tools limited to what the request
    # granted. It is the one execution surface `execute` can never be, which is
    # why a deployment may want both.
    #
    # Dispatching subagents from inside it needs the async path -- `task()` in
    # the REPL awaits, and a sync saver raises partway through a workflow.
    #
    # Off by default. The sandbox ships with kingfisher rather than behind an
    # extra: the flag is already the gate, and a second one bought nothing but a
    # bare ModuleNotFoundError for anyone who set the flag without it. Importing
    # it is deferred to the point of use, so an install that never turns this on
    # pays nothing for carrying it.
    interpreter_enabled: bool = False

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
        `infrastructure.backend.prepare_scratch`: `/tmp` is world-writable, so the
        directory is created private and checked before use.
        """
        return self.scratch_root or self.state_dir / "tmp"

    @property
    def skills_dir(self) -> Path:
        """The skills catalogue. Shared by every session this workspace serves.

        Unlike `/data` and the rest, definitions are not owned by a session:
        they are authored, reviewed and deployed. Relocating them is safe for
        the same reason the state directory is — the agent reaches `/skills`
        through a route, and the shell has no business there.
        """
        return self.skills_root or self.workspace / "skills"

    @property
    def subagents_dir(self) -> Path:
        """The subagent catalogue. Read off disk, never addressed by the agent."""
        return self.subagents_root or self.workspace / "subagents"

    @property
    def default_endpoint(self) -> Endpoint:
        """Where a call goes when nothing names somewhere else."""
        return Endpoint(self.api_style, self.base_url, self.api_key)

    def endpoint_for(self, style: str | None) -> Endpoint:
        """The endpoint for `style`, or the default when `style` is `None`.

        Raises rather than falling back. A definition naming a style this
        deployment has no credentials for would otherwise run somewhere the
        author did not choose, which is the one failure mode worth being loud
        about here: it decides where the prompt goes.
        """
        if style is None or style == self.api_style:
            return self.default_endpoint
        endpoint = self.endpoints.get(style)
        if endpoint is None:
            configured = (self.api_style, *sorted(self.endpoints))
            msg = f"no endpoint configured for style {style!r}; this deployment has {configured}"
            raise ConfigError(msg)
        return endpoint

    @property
    def tools_dir(self) -> Path:
        """The tool catalogue: Python modules imported into this process.

        Deliberately not a backend route. A skill is data the agent reads and a
        tool is code this process runs, so the agent is given no path that
        reaches here — the only agent that could write a tool is one already
        holding `execute`, which can run anything on the host regardless.
        """
        return self.tools_root or self.workspace / "tools"

