"""Deployment configuration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from types import MappingProxyType
from typing import Any

from kingfisher.core.access import Capabilities

from .layout import DEFAULT_LAYOUT, HARNESS_OWNED, SESSIONS, Layout

#: The default `extra`, shared with `WireFormat.extra` because both spread into one
#: `build_model` call under one rule: additive only, never a value the deployment set.
NO_EXTRA: Mapping[str, Any] = MappingProxyType({})

#: The skills mounts of a deployment that names none.
NO_MOUNTS: Mapping[str, Path] = MappingProxyType({})


@dataclass(frozen=True)
class Landing:
    """The attribute each value lands in on a chat class, for `build_model` to read back.

    No `temperature` or `top_p`: a class may drop them on purpose (`ChatOpenAI` does for
    `gpt-5`, which rejects it), and checking them would refuse a model built correctly.
    """

    model: str
    base_url: str
    api_key: str
    max_tokens: str
    timeout: str
    #: `None` for a class with nowhere to keep it, and a model setting `max_retries` on
    #: that format is refused rather than sent where the request body would carry it.
    max_retries: str | None = None


@dataclass(frozen=True)
class WireFormat:
    """One wire format: which class speaks it, and what it needs to be told.

    Endpoints are gateway-shaped, one base URL and one key; a format needing more (a
    region, a credentials profile) is a new field on `Endpoint`, not a new row.
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
    #: The variable the key was read from, kept for a 401: the YAML is right and the key
    #: is not, so the variable is the one thing worth naming.
    key_env: str = ""
    #: The keys file entry the key was read from, where `models.yaml` says `key:` and
    #: `key_env` is empty; then this is what a 401 names.
    key_entry: str = ""
    #: The row `api` named, resolved at load. Carried rather than looked up by name
    #: later: a deployment's own rows exist only in the table load was handed.
    wire_format: WireFormat = field(kw_only=True)


@dataclass(frozen=True)
class ModelProfile:
    """One model this deployment can run, and how."""

    model: str
    endpoint: str
    max_tokens: int = 4096
    timeout_s: int = 120
    #: How many more times the SDK tries a call the provider refused or could not
    #: answer, or `None` for the SDK's own default.
    max_retries: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    extra: Mapping[str, Any] = NO_EXTRA
    #: Where older tool results start being cleared from the conversation of a graph
    #: running this model, in tokens, or `None` for the default rule. Kingfisher's own, so
    #: never a kwarg.
    clear_results_at: int | None = None

    def kwargs(self) -> dict[str, Any]:
        """The model params to build with, omitting everything unset."""
        params: dict[str, Any] = {"max_tokens": self.max_tokens, "timeout": self.timeout_s}
        if self.max_retries is not None:
            params["max_retries"] = self.max_retries
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
    """The files a deployment writes itself, beside its definitions."""
    return {
        "models.yaml": definitions / "models.yaml",
        "INSTRUCTIONS.md": definitions / "INSTRUCTIONS.md",
    }


def refuse_overlap(workspace: Path, definitions: Path) -> None:
    """Refuse a workspace and a definitions folder that are one, or one inside the other.

    Sessions inside the definitions put runtime state in a shared, reviewed folder;
    definitions inside the workspace sit where the macOS fence lets every shell read.
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
    #: Skills directories beside the catalogue, each mounted at `/skills/<label>/`. Not
    #: a definition root: a mount is somebody else's, so nothing creates or seeds it.
    skills_mounts: Mapping[str, Path] = NO_MOUNTS
    #: Where definitions are *copied from*. `None` is normal, not an error: only
    #: seeding needs it, so only seeding refuses without it.
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


#: The thread pool's size when nothing configured one. Sessions on the interface's
#: default async methods run every sync call on it, each a round trip on a remote
#: one, and those are what a smaller pool would queue.
DEFAULT_THREAD_POOL_SIZE = 16

#: Settings that went, each by what replaces it. Refused rather than ignored: sessions
#: kept elsewhere would be lost on upgrade, or a request naming none run in `default`.
#: Not in the module that reads the environment, because they are refusals, not knobs.
_STORE = (
    "a session's backend keeps it now. Mount durable storage at <workspace>/sessions, or "
    "set KINGFISHER_SESSIONS to sessions that keep them"
)
REMOVED_SETTINGS: Mapping[str, str] = MappingProxyType({
    "KINGFISHER_SESSION_STORE": _STORE,
    "KINGFISHER_SESSION_STORE_FACTORY": _STORE,
    "KINGFISHER_SESSION_ID": (
        "name the session in each request instead; a request naming none runs in "
        "'default'"
    ),
    "KINGFISHER_PLUGINS": (
        "the kingfisher command takes no plugins now. Code that needs one builds its own "
        "Kingfisher(...) and passes each as a keyword"
    ),
})

#: Settings that changed name, refused under the old one: one still set is one the
#: deployment wanted, so its default is never what it chose. `RETIRED` names are only
#: reported, because their default may well be what was wanted.
RENAMED_SETTINGS: Mapping[str, str] = MappingProxyType(
    {
        "KINGFISHER_BACKEND_FACTORY": "KINGFISHER_SESSIONS",
        "KINGFISHER_SESSION_BACKENDS_FACTORY": "KINGFISHER_SESSIONS",
        "KINGFISHER_ADAPTERS_FACTORY": "KINGFISHER_WIRE_FORMATS",
    }
)

#: Settings that each moved one thing into `KINGFISHER_DEFINITIONS_DIR`, by its name
#: there. Refused rather than reported: carrying on would silently serve a folder the
#: deployment did not choose.
FOLDED_SETTINGS: Mapping[str, str] = MappingProxyType({
    "KINGFISHER_AGENTS_DIR": "agents",
    "KINGFISHER_MIDDLEWARES_DIR": "middlewares",
    "KINGFISHER_SKILLS_DIR": "skills",
    "KINGFISHER_SUBAGENTS_DIR": "subagents",
    "KINGFISHER_TOOLS_DIR": "tools",
    "KINGFISHER_MODELS_FILE": "models.yaml",
})

class MissingCredentialsWarning(UserWarning):
    """An endpoint in `models.yaml` whose key is not set here, so it was dropped.

    Its own class so `doctor`, which reports this as a check, can silence it by name.
    """


@dataclass(frozen=True)
class Models:
    """What this deployment can run, where, and under which names."""

    #: Keyed by the id sent on the wire. **Closed**: a name absent from here is
    #: refused rather than passed to an endpoint that has never heard of it.
    models: Mapping[str, ModelProfile]
    #: Every endpoint whose credentials are present. The rest are dropped with a
    #: warning, so one reviewed file works across a fleet holding different keys.
    endpoints: Mapping[str, Endpoint]
    #: What runs when nothing names another; always a key of `models`, because
    #: `model_catalogue.load` refuses a file where it is not.
    default: str
    #: What the memory writer runs on, or `None` for the agent's own model. A key of
    #: `models` where set, for the reason `default` is.
    memory_writer: str | None = None
    #: Models this file defines that this machine cannot reach, and why -- keyed by
    #: model name, valued as the clause `resolve` drops into its refusal.
    unreachable: Mapping[str, str] = field(default_factory=dict)
    #: Endpoints dropped for want of a key, valued by what was not set, worded to read
    #: after "whose" and before "is not set". Includes an endpoint no model uses.
    dropped: Mapping[str, str] = field(default_factory=dict)
    #: The keys file `key:` entries were read from, or `None` where none was named.
    keys_file: Path | None = None
    #: Where it was read from, so a refusal can name the file.
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
        """Which model to build, and where to send it.

        Raises rather than falling back: a fallback would send the prompt to a model or
        an endpoint nobody chose.
        """
        wanted = self.default if name is None else name
        profile = self.models.get(wanted)
        if profile is None:
            known = tuple(sorted(self.models))
            where = f" defined in {self.source}" if self.source else ""
            # Before "no such model", which is false for a model whose endpoint has no
            # key here: the catalogue defines it; this machine cannot use it.
            if reason := self.unreachable.get(wanted):
                msg = f"model {wanted!r} runs on {reason}; this deployment can run {known}"
                raise ConfigError(msg)
            msg = f"no model {wanted!r}{where}; this deployment can run {known}"
            raise ConfigError(msg)
        endpoint = self.endpoints.get(profile.endpoint)
        if endpoint is None:
            # Only a `Models` built by hand: `load` drops such a model into `unreachable`.
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
    models: Models
    #: What bounds one shell command or one interpreter run.
    execution_timeout_s: int = 120
    # What one session may consume, not one caller: kingfisher is tenant-blind by
    # design, so bounding a caller is the job of whatever knows who is calling.
    #
    # Nothing else bounds `recursion_limit` times a model's `timeout_s`, which allows
    # 150 x 120s, about 5 hours; an hour is far past any real turn.
    turn_timeout_s: int = 3600
    # Unset by default: workspaces vary by orders of magnitude. Checked before a turn,
    # never during: `execute` writes without any file tool seeing it.
    session_max_bytes: int | None = None
    # How many rounds of a conversation keep their saved tool results; an older one keeps
    # only its ledger row. Unset keeps every one: what a session can do without is the
    # deployment's to judge.
    keep_results_rounds: int | None = None
    # How long an idle session survives a `reap` that names no age. An age, not a count
    # kept: a count would weigh every caller's sessions against each other.
    session_ttl_s: int = 7 * 24 * 3600
    # Each agent turn costs 2-3 graph steps. 60 cut a real 1,000-row analysis off
    # mid-step at 20 turns.
    recursion_limit: int = 150
    # Threads the async paths hand blocking work to, one pool per process. A round trip
    # holds its thread throughout, which is why the loop's default executor (12 workers
    # on an 8-CPU host) was not enough.
    thread_pool_size: int = DEFAULT_THREAD_POOL_SIZE
    skills_mounts: Mapping[str, Path] = NO_MOUNTS
    # Where definitions are copied *from*. Here as well as on `WorkspacePaths` because
    # `doctor` reports on a whole `Config`, and a bad source is the likeliest fault.
    assets: Path | None = None
    # What people write: every definition kind and `models.yaml`. No
    # default, so a hand-built `Config` cannot inherit a folder inside the workspace.
    # One folder, not one per kind: several deployments point at it.
    definitions: Path = field(kw_only=True)
    # What this deployment wires, not what one request may use (`Capabilities`): these
    # shape the cached system prompt, and varying them per request would give up a
    # measured ~90% cache hit.
    skills_enabled: bool = False
    memory_enabled: bool = False
    # How many turns the memory writer waits between runs where nothing was summarised.
    memory_every: int = 4
    # On, because a cited id is what lets a reader trace a claim to the result behind it.
    cite_results: bool = True
    # A JavaScript sandbox to compute in: no filesystem or network, capped memory and
    # time, and only the tools the request granted.
    interpreter_enabled: bool = False
    # On, because a session that forgets surprises anything that issues session ids.
    # Off suits a one-task request/response API: on, a two-turn workspace carries a
    # ~0.4MB database it never reads back.
    conversation_enabled: bool = True
    # The ceiling a request's capabilities are narrowed by, never widened past. `None`
    # restricts nothing.
    grants: Capabilities | None = None
    # The per-session folder names the agent addresses. Per deployment, never per
    # request: the routes, the sandbox rules and the prompt derive from it.
    layout: Layout = DEFAULT_LAYOUT

    @property
    def claim_stale_after(self) -> float:
        """How old a session's claim must be before another turn may take it.

        The turn deadline plus the longest one superstep can hold: a run stops only
        between stream chunks, so it stops no earlier than the end of whichever chunk
        straddles the deadline, and then still has to emit its result, collect what it
        left and let go.
        """
        return self.turn_timeout_s + max(
            # Every bound on a superstep, not the model's alone: the deadline is read
            # between chunks, and a shell command straddles it exactly as a model call
            # does. Measured, a 6s command under a 1s turn and a 1s model timeout let a
            # second caller take the session 3.0s into a turn that ran to 6.0s. A bound
            # only while `execute` refuses a longer `timeout` and the shell clamps to it,
            # and only for what the shell can reach: on macOS, a process calling
            # `setsid()` outlives it.
            self.execution_timeout_s,
            *(profile.timeout_s for profile in self.models.models.values()),
            # Not zero, even with neither models nor a bound on a command: zero would
            # restore the overlap.
            ModelProfile.timeout_s,
        ) + self._memory_writer_bound

    @property
    def _memory_writer_bound(self) -> float:
        """How long the memory writer's call may hold the claim after the turn's last
        superstep, which it follows: none where memory is off."""
        if not self.memory_enabled:
            return 0
        return max(
            (profile.timeout_s for profile in self.models.models.values()),
            default=ModelProfile.timeout_s,
        )

    def __post_init__(self) -> None:
        refuse_overlap(self.workspace, self.definitions)

    @property
    def sessions_root(self) -> Path:
        """Where this workspace keeps its sessions, one folder each."""
        return self.workspace / SESSIONS

    @property
    def kingfisher_dir(self) -> Path:
        """The workspace's folder that is kingfisher's own, neither a session's nor the
        agent's: what a plugin keeps here, the agent's shell must not be able to change."""
        return self.workspace / HARNESS_OWNED

    @property
    def retired_prompt(self) -> Path:
        """Where the house rules were kept before `INSTRUCTIONS.md`. A start that finds a file
        here refuses, so the agent's shell must not be able to make one."""
        return self.workspace / "PROMPT.md"

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
        """Where the files a deployment writes are read from, found or not."""
        # Where they were actually read wins: the macOS profile denies the shell this
        # list, and the copy it must not edit is the one the next start reads.
        beside = authored_files_for(self.definitions)
        return {
            **beside,
            "models.yaml": self.models.source or beside["models.yaml"],
        }
