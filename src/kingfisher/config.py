"""Deployment configuration. Belongs to no layer, which is why it sits here."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from kingfisher.domain.access import Groups

#: The frozen default for an `extra` mapping. Shared with `Adapter.extra`
#: rather than written twice: both spread into one `build_model` call and carry
#: one rule between them -- additive only, may not name a value the deployment
#: configured -- so a reader meeting one should find the other. Public for that
#: reason alone: an empty mapping cannot drift, so this buys visibility of the
#: pairing rather than safety.
NO_EXTRA: Mapping[str, Any] = MappingProxyType({})


@dataclass(frozen=True)
class Endpoint:
    """One place to send a model call, and the credentials for it."""

    api: str
    base_url: str
    api_key: str


@dataclass(frozen=True)
class ModelProfile:
    """One model this deployment can run, and how."""

    model: str
    endpoint: str
    max_tokens: int = 4096
    timeout_s: int = 120
    temperature: float | None = None
    top_p: float | None = None
    extra: Mapping[str, Any] = NO_EXTRA

    def kwargs(self) -> dict[str, Any]:
        """The model params to build with, omitting everything unset."""
        params: dict[str, Any] = {"max_tokens": self.max_tokens, "timeout": self.timeout_s}
        if self.temperature is not None:
            params["temperature"] = self.temperature
        if self.top_p is not None:
            params["top_p"] = self.top_p
        return params


def definition_roots_for(  # noqa: PLR0913, PLR0917 -- one per root, each
    # separately relocatable by its own environment variable
    workspace: Path,
    skills_root: Path | None = None,
    subagents_root: Path | None = None,
    tools_root: Path | None = None,
    agents_root: Path | None = None,
    middleware_root: Path | None = None,
) -> dict[str, Path]:
    """Each definition directory: an override, or a name in the workspace."""
    return {
        "agents": agents_root or workspace / "agents",
        "middleware": middleware_root or workspace / "middleware",
        "skills": skills_root or workspace / "skills",
        "subagents": subagents_root or workspace / "subagents",
        "tools": tools_root or workspace / "tools",
    }


def authored_files_for(
    workspace: Path,
    models_file: Path | None = None,
    groups_file: Path | None = None,
) -> dict[str, Path]:
    """The two files a deployment writes itself: an override, or a name in the
    workspace.
    """
    return {
        "models.yaml": models_file or workspace / "models.yaml",
        "groups.yaml": groups_file or workspace / "groups.yaml",
    }


@dataclass(frozen=True)
class WorkspacePaths:
    """Where a deployment keeps things, before anything has been read."""

    workspace: Path
    skills_root: Path | None = None
    subagents_root: Path | None = None
    tools_root: Path | None = None
    agents_root: Path | None = None
    #: The two single files, relocated. Not beside the four above because they are not
    #: directories and do not move together with them: one reviewed `models.yaml` shared
    #: across a fleet is the arrangement `compose.yaml` ships, and a group policy may
    #: sit somewhere else again.
    models_file: Path | None = None
    groups_file: Path | None = None
    #: Where definitions are *copied from*, which is the opposite direction to
    #: the four above — those say where a catalogue is read, this says what
    #: seeding hands it. Deliberately not beside them for that reason.
    #:
    #: `None` when the deployment named none, which is a normal state and not an
    #: error: a workspace seeded once runs for years without this being set.
    #: Only the act of seeding needs it, so only seeding refuses without it.
    assets: Path | None = None

    @property
    def catalogue_roots(self) -> dict[str, Path]:
        return definition_roots_for(
            self.workspace,
            self.skills_root,
            self.subagents_root,
            self.tools_root,
            self.agents_root,
        )

    @property
    def authored_files(self) -> dict[str, Path]:
        return authored_files_for(self.workspace, self.models_file, self.groups_file)


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Models:
    """What this deployment can run, where, and under which names."""

    #: Keyed by the id sent on the wire. **Closed**: a name absent from here is
    #: refused rather than passed to an endpoint that has never heard of it.
    models: Mapping[str, ModelProfile]
    #: Every endpoint whose credentials are actually present. One whose
    #: `key_env` is unset is dropped before it reaches here, with a warning, so
    #: one reviewed file works across a fleet holding different subsets of keys.
    endpoints: Mapping[str, Endpoint]
    #: What runs when nothing names another. Guaranteed to be a key of `models`
    #: -- `model_catalogue.load` refuses a file where it is not, which is what
    #: makes `resolve()` total.
    default: str
    #: Models this file defines that this machine cannot reach, and why -- keyed by
    #: model name, valued as the clause `resolve` drops into its refusal.
    unreachable: Mapping[str, str] = field(default_factory=dict)
    #: Where all of it was read from. Informational, so a refusal can name the
    #: file that should have defined what it could not find.
    source: Path | None = None

    def __post_init__(self) -> None:
        """Refuse a mapping whose key disagrees with the profile under it."""
        wrong = tuple(f"{key!r} holds {p.model!r}" for key, p in self.models.items()
                      if p.model != key)
        if wrong:
            msg = (
                f"models keyed by a name the profile does not carry: {', '.join(sorted(wrong))}. "
                f"The key is the id sent on the wire, so the two cannot differ"
            )
            raise ConfigError(msg)

    def resolve(self, name: str | None = None) -> tuple[ModelProfile, Endpoint]:
        """Which model to build, and where to send it. One question, one answer.

        Raises rather than falling back, on both halves. A name this deployment
        cannot run would otherwise reach an endpoint that has never heard of it -- a
        404 if you are lucky and a wrong-model run if you are not -- and a model
        whose endpoint went missing would otherwise run somewhere its author did not
        choose. Both decide where the prompt goes, which is the one thing worth being
        loud about here.
        """
        wanted = self.default if name is None else name
        profile = self.models.get(wanted)
        if profile is None:
            known = tuple(sorted(self.models))
            where = f" defined in {self.source}" if self.source else ""
            # Asked before "no such model", because for a model on an endpoint
            # with no key that answer is false and sends its reader to the wrong
            # file. The catalogue defines it; this machine cannot use it.
            # The branch below said this was how it would read, and until
            # `unreachable` existed it could not deliver: the model had already
            # been filtered out one step earlier, so the lookup failed here and
            # answered a question nobody asked.
            if reason := self.unreachable.get(wanted):
                msg = f"model {wanted!r} runs on {reason}; this deployment can run {known}"
                raise ConfigError(msg)
            msg = f"no model {wanted!r}{where}; this deployment can run {known}"
            raise ConfigError(msg)
        endpoint = self.endpoints.get(profile.endpoint)
        if endpoint is None:
            # A `Models` assembled by hand -- a fixture, a caller building one
            # directly -- whose profile names an endpoint the mapping beside it
            # does not have. `load` cannot produce this: it drops such models,
            # and the clause above is what speaks for them.
            msg = (
                f"model {wanted!r} runs on endpoint {profile.endpoint!r}, which this "
                f"deployment has no credentials for; it has {tuple(sorted(self.endpoints))}"
            )
            raise ConfigError(msg)
        return profile, endpoint


@dataclass(frozen=True)
class Config:
    """Everything kingfisher needs to build an agent for one workspace."""

    workspace: Path
    #: What this deployment can run, where, and under which names.
    #:
    #: One field where there were five -- `models`, `endpoints`, `default_model`,
    #: `aliases`, `models_file` -- plus the two methods that read only those. A
    #: `Config` is a flat bag a deployment fills in, and the cluster has
    #: invariants between its parts that siblings of `shell_sandbox` could not
    #: express. See `Models`.
    models: Models
    #: Which groups reach which agents, subagents and tools, or `None` where this
    #: deployment writes no policy.
    access: Groups | None = None
    #: Where the policy above was looked for, whether or not one was found.
    access_source: Path | None = None
    #: What bounds one shell command or one interpreter run.
    execution_timeout_s: int = 120
    # What one session may consume. Session-scoped because that is what
    # kingfisher can see: it is tenant-blind by design (T1), so bounding a
    # *caller* is the job of whatever knows who is calling. These protect the
    # process from one runaway session, not one caller from another.
    #
    # `recursion_limit` bounds graph steps and a model's own `timeout_s` bounds
    # a single call; nothing bounded their product, so a turn could hold a
    # process for 150 x 120s ~= 5 hours. An hour is far past any real turn -- a
    # 1,000-row analysis is ~20 turns of seconds each -- so this only ever fires
    # on the pathological case.
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
    # Where the definitions live. Also `None` for "derive from the workspace",
    # but for a different reason than the two above: these hold *content a
    # person authored*, and pointing several deployments at one directory is
    # how a reviewed catalogue serves all of them instead of each keeping a
    # copy nobody can audit centrally.
    skills_root: Path | None = None
    subagents_root: Path | None = None
    tools_root: Path | None = None
    agents_root: Path | None = None
    # Where definitions are copied *from*, which is the opposite direction to
    # the four above. Carried here as well as on `WorkspacePaths` because
    # `doctor` is handed a whole `Config` and has to report on it -- an unset,
    # mistyped or emptied source is the likeliest thing standing between an
    # install and a run once the definitions stop arriving with the wheel.
    assets: Path | None = None
    # Where a session's files are kept when the machine may not keep them. Unset means
    # the session directory is the only copy, which is right wherever the host is
    # allowed to hold data and is a silent disaster where it is not -- `doctor` says so
    # when the workspace turns out to be in memory.
    session_store: Path | None = None
    # The same port, named rather than built here: `module:name` for something callable
    # with no arguments that returns a `SessionStore`.
    session_store_factory: str | None = None
    # What this deployment *wires*. Distinct from `Capabilities`, which is what a single
    # request may *use* of it -- and the distinction is not stylistic: these two flags
    # shape `render_system_prompt`, which is the cached prefix every turn is compared
    # against. Varying them per request would trade a measured ~90% cache hit for a
    # per-caller prompt.
    skills_enabled: bool = False
    memory_enabled: bool = False
    # A JavaScript sandbox the agent can compute in: no filesystem, no network, capped
    # memory and time, and reachable tools limited to what the request granted. It is
    # the one execution surface `execute` can never be, which is why a deployment may
    # want both.
    interpreter_enabled: bool = False
    # Whether a turn remembers the one before it. On, because a session that forgets is
    # a surprising default for something that issues session ids.
    #
    # Worth turning off for a request/response API, where a caller sends one task and
    # reads one answer. Measured: with it on, a two-turn workspace carries a ~0.4MB
    # database it never reads back.
    conversation_enabled: bool = True

    def __post_init__(self) -> None:
        """Refuse a deployment that named its session store twice."""
        if self.session_store is not None and self.session_store_factory is not None:
            msg = (
                "session storage is configured twice: KINGFISHER_SESSION_STORE names "
                f"{str(self.session_store)!r} and KINGFISHER_SESSION_STORE_FACTORY names "
                f"{self.session_store_factory!r}. Set one -- the factory for a store that "
                "is not a directory on this host, the directory for one that is"
            )
            raise ConfigError(msg)

    @property
    def claim_stale_after(self) -> float:
        """How old a session's claim must be before another turn may take it.

        The two were the same number, so the claim became takeable the instant the
        run's deadline passed -- and a run stops *between stream chunks*, then still
        has to emit its result, collect what the turn left behind, and let go.
        Measured in that window: a second caller took the session while the first
        turn was still running, and the first turn then finished. Two turns in one
        session is what the claim exists to prevent, and what `SessionBusyError`
        records having seen once already, where "a turn simply vanished".
        """
        return self.turn_timeout_s + max(
            (profile.timeout_s for profile in self.models.models.values()),
            # A catalogue with no models cannot run a turn at all, so nothing
            # can be mid-chunk; the grace is the timeout a profile would have
            # defaulted to rather than zero, which would restore the overlap.
            default=ModelProfile.timeout_s,
        )

    @property
    def skills_dir(self) -> Path:
        """The skills catalogue. Shared by every session this workspace serves."""
        return self.skills_root or self.workspace / "skills"

    @property
    def catalogue_roots(self) -> dict[str, Path]:
        """The four definition directories, together, as one answer."""
        return definition_roots_for(
            self.workspace,
            self.skills_root,
            self.subagents_root,
            self.tools_root,
            self.agents_root,
        )

    @property
    def authored_files(self) -> dict[str, Path]:
        """Where `models.yaml` and `groups.yaml` are read from, found or not."""
        return authored_files_for(self.workspace, self.models.source, self.access_source)
