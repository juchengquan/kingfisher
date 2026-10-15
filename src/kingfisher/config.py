"""Deployment configuration. Belongs to no layer, which is why it sits here."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from types import MappingProxyType
from typing import Any

from kingfisher.domain.access import SourceIds
from kingfisher.layout import DEFAULT_LAYOUT, Layout

#: The frozen default for an `extra` mapping. Shared with `Adapter.extra`
#: rather than written twice: both spread into one `build_model` call and carry
#: one rule between them -- additive only, may not name a value the deployment
#: configured -- so a reader meeting one should find the other. Public for that
#: reason alone: an empty mapping cannot drift, so this buys visibility of the
#: pairing rather than safety.
NO_EXTRA: Mapping[str, Any] = MappingProxyType({})

#: A deployment's skills mounts when it names none: frozen, for `NO_EXTRA`'s reason.
NO_MOUNTS: Mapping[str, Path] = MappingProxyType({})


@dataclass(frozen=True)
class Landing:
    """The attribute a chat class keeps each value in, once constructed.

    The classes agree on the keyword and disagree on the attribute: `base_url` is
    `anthropic_api_url` on one and `openai_api_base` on the other. `build_model` reads
    every value back through these after construction.

    `temperature` and `top_p` are absent deliberately: a class may drop them on purpose
    -- `ChatOpenAI` discards `temperature` for `gpt-5`, which rejects it -- and
    checking them would refuse a model the vendor's own client builds correctly.
    """

    model: str
    base_url: str
    api_key: str
    max_tokens: str
    timeout: str


@dataclass(frozen=True)
class Adapter:
    """One wire format: which class speaks it, and what it needs to be told.

    kingfisher targets **gateway-shaped** endpoints — one base URL, one key. A wire
    format without that shape (Bedrock wants a region and a credentials profile) is a
    new field on `Endpoint`, not a new row.
    """

    chat_class: str
    lands: Landing
    extra: Mapping[str, Any] = NO_EXTRA

    def resolve(self) -> Any:
        """Import the chat class this row names."""
        module_name, _, class_name = self.chat_class.partition(":")
        return getattr(import_module(module_name), class_name)


@dataclass(frozen=True)
class Endpoint:
    """One place to send a model call, and the credentials for it."""

    api: str
    base_url: str
    api_key: str
    #: The variable the key above was read from. Kept rather than discarded at
    #: load time because it is the only actionable thing in a 401: the YAML is
    #: correct, the key it points at is not, and a message that cannot name the
    #: variable sends someone to the file where everything already looks right.
    #: Defaulted, so an endpoint built in a test need not carry one.
    key_env: str = ""
    #: The keys file entry the key was read from, for an endpoint whose `models.yaml`
    #: says `key:` -- then `key_env` is empty. Kept for the 401's reason above: the
    #: entry is the thing to check, and a message about variables would send the
    #: reader to the environment, which held nothing.
    key_entry: str = ""
    #: The row `api` named, resolved as the catalogue loaded. Carried rather than
    #: looked up again by name when a model is built, because a deployment's own
    #: rows exist only in the table that load was handed.
    adapter: Adapter = field(kw_only=True)


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


def definition_roots_for(definitions: Path) -> dict[str, Path]:
    """Each definition directory, by the name it has in the definitions folder."""
    return {
        "agents": definitions / "agents",
        "middlewares": definitions / "middlewares",
        "skills": definitions / "skills",
        "subagents": definitions / "subagents",
        "tools": definitions / "tools",
    }


def authored_files_for(definitions: Path) -> dict[str, Path]:
    """The two files a deployment writes itself, beside its definitions."""
    return {
        "models.yaml": definitions / "models.yaml",
        "source_ids.yaml": definitions / "source_ids.yaml",
    }


def refuse_overlap(workspace: Path, definitions: Path) -> None:
    """Refuse a workspace and a definitions folder that are one, or one inside the other.

    Either way round loses what the split is for. Sessions inside the definitions put
    runtime state in the folder several deployments read and somebody reviews; the
    definitions inside the workspace are in the one place the macOS fence lets every
    session's shell read.
    """
    workspace, definitions = Path(workspace).resolve(), Path(definitions).resolve()
    if workspace.is_relative_to(definitions) or definitions.is_relative_to(workspace):
        msg = (
            f"KINGFISHER_WORKSPACE is {workspace} and KINGFISHER_DEFINITIONS_DIR is "
            f"{definitions}; neither may be inside the other. The workspace holds what "
            "sessions make and the definitions folder what people write, so give each "
            "a folder of its own"
        )
        raise ConfigError(msg)


@dataclass(frozen=True)
class WorkspacePaths:
    """Where a deployment keeps things, before anything has been read."""

    #: Where the definitions are read from, which is the one thing seeding needs.
    definitions: Path
    #: `None` where the deployment named none, which only seeding may do: it writes
    #: into the definitions folder and never into a workspace.
    workspace: Path | None = None
    #: Skills directories beside the catalogue, each mounted at `/skills/<label>/`.
    #: Not a definition root: a mount is somebody else's directory, so nothing
    #: creates it and nothing seeds into it.
    skills_mounts: Mapping[str, Path] = NO_MOUNTS
    #: Where definitions are *copied from*, which is the opposite direction to the
    #: definition roots — those say where a catalogue is read, this says what
    #: seeding hands it. Deliberately not beside them for that reason.
    #:
    #: `None` when the deployment named none, which is a normal state and not an
    #: error: a workspace seeded once runs for years without this being set.
    #: Only the act of seeding needs it, so only seeding refuses without it.
    assets: Path | None = None

    def __post_init__(self) -> None:
        if self.workspace is not None:
            refuse_overlap(self.workspace, self.definitions)

    @property
    def catalogue_roots(self) -> dict[str, Path]:
        return definition_roots_for(self.definitions)

    @property
    def authored_files(self) -> dict[str, Path]:
        return authored_files_for(self.definitions)


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


#: What configured the `SessionStore`, which went when a session's backend became
#: where it is kept. Refused where the environment is read rather than ignored, and
#: kept here rather than beside that reader because they are refusals, not knobs.
#: The size of the process's thread pool when nothing configured one. A turn hands
#: it only short work on this host -- building the agent, writing a pause, closing up
#: -- but session backends whose async methods are the protocol's defaults run every
#: sync call on it too, each a whole round trip on a remote one, and those are what a
#: smaller pool would queue.
DEFAULT_THREAD_POOL_SIZE = 16

#: Settings that went, refused where the environment is read rather than ignored, each
#: by what replaces it. Refused because what each did would change without a word:
#: sessions kept somewhere else would be lost on upgrade, and a request naming no
#: session would run in `default` rather than the one the deployment chose.
_STORE = (
    "a session's backend keeps it now. Mount durable storage at <workspace>/sessions, or "
    "set KINGFISHER_SESSION_BACKENDS_FACTORY to session backends that keep them"
)
REMOVED_SETTINGS: Mapping[str, str] = MappingProxyType({
    "KINGFISHER_SESSION_STORE": _STORE,
    "KINGFISHER_SESSION_STORE_FACTORY": _STORE,
    "KINGFISHER_SESSION_ID": (
        "name the session in each request instead; a request naming none runs in "
        "'default'"
    ),
})

#: Settings that changed name, refused under the old one. A renamed setting still set
#: is one the deployment wanted, so carrying on with the default is never what it
#: chose -- for this one, the command line's sessions and shell on this host instead
#: of the deployment's own. `RETIRED` names are reported rather than refused because
#: their default may well be what was wanted.
RENAMED_SETTINGS: Mapping[str, str] = MappingProxyType(
    {"KINGFISHER_BACKEND_FACTORY": "KINGFISHER_SESSION_BACKENDS_FACTORY"}
)

#: Settings that each moved one thing out of the workspace, refused where they are
#: still set, by the name the thing has in `KINGFISHER_DEFINITIONS_DIR` now. Refused
#: rather than reported, because the folder named there is what a deployment chose to
#: serve, and carrying on would serve a different one without a word.
FOLDED_SETTINGS: Mapping[str, str] = MappingProxyType({
    "KINGFISHER_AGENTS_DIR": "agents",
    "KINGFISHER_MIDDLEWARES_DIR": "middlewares",
    "KINGFISHER_SKILLS_DIR": "skills",
    "KINGFISHER_SUBAGENTS_DIR": "subagents",
    "KINGFISHER_TOOLS_DIR": "tools",
    "KINGFISHER_MODELS_FILE": "models.yaml",
    "KINGFISHER_SOURCE_IDS_FILE": "source_ids.yaml",
})

class MissingCredentialsWarning(UserWarning):
    """An endpoint in `models.yaml` whose key is not set here, so it was dropped.

    A class of its own so `doctor`, which reports the same thing as a check, can
    silence this warning by name rather than by matching its wording.
    """


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
    #: Endpoints this file defines that were dropped for want of a key, valued by
    #: what was not set: the variable an endpoint names, the keys file setting, or the
    #: keys file's entry -- each written to read after "whose" and before "is not
    #: set". Includes an endpoint no model uses, which `unreachable` would not mention.
    dropped: Mapping[str, str] = field(default_factory=dict)
    #: The keys file `key:` entries were read from, or `None` where none was named.
    keys_file: Path | None = None
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

    #: What sessions make: `sessions/` and the harness's own `.kingfisher/`.
    workspace: Path
    #: What this deployment can run, where, and under which names.
    #:
    #: One field where there were five -- `models`, `endpoints`, `default_model`,
    #: `aliases`, `models_file` -- plus the two methods that read only those. A
    #: `Config` is a flat bag a deployment fills in, and the cluster has
    #: invariants between its parts that siblings of `shell_sandbox` could not
    #: express. See `Models`.
    models: Models
    #: Which source ids reach which agents, subagents and tools, or `None` where this
    #: deployment writes no policy.
    access: SourceIds | None = None
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
    # How many threads the async paths hand blocking work to. One number per
    # process, because the pool is one per process: threads are the process's to
    # spend. A call that blocks for a round trip holds its thread for all of it,
    # which is why the loop's default executor -- 12 workers on an 8-CPU host --
    # was not enough.
    thread_pool_size: int = DEFAULT_THREAD_POOL_SIZE
    shell_path_extra: tuple[str, ...] = ()
    # Who keeps `execute` out of the rest of the host. `auto` uses whatever the
    # platform offers, `bubblewrap` asks for it even where Landlock would run,
    # `external` says a container already does it, `off` opts out and is warned
    # about on every start. On by default because an exposure nobody opted into
    # is one nobody knows they have: measured unconfined, the shell could read
    # this deployment's own API keys, `~/.aws` and the GitHub CLI's token, and
    # `http_fetch` is one tool call away from sending them.
    shell_sandbox: str = "auto"
    skills_mounts: Mapping[str, Path] = NO_MOUNTS
    # Where definitions are copied *from*, which is the opposite direction to
    # the definition roots. Carried here as well as on `WorkspacePaths` because
    # `doctor` is handed a whole `Config` and has to report on it -- an unset,
    # mistyped or emptied source is the likeliest thing standing between an
    # install and a run once the definitions stop arriving with the wheel.
    assets: Path | None = None
    # What people write, apart from what sessions make: every definition kind, and
    # `models.yaml` and `source_ids.yaml` beside them. Keyword-only and without a
    # default, so a `Config` built by hand says where its definitions are rather than
    # inheriting a folder inside the workspace. One folder rather than one per kind:
    # several deployments point at it, and one path is what they agree on.
    definitions: Path = field(kw_only=True)
    # The session backends the command line runs on, named rather than built here:
    # `module:name` for something callable with no arguments that returns a
    # `SessionBackends`. Only the command line reads it -- `Kingfisher` takes them as
    # `backends=` -- and it is what lets `kingfisher sessions` and `reap` see sessions
    # a deployment's own keep somewhere else.
    session_backends_factory: str | None = None
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
    # Deployment-wide prompt text, appended after the harness prompt for every agent
    # and subagent (ahead of a workspace's PROMPT.md and an agent's own line). House
    # rules a library integrator sets in code: tone, a standing instruction, a policy.
    # Set once at startup, so it stays part of the cached prompt prefix rather than
    # moving per request. It adds to the harness prompt; it does not replace it.
    deployment_prompt: str = ""
    # The per-session folder names the agent addresses. Defaulted to kingfisher's own;
    # a deployment renames the leaves by passing a `Layout`. Set once at startup, so the
    # routes, the sandbox rules and the prompt that derive from it are stable per
    # deployment rather than per request.
    layout: Layout = DEFAULT_LAYOUT

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

    def __post_init__(self) -> None:
        refuse_overlap(self.workspace, self.definitions)

    @property
    def skills_dir(self) -> Path:
        """The skills catalogue. Shared by every session this workspace serves."""
        return self.definitions / "skills"

    @property
    def catalogue_roots(self) -> dict[str, Path]:
        """Every definition directory, together, as one answer."""
        return definition_roots_for(self.definitions)

    @property
    def authored_files(self) -> dict[str, Path]:
        """Where `models.yaml` and `source_ids.yaml` are read from, found or not."""
        # Where they were actually read, ahead of where the folder says. A `Config` built
        # in code may load either from anywhere, and the macOS profile denies the shell
        # this list: the one it must not edit is the one the next start reads.
        beside = authored_files_for(self.definitions)
        return {
            "models.yaml": self.models.source or beside["models.yaml"],
            "source_ids.yaml": self.access_source or beside["source_ids.yaml"],
        }
