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
the file that describes the endpoints and models lives in the workspace, and
`infrastructure/model_catalogue.py` is what reads it.

There was an `ApiStyle` literal here, naming the endpoint styles a deployment
could choose between. It conflated two things: which *wire format* to construct
and which *endpoint* to send to, welded 1:1 -- so a deployment had exactly one
endpoint per wire format and two Anthropic-compatible gateways were
unconfigurable. The wire formats are now a closed registry in
`infrastructure.models`, keyed by `api`, and endpoints are open data read from
`models.yaml`. See `docs/design/2026-08-16-model-catalogue.md`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

#: The frozen default for an `extra` mapping. Shared with `Adapter.extra`
#: rather than written twice: the two are spread into one `build_model` call
#: and carry one rule between them -- additive only, may not name a value the
#: deployment configured -- so a reader meeting one should find the other.
#:
#: Public for that reason alone. An empty mapping cannot drift in value, so
#: this buys no safety; what it buys is that the pairing is visible.
NO_EXTRA: Mapping[str, Any] = MappingProxyType({})


@dataclass(frozen=True)
class Endpoint:
    """One place to send a model call, and the credentials for it.

    `api` names a wire format from `infrastructure.models.ADAPTERS`, which is
    closed and ships with kingfisher; everything else here is open data a
    deployment writes. Several endpoints may share one `api` -- that is the
    point of the split, and what the old `api_style` could not express.

    Credentials and wire format, and no name. It carried one for a while, so an
    error about where a prompt would have gone could say which entry it came
    from -- but that name is already written on the other side of the pair:
    `ModelProfile.endpoint` is how a model *reaches* here, and every caller
    holding an `Endpoint` was handed the profile beside it. Two fields saying
    one thing, one of them able to disagree with its own mapping key.
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
) -> dict[str, Path]:
    """The three definition directories: an override, or a name in the workspace.

    A free function because two records answer this question and the answer has
    to be the same one. `Config` is the whole configuration and needs a model
    catalogue to exist; `WorkspacePaths` is the part you can know before reading
    one, which is what seeding a brand-new workspace runs on. A second copy of
    `skills_root or workspace / "skills"` is how a deployment that relocated its
    catalogue gets seeded into the directory it stopped reading.
    """
    return {
        "skills": skills_root or workspace / "skills",
        "subagents": subagents_root or workspace / "subagents",
        "tools": tools_root or workspace / "tools",
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

    @property
    def catalogue_roots(self) -> dict[str, Path]:
        return definition_roots_for(
            self.workspace, self.skills_root, self.subagents_root, self.tools_root
        )


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
    gives. This comes from a file that is deliberately relocatable and shared
    across a fleet, and it is addressed by name everywhere, so a type is what
    makes `.defualt` an error before the code runs rather than a `KeyError`
    while it does.

    Still not named by analogy with `Definitions`, but the difference is no longer
    the one this used to give. `Definitions` held three paths and deliberately did
    not read them; it holds a repository per kind now, and they do the reading.
    What separates the two is *when* and *how often*: a catalogue is read when
    the deployment is wired, answers every turn from what it held, and is layered
    per turn with whatever a session uploaded. This is read once, at config time,
    and never again -- there is no per-turn half for a model catalogue to have,
    because nothing a request carries adds a model.

    Which is also why this stayed a record rather than becoming a repository.
    `model_catalogue` is one public function called once, its helpers are steps
    in a single parse rather than views over a directory, and the seam a
    repository would have added is already here: a deployment holding its models
    somewhere else builds one of these and passes `Config(models=...)`, touching
    no file and no loader. The test suite runs that way.

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
    #: General names bound to models of this deployment's choosing, for
    #: definitions that know what *kind* of model they want and cannot know its
    #: name. A second namespace, kept apart from `models` on purpose.
    #:
    #: Not called `roles`. That word belonged to `KINGFISHER_MODEL_{role}` and
    #: `model_for("main")`, deleted for being the wrong granularity, and reusing
    #: it would revive the vocabulary of the thing that was removed.
    aliases: Mapping[str, str] = field(default_factory=dict)
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

    def bound(self, alias: str) -> str:
        """The model this deployment binds `alias` to.

        Refuses rather than falling back to the default, which is the whole
        point of the indirection. A definition writing `alias: alternate` is
        saying it needs a model unlike the one beside it; quietly handing it
        that very model is the failure the alias exists to prevent, and it is
        invisible -- the delegate still builds, still answers, and the answer is
        worth nothing.

        So an unbound alias stops the build and says what to write. Loud is
        cheap here: it fires only when a request activates the delegate, so
        seeding a definition you have not bound for costs nothing until you use it.
        """
        model = self.aliases.get(alias)
        if model is None:
            known = tuple(sorted(self.aliases))
            where = f" in {self.source}" if self.source else ""
            msg = (
                f"no model bound to alias {alias!r}{where}; this deployment binds {known}. "
                f"Add it under 'aliases:', naming one of {tuple(sorted(self.models))}"
            )
            raise ConfigError(msg)
        return model

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
            msg = f"no model {wanted!r}{where}; this deployment can run {known}"
            raise ConfigError(msg)
        endpoint = self.endpoints.get(profile.endpoint)
        if endpoint is None:
            # Unreachable for the default, which `load` checks. Reachable for a
            # model a request named, whose endpoint was dropped for having no
            # credentials on this machine.
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
    def tools_dir(self) -> Path:
        """The tool catalogue: Python modules imported into this process.

        Deliberately not a backend route. A skill is data the agent reads and a
        tool is code this process runs, so the agent is given no path that
        reaches here — the only agent that could write a tool is one already
        holding `execute`, which can run anything on the host regardless.
        """
        return self.tools_root or self.workspace / "tools"

    @property
    def catalogue_roots(self) -> dict[str, Path]:
        """The three definition directories, together, as one answer.

        The three above are what a deployment *sets*; this is what everything
        that reads a catalogue *asks for*. Kept as a mapping rather than a tuple
        of paths because the three are relocatable apart -- `skills_root` and
        its siblings are separate overrides on purpose -- so there is no tree to
        name them by position under.

        A `Kingfisher` may be handed a different mapping, which is the whole
        seam: this is the fallback, not the only source. See
        `workspace_fs.resolve_definitions`.

        Delegated to `definition_roots_for` because `WorkspacePaths` answers the
        same question before a catalogue has been read, and the two must not be
        able to disagree.
        """
        return definition_roots_for(
            self.workspace, self.skills_root, self.subagents_root, self.tools_root
        )

