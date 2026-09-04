"""Deployment configuration. Belongs to no layer, which is why it sits here.

`Config` lived in `domain/` so `application/` and `infrastructure/` could read
it without depending on each other -- reasoning about import direction, not
modelling. No domain rule reads a `Config`, and `base_url`, `api_key` and
`timeout_s` are not kingfisher's vocabulary: it was the innermost layer holding
a record for the outer ones.

So it sits at the package root, above the layers. `application/` and
`infrastructure/` may read it; `domain/` may not, and a test enforces that -- a
domain rule that needs a value takes the value, not the record. Reading it out
of the environment stays in `application/config.py`; the file describing
endpoints and models lives in the workspace, and
`infrastructure/model_catalogue.py` reads it.

An `ApiStyle` literal here once named the endpoint styles to choose between,
welding two things 1:1 -- which *wire format* to construct, which *endpoint* to
send to -- so a deployment had one endpoint per wire format and two
Anthropic-compatible gateways were unconfigurable. Wire formats are now a closed
registry in `infrastructure.harness.models` keyed by `api`; endpoints are open
data from `models.yaml`. See *Models and endpoints* in `docs/decisions.md`.
"""

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
    """One place to send a model call, and the credentials for it.

    `api` names a wire format from `infrastructure.models.ADAPTERS` -- closed,
    and shipped; everything else here is open data a deployment writes. Several
    endpoints may share one `api`, which the old `api_style` could not express.

    No name field. It had one, so an error could name the entry a prompt would
    have gone to -- but `ModelProfile.endpoint` already says that, and every
    caller holding an `Endpoint` was handed the profile beside it. Two fields
    saying one thing, one able to disagree with its own mapping key.
    """

    api: str
    base_url: str
    api_key: str


@dataclass(frozen=True)
class ModelProfile:
    """One model this deployment can run, and how.

    `model` is the id sent on the wire and is also this entry's key in
    `models.yaml`. It stays duplicated where `Endpoint.name` did not, because
    the two are not alike: an endpoint's name is written on the profile that
    reaches it, so removing it lost nothing, while a profile's model id has
    nowhere else to be. `build_model` takes a profile and an endpoint and must
    know what to *send*; handing the name separately would make the lookup
    return a third thing solely to carry it back.

    Duplicated, then, but not able to drift -- `Models` refuses a mapping whose
    key and `model` disagree.

    Every param is optional and, apart from the two with real defaults, omitted
    means *the kwarg is not passed at all* rather than passed as some default we
    chose. `temperature` is why: defaulting it would silently change every
    deployment's behaviour, in the one file whose purpose is to hand that
    decision to the operator.

    `max_tokens` and `timeout_s` keep defaults because a missing ceiling behaves
    differently per vendor, and because a model timeout has nowhere else to fall
    back to -- `Config.execution_timeout_s` bounds the shell and the interpreter,
    and reusing it here would rebuild the conflation this design took apart.

    `extra` is additive only, and carries kwargs peculiar to one endpoint --
    `reasoning_effort`, a thinking budget. It may not name one of the values
    above: Python raises on the duplicate keyword, which is the intended
    outcome, exactly as it is for `Adapter.extra`.
    """

    model: str
    endpoint: str
    max_tokens: int = 4096
    timeout_s: int = 120
    temperature: float | None = None
    top_p: float | None = None
    extra: Mapping[str, Any] = NO_EXTRA

    def kwargs(self) -> dict[str, Any]:
        """The model params to build with, omitting everything unset.

        Not a dict comprehension over the fields: `max_tokens` and `timeout_s`
        always go, the optional ones go only when set, and `extra` is spread
        last so a collision raises rather than overwriting.
        """
        params: dict[str, Any] = {"max_tokens": self.max_tokens, "timeout": self.timeout_s}
        if self.temperature is not None:
            params["temperature"] = self.temperature
        if self.top_p is not None:
            params["top_p"] = self.top_p
        return params


def definition_roots_for(
    workspace: Path,
    skills_root: Path | None = None,
    subagents_root: Path | None = None,
    tools_root: Path | None = None,
    agents_root: Path | None = None,
) -> dict[str, Path]:
    """The four definition directories: an override, or a name in the workspace.

    A free function because two records answer this question and the answer has
    to be the same one. `Config` is the whole configuration and needs a model
    catalogue to exist; `WorkspacePaths` is the part you can know before reading
    one, which is what seeding a brand-new workspace runs on. A second copy of
    `skills_root or workspace / "skills"` is how a deployment that relocated its
    catalogue gets seeded into the directory it stopped reading.
    """
    return {
        "agents": agents_root or workspace / "agents",
        "skills": skills_root or workspace / "skills",
        "subagents": subagents_root or workspace / "subagents",
        "tools": tools_root or workspace / "tools",
    }


def authored_files_for(
    workspace: Path,
    models_file: Path | None = None,
    groups_file: Path | None = None,
) -> dict[str, Path]:
    """The two files a deployment writes itself: an override, or a name in the workspace.

    A free function for the reason `definition_roots_for` is one -- two records
    answer this and the answer has to be the same -- and a separate one because
    these are not catalogue roots. They are single files, they relocate by their
    own variables, and each has a shipped `.example` beside it that is only
    useful in the directory the real file is read from.

    Both default inside the workspace and both relocate, for the reason the
    definition roots do: they hold content a person authored and reviewed, so
    several deployments sharing one reviewed file is the point rather than an
    accident.

    Keyed by filename rather than by concept, because that is what the caller
    with the examples needs: `models.yaml.example` finds its destination by
    dropping the suffix. Naming them `models` and `groups` would put the mapping
    between the two names somewhere else, where it could disagree.
    """
    return {
        "models.yaml": models_file or workspace / "models.yaml",
        "groups.yaml": groups_file or workspace / "groups.yaml",
    }


@dataclass(frozen=True)
class WorkspacePaths:
    """Where a deployment keeps things, before anything has been read.

    Everything else in a `Config` needs the model catalogue, and *that* file
    lives inside the workspace — so a first run has to be able to answer "which
    directories?" before it can answer "which models?". This is that answer, and
    `Config` is built on top of it rather than beside it.

    Said as "that file" rather than "the catalogue", which used to appear twice
    in this sentence meaning two different things. `catalogue_roots` below is
    the other one: where the *definitions* are kept, which is why the type it
    resolves to is `Definitions` and not `Catalogue`.
    """

    workspace: Path
    skills_root: Path | None = None
    subagents_root: Path | None = None
    tools_root: Path | None = None
    agents_root: Path | None = None
    #: The two single files, relocated. Not beside the four above because they
    #: are not directories and do not move together with them: one reviewed
    #: `models.yaml` shared across a fleet is the arrangement `compose.yaml`
    #: ships, and a group policy may sit somewhere else again.
    #:
    #: Carried here rather than only on `Config` because laying a workspace out
    #: places the worked example for each, and that runs before a catalogue can
    #: be read. A relocation this record cannot see is one seeding writes past.
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
    """What this deployment can run, where, and under which names.

    Five fields on `Config` before this, and a `Config` is a flat bag a
    deployment fills in -- which is the wrong shape for a cluster with
    invariants between its parts. `default` is a key of `models`; every alias
    binds a key of `models`; every profile's `endpoint` is a key of `endpoints`.
    None of that was expressible while the five were siblings of
    `shell_sandbox`, and the two questions worth asking of them -- what does
    this name resolve to, what is bound here -- had nowhere to live but `Config`
    itself.

    The same move `Definitions` made, for the same two reasons its docstring
    gives: this comes from a file deliberately relocatable and shared across a
    fleet, and it is addressed by name everywhere, so a type makes `.defualt` an
    error before the code runs rather than a `KeyError` while it does.

    Still not named by analogy with `Definitions`, but the difference is no
    longer the one this used to give. `Definitions` held three paths and
    deliberately did not read them; it holds a repository per kind now, and they
    do the reading. What separates the two is *when* and *how often*: a catalogue
    is read when the deployment is wired, answers every turn from what it held,
    and is layered per turn with whatever a session uploaded. This is read once,
    at config time, and never again -- there is no per-turn half for a model
    catalogue to have, because nothing a request carries adds a model.

    Which is also why this stayed a record rather than a repository.
    `model_catalogue` is one public function called once, its helpers steps in a
    single parse rather than views over a directory, and the seam a repository
    would have added is already here: a deployment holding its models elsewhere
    builds one of these and passes `Config(models=...)`, touching no file and no
    loader. The test suite runs that way.

    It lives here rather than in `infrastructure` for the reason
    `Config.catalogue_roots` does *not* return a `Definitions`: this file sits
    above the layers and imports none of them. Nothing this record does needs
    one -- `model_catalogue` reads the file and hands one back, and everything
    after that is a lookup.
    """

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
    #: Models this file defines that this machine cannot reach, and why -- keyed
    #: by model name, valued as the clause `resolve` drops into its refusal.
    #:
    #: Not somewhere to look a model up. `models` is what can run and stays the
    #: only answer to that; this exists so that "there is no such model" can stop
    #: being said about a model that is right there in the file, on an endpoint
    #: with no key. Named to make the mistake read wrong: `unreachable[name]`
    #: gives a sentence, never a profile.
    unreachable: Mapping[str, str] = field(default_factory=dict)
    #: Where all of it was read from. Informational, so a refusal can name the
    #: file that should have defined what it could not find.
    source: Path | None = None

    def __post_init__(self) -> None:
        """Refuse a mapping whose key disagrees with the profile under it.

        `ModelProfile.model` is the id sent on the wire and the key is what
        everything looks up by, so a pair that disagree means a delegate asking
        for one model and a client built for another -- silently, since both
        names are real. `model_catalogue` builds these from the key and cannot
        get it wrong; a test fixture or a caller assembling one by hand can.

        The check `Endpoint` no longer needs, which is why it kept no name.
        """
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

        `None` is the ordinary case: whatever the deployment named as default.

        Raises rather than falling back, on both halves. A name this deployment
        cannot run would otherwise reach an endpoint that has never heard of it
        -- a 404 if you are lucky and a wrong-model run if you are not -- and a
        model whose endpoint went missing would otherwise run somewhere its
        author did not choose. Both decide where the prompt goes, which is the
        one thing worth being loud about here.

        Returned as a pair because they are never wanted apart: `build_model`
        needs the params from one and the credentials from the other, and
        splitting them into two lookups is how they drift.
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
    """Everything kingfisher needs to build an agent for one workspace.

    Frozen but *not* hashable — `endpoints` is a real `dict`, so `hash(cfg)`
    raises. Nothing may key a cache on a `Config`; build the model once and let
    the composition root hold the instance.
    """

    workspace: Path
    #: What this deployment can run, where, and under which names.
    #:
    #: One field where there were five -- `models`, `endpoints`, `default_model`,
    #: `aliases`, `models_file` -- plus the two methods that read only those. A
    #: `Config` is a flat bag a deployment fills in, and the cluster has
    #: invariants between its parts that siblings of `shell_sandbox` could not
    #: express. See `Models`.
    models: Models
    #: Which groups reach which agents, subagents and tools, or `None` where
    #: this deployment writes no policy.
    #:
    #: Beside `models` because it is the same kind of thing: a static,
    #: operator-authored table, read once at startup, whose absence is a
    #: legitimate state rather than a setting somebody forgot. `None` is the
    #: whole of what "this deployment does not control access by group" means,
    #: and every deployment that predates this field has exactly that.
    #:
    #: Read once rather than per turn, unlike the catalogue. The catalogue is
    #: re-read because a workspace directory is edited between turns and a
    #: stale view of it is a wrong answer about what exists; a policy is a
    #: deployment setting, and a revocation lands on restart the way every
    #: other one here does.
    access: Groups | None = None
    #: Where the policy above was looked for, whether or not one was found.
    #:
    #: Beside `access` rather than on `Groups`, and the absent case is what
    #: decides it: with no file there is no `Groups` to carry a path, and "not
    #: set, and here is where I looked" is the one line this field exists to
    #: make possible. A `groups.yaml` written one directory off leaves a
    #: deployment silently open to everyone, and the symptom is nothing at all.
    #:
    #: It would also put a filesystem path on a `domain/` record whose own
    #: docstring says it reads no file. `Models` carries its `source` because it
    #: sits here, above the layers, and because a model catalogue is required --
    #: there is always an object to hang it on.
    #:
    #: `None` when nothing resolved a path: a `Config` assembled in code rather
    #: than read from the environment.
    access_source: Path | None = None
    #: What bounds one shell command or one interpreter run.
    #:
    #: Not the model timeout. This was `timeout_s` and served three unrelated
    #: consumers -- the model call, `execute`, and the interpreter sandbox --
    #: which is the same conflation `api_style` was. The model half moved into
    #: the table, where it is per-model; what is left is renamed for the two it
    #: still bounds. Splitting those two further is deferred until someone has a
    #: case for diverging values.
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
    agents_root: Path | None = None
    # Where definitions are copied *from*, which is the opposite direction to
    # the four above. Carried here as well as on `WorkspacePaths` because
    # `doctor` is handed a whole `Config` and has to report on it -- an unset,
    # mistyped or emptied source is the likeliest thing standing between an
    # install and a run once the definitions stop arriving with the wheel.
    assets: Path | None = None
    # Where a session's files are kept when the machine may not keep them. Unset
    # means the session directory is the only copy, which is right wherever the
    # host is allowed to hold data and is a silent disaster where it is not --
    # `doctor` says so when the workspace turns out to be in memory.
    #
    # A directory, which is the shape a deployment keeping sessions on this host
    # names. Somewhere else entirely is `session_store_factory` below.
    #
    # This comment used to end "a deployment reaching for that passes an object
    # rather than a path", and both halves of that were wrong. An environment
    # variable can name a factory -- `models.yaml` already reaches a chat class
    # through a `module:Name` string -- and passing an object only reaches the
    # one construction site a deployment controls, which is neither of the two
    # kingfisher ships.
    session_store: Path | None = None
    # The same port, named rather than built here: `module:name` for something
    # callable with no arguments that returns a `SessionStore`.
    #
    # A factory rather than a class, because kingfisher does not know whether a
    # store wants a bucket, a DSN or a mount point, and inventing a URL grammar
    # for stores it knows nothing about is the version of this that ages worst.
    # The deployment's own configuration stays the deployment's.
    #
    # Read here rather than only by the service, so that both entry points get
    # it. That is the whole reason this is a setting and not a constructor
    # argument -- `presentation/cli/__main__.py` builds its own `Kingfisher` and
    # there is nowhere to point it.
    #
    # An environment variable and never a workspace file. `confinement`'s
    # writable roots are the whole workspace with only `skills/` carved out, so
    # a settings file naming code to import would be code the agent can edit --
    # the rule `confinement.resolve` already states about its own profile.
    session_store_factory: str | None = None
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
    # Whether a turn remembers the one before it. On, because a session that
    # forgets is a surprising default for something that issues session ids.
    #
    # Off makes a deployment stateless in the only sense kingfisher can be:
    # there is no checkpointer, so no database, nothing to contend on, orphan or
    # vacuum. Files are unaffected -- `/data`, `/derived` and `/memory` are on
    # disk and a resumed session still finds them. It is the conversation that
    # goes, so `--session` still names the same files while the agent starts each
    # turn cold.
    #
    # Worth turning off for a request/response API, where a caller sends one task
    # and reads one answer. Measured: with it on, a two-turn workspace carries a
    # ~0.4MB database it never reads back.
    conversation_enabled: bool = True

    def __post_init__(self) -> None:
        """Refuse a deployment that named its session store twice.

        Not precedence, which was the first spelling and is the wrong shape: two
        answers to one question is what this codebase refuses everywhere else,
        and a deployment with both set has a mistake worth being told about
        rather than a preference worth honouring. Silently preferring one would
        put a deployment's sessions in the directory it stopped meaning to use,
        which is the failure that does not announce itself until somebody looks
        for a session that is somewhere else.

        Here rather than in the reader, so a `Config` assembled in Python is held
        to the same rule as one read from the environment. `config_from_env` is
        the common path and not the only one -- tests build these directly, and
        so does anyone embedding the library.
        """
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

        Longer than `turn_timeout_s`, and that gap is the whole point. A claim
        is taken over so a process that *died* cannot lock a session out
        forever; it must never be taken from a turn that is merely stopping.

        The two were the same number, so the claim became takeable the instant
        the run's deadline passed -- and a run stops *between stream chunks*,
        then still has to emit its result, collect what the turn left behind,
        and let go. Measured in that window: a second caller took the session
        while the first turn was still running, and the first turn then
        finished. Two turns in one session is what the claim exists to prevent,
        and what `SessionBusyError` records having seen once already, where "a
        turn simply vanished".

        The grace is the longest a single model call may take, because that is
        what a stopping turn is waiting on: it is inside one chunk, and a chunk
        ends when the call does. Derived rather than chosen -- a fixed number
        would be a guess a deployment raising a model's `timeout_s` would
        quietly invalidate. What follows the chunk is filesystem work, unbounded
        here and milliseconds against a bound in minutes.
        """
        return self.turn_timeout_s + max(
            (profile.timeout_s for profile in self.models.models.values()),
            # A catalogue with no models cannot run a turn at all, so nothing
            # can be mid-chunk; the grace is the timeout a profile would have
            # defaulted to rather than zero, which would restore the overlap.
            default=ModelProfile.timeout_s,
        )

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
        `infrastructure.harness.backend.prepare_scratch`: `/tmp` is world-writable, so the
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

        The only one of the three left, and the asymmetry is the point rather
        than an oversight. `subagents_dir` and `tools_dir` sat beside it with no
        reader in the package -- `catalogue_roots` took both over when it became
        the one answer to "where are the definitions" -- and stayed on because
        the trio looked symmetrical, which is the argument a vestige makes for
        itself. This one has a caller: `confinement.shell_confinement` needs the
        skills root alone, because the shell boundary is built per directory and
        not from a mapping.
        """
        return self.skills_root or self.workspace / "skills"

    @property
    def catalogue_roots(self) -> dict[str, Path]:
        """The four definition directories, together, as one answer.

        `skills_root` and its three siblings are what a deployment *sets*; this
        is what everything reading a catalogue *asks for*, and now the only way
        to ask, since the two per-directory properties above it had no reader
        left. A mapping rather than a tuple because the four relocate apart --
        separate overrides on purpose -- so there is no tree to name them by
        position under.

        A `Kingfisher` may be handed a different mapping, which is the whole
        seam: this is the fallback, not the only source. See
        `infrastructure.catalogue.resolve_definitions`.

        Delegated to `definition_roots_for` because `WorkspacePaths` answers the
        same question before a catalogue has been read, and the two must not be
        able to disagree.
        """
        return definition_roots_for(
            self.workspace,
            self.skills_root,
            self.subagents_root,
            self.tools_root,
            self.agents_root,
        )

    @property
    def authored_files(self) -> dict[str, Path]:
        """Where `models.yaml` and `groups.yaml` are read from, found or not.

        Assembled from the two fields that already record it rather than from a
        pair of overrides: a `Config` has been *read*, so it knows where each
        file was looked for -- `Models.source` and `access_source` exist to say
        so. `WorkspacePaths` holds the overrides instead, because it is what a
        first run has, and `authored_files_for` is where the two meet.

        `None` on either means a `Config` assembled in code, which relocated
        nothing; the workspace default is the right answer for it.
        """
        return authored_files_for(self.workspace, self.models.source, self.access_source)
