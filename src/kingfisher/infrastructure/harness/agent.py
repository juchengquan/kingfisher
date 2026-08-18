"""Agent assembly: kingfisher's configuration and grants into a deepagents graph.

Not the composition root, though it said so for a while — `Kingfisher.__init__`
is, and says so too. That one chooses a deployment's collaborators: which
config, which session directories, which thread store, which middleware
registry. This one is the deepagents adapter, and the only reason it cannot
live a layer up is that assembling the graph means naming deepagents' own
types.

Construction stays free of side effects that a test would have to clean up, and
every dependency is injectable, so wiring can be exercised with a fake model and
no network, no database, and no sweeping.

Two jobs it used to do live beside it now, because at 657 lines it was doing
four. `prompting` assembles the system prompt -- moved out because it needs
nothing foreign, and sharing a file with `create_deep_agent` cost every consumer
of `system_prompt` 764ms and three provider SDKs. `delegation` resolves what a
delegate runs with. Neither calls anything here; `build_agent` is the only
caller of either.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from deepagents import FilesystemPermission, create_deep_agent
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT
from langchain.agents.middleware import TodoListMiddleware

from kingfisher.config import Config, ConfigError
from kingfisher.domain import skill
from kingfisher.domain.agent import AgentSpec
from kingfisher.domain.capabilities import (
    ALL,
    Capabilities,
    CapabilityError,
    Selection,
    narrowed,
    refuse_ungranted_models,
    refuse_unoffered,
)
from kingfisher.domain.subagent import RunOn
from kingfisher.domain.subagent.rules import refuse_cycles, refuse_two_of_a_name
from kingfisher.domain.tool import Found, Offering
from kingfisher.infrastructure.catalogue import Definitions, source_of
from kingfisher.infrastructure.catalogue.layered import for_session
from kingfisher.infrastructure.harness import skill_registry
from kingfisher.infrastructure.harness.backend import (
    MEMORY_SOURCES,
    SKILLS_ROUTE,
    HostPathGuard,
    WorkspaceToolErrors,
    build_backend,
    skills_sources,
)
from kingfisher.infrastructure.harness.delegation import (
    as_subagent,
    indistinct,
    model_for,
    model_object,
    subagent_helpers,
    subagent_middleware,
    subagent_skills,
)
from kingfisher.infrastructure.harness.models import build_model
from kingfisher.infrastructure.harness.narrowing import (
    DeclaredDelegatesOnly,
    NarrowedSkills,
    ToolAllowlist,
)
from kingfisher.infrastructure.harness.skill_registry import SkillRegistry
from kingfisher.infrastructure.prompting import system_prompt

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from langgraph.graph.state import CompiledStateGraph

    from kingfisher.domain.subagent import SubagentSpec


#: Delegation, wherever it is dispatched from.
TASK_TOOL = "task"


#: For a request that declined memory a deployment did wire. Reads are denied
#: rather than the prompt rewritten: the prompt is the cached prefix.
MEMORY_IS_DENIED = FilesystemPermission(
    operations=["read"],
    paths=["/memory/**"],
    mode="deny",
)


def available_skills(
    cfg: Config, session_dir: Path | None, *, catalogue: Definitions | None = None
) -> tuple[str, ...]:
    """Every skill this request may activate: the catalogue, plus its own.

    `catalogue` says where the shared half is read from, falling back to `cfg`.
    What a session adds, and how the two halves merge, is `layered.for_session`
    -- the rule lives there because it differs per kind and a reader comparing
    them should not have to visit two functions to see the difference.

    The catalogue half is the *registry*, not the directory listing, and that is
    the point of it: a directory that looks like a skill and will not parse
    used to be advertised here, accepted by the build, allowed through the
    filter, and then absent from an agent that reported nothing wrong. Asking
    what will be loaded makes naming one an ordinary unknown-skill refusal.

    A session's own skills stay a listing. They are written by `uploads`, which
    reads each header to file it under the name inside it, so the two cannot
    disagree the way a catalogue's could -- and a request's uploads are checked
    when they are provisioned rather than here.
    """
    return activatable_skills(cfg, session_dir, catalogue=catalogue).names


def activatable_skills(
    cfg: Config, session_dir: Path | None, *, catalogue: Definitions | None = None
) -> SkillRegistry:
    """One registry for both halves: the catalogue, plus this request's own.

    The single answer to "what may this request activate", and it is a single
    answer because the last time there were two they disagreed. `available_skills`
    merged the session's directory listing over the catalogue registry while
    `build_agent` resolved against the catalogue registry alone, so every
    uploaded skill was advertised and then refused as unknown -- the whole
    feature, not an edge of it.

    The catalogue half is cached for the life of the deployment; the session
    half is read per turn, because that is when it arrives. One listing of a
    directory holding at most a handful of skills.
    """
    resolved = catalogue or Definitions.from_config(cfg)
    uploaded = None if session_dir is None else session_dir / skill.DIRECTORY / skill.UPLOADED
    return resolved.registry.merged(skill_registry.read_uploaded(uploaded))


def defined_subagents(
    cfg: Config, session_dir: Path | None, *, catalogue: Definitions | None = None
) -> dict[str, SubagentSpec]:
    """Every subagent this request may activate: the catalogue, plus its own.

    A function because two callers need the same answer: `build_agent`, which
    wants the specs, and the service, which wants only the names so it can say
    which of them a request did not grant. Written out at both, the rule about
    what a session adds to the catalogue would exist twice.
    """
    return dict(for_session(catalogue or Definitions.from_config(cfg), session_dir).subagents.specs)


def unrunnable_delegates(
    cfg: Config, *, catalogue: Definitions | None = None
) -> tuple[tuple[str, str], ...]:
    """`(name, why)` for each defined delegate this deployment cannot run.

    Every definition the catalogue holds, not the ones a request activated --
    which is the difference from `indistinct_delegates` beside it, and the whole
    point. A delegate binding an alias to a model on an endpoint with no key is
    invisible until somebody activates it: the workspace loads, the listing is
    clean, and the failure waits for the first request that names it.

    Through `model_for` and `resolve`, the two calls a build makes, so this
    cannot come to disagree with what actually happens. Both are needed and
    neither is enough: `model_for` catches an alias nothing binds and a delegate
    whose every candidate was passed over, and returns a model *name*; whether
    that name can be reached is `resolve`'s question, and it is the one the
    dropped-endpoint case fails.

    Reported, never refused, and never called -- no model is built and nothing
    goes over a network. It costs two dictionary lookups per definition, which
    is what lets `doctor` run it before a deployment rather than after.
    """
    from kingfisher.infrastructure.harness.delegation import model_for  # noqa: PLC0415

    found: list[tuple[str, str]] = []
    for name, spec in sorted(defined_subagents(cfg, None, catalogue=catalogue).items()):
        try:
            model = model_for(spec, cfg)
            if model is not None:
                cfg.models.resolve(model)
        except ConfigError as exc:
            found.append((name, str(exc)))
    return tuple(found)


def indistinct_delegates(
    cfg: Config,
    capabilities: Capabilities,
    session_dir: Path | None,
    *,
    catalogue: Definitions | None = None,
    run_on: Mapping[str, RunOn] | None = None,
) -> tuple[tuple[str, str], ...]:
    """`(name, why)` for each activated delegate that asked to run elsewhere and
    did not.

    Asked after the build rather than during it, the way `_withheld_by_kind`
    is: `build_agent` returns a graph, and a fact about the run is not one of
    the things a graph can carry. It re-resolves through `model_for`,
    the same call the build makes, so the two cannot come to disagree about
    where a delegate ended up.
    """
    if capabilities.subagents is None:
        return ()
    defined = defined_subagents(cfg, session_dir, catalogue=catalogue)
    activated = tuple(defined) if capabilities.subagents == ALL else capabilities.subagents
    wanted = run_on or {}

    found = []
    for name in activated:
        spec = defined.get(name)
        if spec is None:
            continue  # `build_agent` refuses this; reporting is not its job
        try:
            model = model_for(spec, cfg, override=wanted.get(name))
        except ConfigError:
            # An unbound alias, or a model this deployment cannot run. The build
            # refuses it with the message worth reading; reporting is not
            # refusing, and raising a second copy of that refusal from here
            # would put it in front of the caller twice, worded for the wrong
            # question. Skipped, and the build says why.
            continue
        if why := indistinct(spec, cfg, model=model):
            found.append((name, why))
    return tuple(found)


def registered_tools(graph: Any) -> tuple[str, ...] | None:
    """Tool names the compiled agent can actually dispatch, or `None` if unreadable.

    Derived from the graph rather than listed here, because a hardcoded list
    would drift the first time deepagents adds or renames a tool. The path into
    the tool node is not a public contract, so a shape we do not recognise is
    reported rather than raised: taking down every build over an introspection
    detail would be the worse trade, and a rename upstream is meant to fail
    `test_a_real_build_is_readable` instead.

    It used to answer `()` for both "no tools" and "cannot read this", and said
    so -- callers "read [it] as *cannot check*". They were the same answer
    because nothing needed them apart: every graph here is one `build_agent`
    made, and those always dispatch something.

    A compiled subagent is the first graph kingfisher will be handed rather than
    have built, and there the difference is the whole point -- a listing that
    prints "no tools" for a graph it could not read has stated a fact it does
    not have. So `()` now means none, and `None` means unreadable.

    Telling them apart takes a second look, because the obvious one does not
    work: `create_agent(model, tools=[])` compiles to `['__start__', 'model']`
    with **no tool node at all**, which is exactly the shape of a hand-written
    graph that dispatches nothing. Measured, not assumed. What separates them is
    the `model` node -- an agent graph keeps one whether or not it has tools, so
    a tool node missing from *that* shape is a definite none, and anything else
    is a shape with no answer in it.
    """
    nodes = getattr(graph, "nodes", None)
    if not hasattr(nodes, "get"):
        return None
    by_name = getattr(getattr(nodes.get("tools"), "bound", None), "tools_by_name", None)
    if isinstance(by_name, dict):
        return tuple(sorted(by_name))
    return () if "model" in nodes else None


# `/data` holds what a caller supplied and nothing else has a copy of: it is
# never re-derivable from the workspace, and kingfisher versions nothing.
# `FilesystemOperation` is just read|write and `delete` maps to write, so this
# single rule covers write_file, edit_file and delete.
#
# It does not cover `execute` — filesystem permissions are applied by
# FilesystemMiddleware at the tool level, and the shell bypasses them entirely,
# which is why `protect_data()` drops the write bits underneath this.
DATA_IS_READ_ONLY = FilesystemPermission(
    operations=["write"],
    paths=["/data/**"],
    mode="deny",
)


# The catalogue is *instructions the agent follows*, which makes it the one
# route where a write outlasts the request that made it. `/memory` and
# `/derived` belong to a session and go when it does; a skill belongs to the
# deployment, and `KINGFISHER_SKILLS_DIR` exists so several deployments can
# share one reviewed set -- so a skill edited during one request is read by
# every later request, in every deployment pointing at that directory.
#
# Measured before adding, because `/data` had a rule and this did not:
# `backend.write("/skills/demo/PWNED.md", ...)` and `backend.edit(...)` both
# succeeded against the catalogue on disk. Nothing depended on it -- a request's
# own skills are written host-side by `uploads`, never through a file tool.
#
# `/skills/uploaded/**` is covered too, and deliberately. It is a session's own
# half rather than the deployment's, but kingfisher writes it host-side for the
# same reason, and an agent able to rewrite an uploaded skill could rewrite the
# instructions it was about to follow.
#
# Same `write` operation as above, so it covers write_file, edit_file and
# delete. It does not cover `execute` either -- which is why
# `confinement.resolve` denies writes to the same directory in the sandbox
# profile. Both halves are needed and neither is sufficient: the profile is
# macOS-only and can be switched off, and this one never sees the shell.
SKILLS_ARE_READ_ONLY = FilesystemPermission(
    operations=["write"],
    paths=["/skills/**"],
    mode="deny",
)


#: The one warning kingfisher asks for and does not want to hear. Matched on
#: the message rather than silenced by level, because the same logger carries
#: warnings that matter -- a snapshot that failed to restore, and a workspace
#: tool skipped for having a name JavaScript cannot spell.
_EXPECTED_DROP = "Dropping QuickJS snapshot"


class _ExpectedSnapshotDrop(logging.Filter):
    """Drop the warning `max_snapshot_bytes=1` makes inevitable.

    The cap is deliberate -- it is what makes the sandbox affordable to leave
    on -- and the library warns every time it drops an image, which is every
    turn. So the warning reports a setting we chose, on a schedule nothing can
    reduce, into the middle of the agent's streamed prose. It reaches the
    terminal at all only because a library that configures no logging leaves
    Python's last-resort handler to print WARNING and above to stderr.

    A filter on that one logger, not a level and not a handler: kingfisher is a
    library, and a library that calls `basicConfig` decides for a program it
    does not own. This suppresses one record and leaves every other decision to
    whoever is hosting us.

    If upstream rewords the message the filter stops matching and the noise
    comes back. That is the safe direction: the failure is visible rather than
    a real warning silently swallowed.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return not str(record.msg).startswith(_EXPECTED_DROP)


def quieten_expected_snapshot_drop() -> None:
    """Install the filter once, on the logger that emits it.

    Idempotent because `_interpreter` runs per request: an agent is built for
    every turn, and a filter added each time would be a list that grows for the
    life of the process.
    """
    logger = logging.getLogger("langchain_quickjs.middleware")
    if not any(isinstance(f, _ExpectedSnapshotDrop) for f in logger.filters):
        logger.addFilter(_ExpectedSnapshotDrop())


def _interpreter(cfg: Config, permitted: tuple[str, ...] | None) -> Any:
    """The JavaScript sandbox, if this deployment wired one.

    `ptc` is the request's own tool grant, unchanged. A caller that withheld
    `execute` cannot reach it from inside the sandbox either; a caller that
    granted it is not escalating by using it from code. That is the same rule
    the parent and its delegates follow -- restrictions attach to narrowing,
    and a caller who narrowed nothing has nothing to escape from.

    `None` rather than an empty tuple when the request is unrestricted: the
    library reads `None` as "no allowlist", and an empty tuple would mean the
    opposite of what an unrestricted request asked for.

    `mode="thread"` because a thread here is a kingfisher session -- the
    checkpointer already keys on the same id, so REPL state and conversation
    state expire together rather than one outliving the other. Largely moot
    while the snapshot is capped below, and kept so that raising the cap gives
    the aligned lifetime rather than a second decision to remember.

    `max_snapshot_bytes=1` drops the VM image instead of storing it, and that
    is the whole reason the sandbox is affordable to leave on. The library
    otherwise serialises the entire QuickJS heap into the checkpoint at the end
    of every turn: measured at exactly 1,280KB each time, a floor rather than a
    cost that scales with the work, and written whether or not `eval` was
    called at all. In one observed run it was called zero times out of
    forty-five tool calls and still cost that. Capping it took a workspace's
    thread database from 2.94MB to 0.31MB across the same two turns.

    What that buys the deployment is the sandbox forgetting between calls: a
    value computed in one `eval` is gone by the next. Everything measured here
    did its whole calculation in a single call -- including the fan-out spike,
    whose loop runs inside one `eval` -- so nothing observed paid for the
    memory it was storing. A deployment that genuinely builds state across
    calls should raise this, and pay the 1,280KB a turn knowingly.

    There is no useful middle value. The image is a constant 1,280KB, so any
    cap under it drops everything and any cap over it keeps everything.

    Dispatching subagents from code needs the *async* path. `task()` inside the
    REPL awaits, so a sync `SqliteSaver` raises `does not support async
    methods` partway through a workflow that has already run. Use `arun` or
    `astream`; everything else here works on either. Undocumented upstream, and
    found by running it.

    Nothing has to be injected for that. This note used to say
    `async_checkpointer(cfg)`, which was true when a sync saver was the default
    and `astream` refused it -- and became advice to reach for the one shared
    database per workspace, which is the contention a database per session
    exists to avoid. `astream` now opens an async saver for the session itself.
    """
    # Deferred so that shipping the sandbox by default costs nothing to the runs
    # that never enable it. Measured, because the saving is smaller than it
    # looks: importing this standalone takes ~0.85s, but nearly all of that is
    # deepagents and langchain, which are loaded already. On top of kingfisher it
    # is ~15ms and ~6MB of resident memory -- worth deferring, not worth
    # restructuring anything else around.
    from langchain_quickjs import CodeInterpreterMiddleware  # noqa: PLC0415

    # Here rather than at import: the cap below is what makes the warning
    # inevitable, so the remedy belongs beside the cause.
    quieten_expected_snapshot_drop()

    # `None` here is the library's "no allowlist", which is what an
    # unrestricted request resolves to. A request that granted no tools gets an
    # empty list, which is the opposite and has to stay distinguishable.
    ptc: list[Any] | None = (
        None if permitted is None else [t for t in permitted if t != TASK_TOOL]
    )
    return CodeInterpreterMiddleware(
        # `task` is refused here by the library: it is always the top-level
        # `task()` global, and routing it through `tools.*` as well would give
        # two dispatch paths, the second losing `responseSchema`. Delegation is
        # governed by `subagents=` below instead.
        ptc=ptc,
        # Dispatch from code follows the same grant as dispatch from a tool
        # call. Left at its default this would let a request that withheld
        # `task` delegate anyway, from inside the sandbox -- a hole of exactly
        # the shape the delegate ceiling exists to close.
        subagents=permitted is None or TASK_TOOL in permitted,
        mode="thread",
        # Below any real snapshot, so every one is dropped. See the docstring:
        # the image is a constant 1,280KB written every turn regardless of use.
        max_snapshot_bytes=1,
        timeout=float(cfg.execution_timeout_s),
    )


def _denied_path(read_at: str) -> str:
    """One skill's own directory, as a rule the agent's routes can carry.

    The registry reads a catalogue through a backend rooted at the catalogue
    itself, so a skill's `path` is `/research/lookup/<file>` -- where the
    agent addresses that same file under `/skills/`. Two
    roots, two spellings, and a rule written in the wrong one is not merely
    wrong: `FilesystemMiddleware` refuses *every* permission when the backend
    can execute unless each rule is scoped to a route, so one unrouted path
    takes the whole deny list down with it. Found by a test doing exactly that.
    """
    return f"{SKILLS_ROUTE}{read_at.lstrip('/').rsplit('/', 1)[0]}/**"


def _skill_denials(activated: tuple[str, ...], registry: Any) -> list[FilesystemPermission]:
    """Deny reads of skills this request did not activate.

    The listing filter only stops the agent being *told*; this stops the file
    tools reading it anyway. Neither stops `execute`, which bypasses tool-level
    permissions entirely — so this is a real boundary only for a request that
    did not activate the shell.

    Built from each skill's own path rather than from its name, and that is the
    fix rather than a tidy-up. This wrote `/skills/{name}/**`, which is where a
    skill sits only while every skill sits at the top level. A skill in a folder
    lives at `/skills/research/lookup/`, so the rule denied a path that does not
    exist and the file tools could still read it -- a boundary failing open,
    silently, the moment folders were possible.
    """
    allowed = set(activated)
    return [
        FilesystemPermission(
            operations=["read"], paths=[_denied_path(one["path"])], mode="deny"
        )
        for key, one in registry.offered.items()
        if key not in allowed
    ]


def _backend_for(
    cfg: Config, session_dir: Path | None, backend: Any | None, catalogue: Definitions
) -> Any:
    """The filesystem an agent sees: rooted at a session, or supplied ready-made.

    Neither is a wiring mistake rather than a default worth guessing at. There
    is no sensible fallback: an agent rooted at the workspace instead of at a
    session would write one caller's files into a directory every other caller
    can read.

    A ready-made backend is taken as it is, catalogue included. It was built by
    whoever passed it, and re-routing `/skills/` underneath them would be a
    second answer to a question they already answered.
    """
    if backend is not None:
        return backend
    if session_dir is not None:
        return build_backend(cfg, session_dir, catalogue=catalogue)
    msg = "build_agent needs either a session_dir to root a backend at, or a backend"
    raise ValueError(msg)


def workspace_tool_names(
    cfg: Config, *, catalogue: Definitions | None = None
) -> tuple[str, ...]:
    """The tools this workspace defines, as a grant would write them.

    Knowable off disk, unlike the built-in set. That asymmetry is why the two
    axes resolve differently and why only one of them needs a probe.

    Written forms rather than bare names, because a bare list said `fetch,
    fetch` once two files could each define one -- which read as a workspace
    with a stutter rather than two tools a grant has to choose between.
    """
    found = (catalogue or Definitions.from_config(cfg)).tools.found
    return tuple(sorted(Offering.of(found).workspace))


def _refuse_shadowed(
    walked: Sequence[Found], *, builtin: tuple[str, ...], where: str
) -> None:
    """What the workspace defines, refusing anything that shadows a built-in.

    `tools_by_name` is a dict, so a workspace tool called `read_file` would take
    the name in silence and the real one would simply stop existing -- the same
    "quietly different from what you asked for" failure the capability checks
    refuse elsewhere. It matters more now that the two are granted separately: a
    shadowed name would be permitted by one axis and enforced as the other.

    Takes where they came from as text rather than the `Config` it used to
    derive a directory from: the only use is naming the place to go and rename
    them, and a catalogue that is not a directory can still say where it is.
    """
    shadowed = tuple(sorted({one.name for one in walked} & set(builtin)))
    if shadowed:
        msg = (
            f"workspace tool(s) {', '.join(shadowed)} would replace a built-in of "
            f"the same name; rename them in {where}"
        )
        raise CapabilityError(msg)


@dataclass(frozen=True)
class _ToolSurface:
    """The tool picture one build works from, resolved once.

    Four values rather than one flat allowlist, because a *delegate* narrows
    the two axes separately and cannot do that from their union: `tools:
    [http_fetch]` must cost it no built-in, which is only expressible while the
    halves are still apart. `permitted` puts them back together for the
    parent's own allowlist, which is one flat list by the time it reaches
    `ToolAllowlist`.

    The default is the skipped probe: nothing narrowed, nothing offered known,
    and `permitted` `None` for "no allowlist at all". Safe because the probe is
    only skipped when no definition names a tool either, so the `ALL`s below
    are never asked to enumerate anything.
    """

    #: What this build has to offer, or `None` when the probe was skipped.
    #:
    #: `None` rather than an empty `Offering`, because the two mean opposite
    #: things: nothing offered would narrow every grant to nothing, while a
    #: skipped probe means nothing was ever narrowed. The probe is only skipped
    #: when no definition names a tool either, so nothing below is ever asked to
    #: enumerate what it does not know.
    offering: Offering | None = None
    #: What the request asked for. Held so the grants can be *derived* rather
    #: than stored beside what they came from -- which is what this dataclass
    #: used to do, and it needed an `unrestricted` flag to compensate, because a
    #: stored `ALL` could no longer say whether everything was granted or
    #: nothing was narrowed. `permitted` answers that from the request directly.
    asked: Capabilities = field(default_factory=Capabilities)
    #: The built tool *objects*, by name, taken off the probe graph. Needed
    #: only for a helper: `SubAgentMiddleware` registers what a spec carries,
    #: and these are constructed from the backend inside `create_deep_agent`
    #: where nothing here can reach them -- except off an assembled graph,
    #: which is what the probe already is.
    objects: Mapping[str, Any] = field(default_factory=dict)
    #: Every workspace tool with the file it came from, kept because a grant no
    #: longer resolves to a name. Two files may each define a `fetch`, so what
    #: an agent registers has to be chosen as *objects* -- a name would pick one
    #: of the two out of a dictionary and lose the other before any narrowing
    #: ran.
    found: tuple[Found, ...] = ()

    @property
    def carried(self) -> tuple[Any, ...]:
        """The tool objects this agent registers: granted, minus the ambiguous.

        A delegate names which `fetch` it wants and gets it. The agent holding
        the grant cannot name anything -- it dispatches by name -- so a pair it
        cannot tell apart is left out and `ambiguous` says which.
        """
        if self.offering is None:
            return tuple(one.tool for one in self.found)
        return tuple(
            one.tool for one in self.offering.carried(self.granted_workspace, self.found)
        )

    @property
    def ambiguous(self) -> tuple[str, ...]:
        """Names granted to this run that only a delegate can ask for."""
        if self.offering is None:
            return ()
        return self.offering.ambiguous(self.granted_workspace, self.found)

    @property
    def offers(self) -> Offering:
        """The offering, or an empty one for the callers that only read names."""
        return self.offering or Offering()

    @property
    def permitted(self) -> tuple[str, ...] | None:
        """The parent's allowlist, or `None` for no restriction at all."""
        if self.offering is None:
            return None
        return self.offering.permitted(self.asked.builtin_tools, self.asked.tools)

    @property
    def granted_builtin(self) -> Selection:
        """The request's built-in grant, resolved against what was offered."""
        if self.offering is None:
            return ALL
        return narrowed(self.asked.builtin_tools, by=self.offering.builtin) or ()

    @property
    def granted_workspace(self) -> Selection:
        """The request's workspace grant, resolved against what was offered."""
        if self.offering is None:
            return ALL
        return narrowed(self.asked.tools, by=self.offering.workspace) or ()


def _tool_objects(graph: Any) -> Mapping[str, Any]:
    """The built tool objects a compiled graph dispatches, by name.

    `registered_tools` reads the same dict for its keys; a helper needs the
    values. `task` is excluded deliberately and not for tidiness: the harvested
    one is bound to *this* graph's delegate list, so handing it to a helper
    would let the helper reach every delegate the parent can. A delegate that
    may consult one gets a fresh `task` from its own `SubAgentMiddleware`.
    """
    node = getattr(graph, "nodes", {}).get("tools")
    by_name = getattr(getattr(node, "bound", None), "tools_by_name", None)
    if not isinstance(by_name, dict):
        return {}
    return {name: tool for name, tool in by_name.items() if name != TASK_TOOL}


def _wanted_endpoints(
    run_on: Mapping[str, RunOn] | None, activated: tuple[str, ...], granted: Selection
) -> Mapping[str, RunOn]:
    """Where this request wants delegates to run, once it may say so.

    Two refusals, both before anything is built and both raising rather than
    dropping. Elsewhere a narrower caller is quietly given less, because less
    is what they asked for; here the caller asked for the *cheap* model, and
    silently giving them the expensive one is the outcome nobody wants and
    nobody sees. Naming a delegate this request never activated is the same
    kind of mistake as naming an unknown tool.
    """
    wanted = dict(run_on or {})
    if stray := tuple(n for n in wanted if n not in activated):
        msg = (
            f"run_on names subagent(s) this request did not activate: "
            f"{', '.join(sorted(stray))}; it activated {activated}"
        )
        raise CapabilityError(msg)
    refuse_ungranted_models(
        (where.model for where in wanted.values()), granted=granted, subject="run_on"
    )
    return wanted


def _activated_subagents(
    cfg: Config,
    capabilities: Capabilities,
    session_dir: Path | None,
    *,
    catalogue: Definitions | None = None,
) -> tuple[Mapping[str, Any], tuple[str, ...]]:
    """Which delegates this request wired, and every definition available.

    Resolved before the tools rather than beside them, because whether any
    activated definition *names* a tool decides whether the tool probe has to
    run at all. Nothing here reads a tool, so the order costs nothing.
    """
    if capabilities.subagents is None:
        return {}, ()
    defined = defined_subagents(cfg, session_dir, catalogue=catalogue)
    # A property of the definitions, not of this request, so it is asked once
    # the merged set is known and before anything reads a single spec. An
    # upload can break it by shadowing a catalogue name, which is why it cannot
    # be checked at seed time and left at that.
    refuse_cycles(defined)
    # There is deliberately *no* matching check that every definition names a
    # runnable model. It was written and taken out again: the two rules look
    # alike and are not. Helper depth is structural -- a catalogue asking for
    # two levels is incoherent however it is used, and no request can rescue it.
    # An unrunnable model is not: `run_on` exists precisely so a caller can put
    # a shipped delegate on a model their credentials reach, without editing a
    # file they may not own, and a catalogue-wide refusal would fire before the
    # override could apply and defeat it.
    #
    # So it stays per-delegate, at `as_subagent`, where the override has already
    # been resolved -- and seeding a definition you cannot run costs nothing until
    # you activate it.
    # `ALL` is every subagent the workspace defines, resolved here because here
    # is where "what it defines" is known.
    activated = tuple(defined) if capabilities.subagents == ALL else capabilities.subagents
    refuse_unoffered(activated, offered=defined, kind="subagent", subject="this request")
    # After the unknown-name check, not before: naming one that does not exist
    # and naming two that do are different mistakes, and the first would
    # otherwise be reported as the second.
    refuse_two_of_a_name(activated, subject="this request")
    return defined, activated


def _resolve_tools(
    # answers came from; folding them up would hide what each one is for.
    where: str,
    capabilities: Capabilities,
    workspace_tools: Sequence[Found],
    assemble: Callable[[tuple[Any, ...]], CompiledStateGraph],
    *,
    names_needed: bool = False,
) -> _ToolSurface:
    """What this request may call, and what this agent offers at all.

    Costs a throwaway assembly, and only when it has to. Both offered sets must
    be known to resolve `ALL` on either axis, and only one can be read off disk:
    the built-in set is a property of an assembled graph. A request that narrows
    neither axis, in a workspace that defines no tools, skips it entirely.

    `names_needed` is the fourth job: a *definition* naming tools has to be
    checked against what exists, and what exists includes the built-in set. A
    caller asks for it only when some activated definition actually names one.
    Measured on a build with one delegate: 1 compile and 12.6ms when it names
    no tools, 2 and 20.3ms when it does -- so the probe is the ~7.7ms, and the
    runs that never needed it still skip it.

    The same probe the shadow check has always needed, doing four jobs now
    rather than one.
    """
    unrestricted = capabilities.builtin_tools == ALL and capabilities.tools == ALL
    if not names_needed and not workspace_tools and unrestricted:
        return _ToolSurface()

    probe = assemble(())
    # Our own probe, so `None` is not reachable here; `or ()` keeps a shape
    # change upstream from becoming a crash at the one site that would.
    builtin = registered_tools(probe) or ()
    _refuse_shadowed(workspace_tools, builtin=builtin, where=where)
    offering = Offering.of(workspace_tools, builtin=builtin)
    offering.refuse_unknown(
        capabilities.builtin_tools, capabilities.tools, subject="this request"
    )
    return _ToolSurface(
        offering=offering,
        asked=capabilities,
        objects=_tool_objects(probe),
        found=tuple(workspace_tools),
    )


def build_agent(  # noqa: PLR0913, PLR0915 -- the composition root; each argument
    # is one injectable collaborator, and the body is the wiring itself: every
    # statement attaches one thing to the graph, so splitting it would move the
    # wiring somewhere a reader has to go and find rather than shortening it.
    cfg: Config,
    *,
    capabilities: Capabilities | None = None,
    session_dir: Path | None = None,
    middleware_registry: Mapping[str, Callable[[], Any]] | None = None,
    model: Any | None = None,
    backend: Any | None = None,
    checkpointer: Any | None = None,
    catalogue: Definitions | None = None,
    run_on: Mapping[str, RunOn] | None = None,
    workspace_tools: Sequence[Found] | None = None,
    agent: AgentSpec | None = None,
) -> CompiledStateGraph:
    """Wire model, backend and checkpointer into a deep agent.

    `session_dir` is where the backend roots, so an agent belongs to one
    session and cannot be reused across them.

    `catalogue` is where the shared skills, subagents and tools are read from,
    as `{"skills": …, "subagents": …, "tools": …}`. Omitted, it is derived from
    `cfg`, which is what it has always been -- the same fallback `model=` takes,
    and for the same reason: derive from `cfg`, or raise, but never invent. A
    deployment staging its definitions somewhere else resolves them once at
    construction and passes the result down.

    deepagents 0.7.6 ships no planning tool, so `TodoListMiddleware` is added
    explicitly. Its default prompt fragment is kept rather than trimmed: it
    already carries anti-overuse guidance and a finishing convention that would
    only be rewritten worse.
    """
    capabilities = capabilities or Capabilities()
    if agent is not None:
        # The agent file is the baseline and the request only ever subtracts
        # from it. One lattice, applied in the one direction it already goes:
        # what a caller asks for cannot exceed what the definition declared.
        capabilities = agent.declares.intersect(capabilities)
    roots = catalogue or Definitions.from_config(cfg)
    resolved_backend = _backend_for(cfg, session_dir, backend, roots)
    # Unconditional: the backend rejects host paths on every run, so the
    # thing that turns that rejection into a correction must always be here.
    middleware: list[Any] = [TodoListMiddleware(), HostPathGuard()]
    permissions = [DATA_IS_READ_ONLY, SKILLS_ARE_READ_ONLY]
    extras: dict[str, Any] = {}

    # Two axes, and this is where they meet: `cfg` says what is wired, the
    # request says what it wants of that. Narrowing can only subtract --
    # `memory=True` against a deployment that wired none stays off.
    if cfg.memory_enabled and capabilities.memory is not False:
        extras["memory"] = MEMORY_SOURCES
    elif cfg.memory_enabled:
        # Wired but declined. The prompt still describes memory, because it is
        # the cached prefix and must not vary per request; this stops the file
        # being read anyway. deepagents puts memory behind its own cache
        # breakpoint, so dropping the block leaves the prefix cached.
        permissions.append(MEMORY_IS_DENIED)

    if cfg.skills_enabled:
        registry = activatable_skills(cfg, session_dir, catalogue=roots)
        # One source per folder, so a skill below the top level is visible at
        # all -- and labelled the way the registry labelled it, because a label
        # is the first half of what a request grants.
        sources = skills_sources(registry.folders)
        if capabilities.skills == ALL:
            extras["skills"] = sources
        elif capabilities.skills is None:
            pass  # none: no index, and no deny rules to write for one
        else:
            # Each grant to the one skill it means. A bare name that two sources
            # both offer is refused here rather than resolved, because resolving
            # it is exactly the silent pick this exists to stop.
            activated = tuple(registry.resolve(one) for one in capabilities.skills)
            # Supplied as middleware rather than via `skills=`: passing that
            # argument makes deepagents construct its own SkillsMiddleware,
            # leaving no way to substitute a filtered one.
            middleware.append(
                NarrowedSkills(
                    allowed=activated,
                    backend=resolved_backend,
                    sources=sources,
                )
            )
            permissions.extend(_skill_denials(activated, registry))

    interpreter_at: int | None = None
    if cfg.interpreter_enabled:
        # Unrestricted for now: the probe below has to see `eval` to count it
        # among the built-ins, and the grant is not resolved until after it.
        interpreter_at = len(middleware)
        middleware.append(_interpreter(cfg, None))

    # What this agent runs, resolved once: the id for the delegates below --
    # `distinct` is measured against whatever summoned a delegate, and at the
    # top that is the agent -- and the instance for the graph itself. An
    # injected `model=` still wins, since a test handing one in has said the
    # catalogue is not the subject.
    agent_model_id = model_for(agent, cfg) if agent is not None else None
    agent_model = (
        model_object(agent, cfg, endpoints=capabilities.endpoints)
        if agent is not None
        else None
    )
    running = model or agent_model or build_model(*cfg.models.resolve())

    def assemble(extra_tools: tuple[Any, ...]) -> CompiledStateGraph:
        return create_deep_agent(
            model=running,
            backend=resolved_backend,
            system_prompt=system_prompt(cfg, agent.system_prompt if agent else ""),
            middleware=middleware,
            permissions=permissions,
            checkpointer=checkpointer,
            tools=list(extra_tools) or None,
            **extras,
        )

    # The catalogue walked these when the deployment was wired; a caller that
    # has already walked them itself -- `--list` -- still wins.
    walked = tuple(roots.tools.found if workspace_tools is None else workspace_tools)

    # Appended here rather than beside `HostPathGuard` above, because it needs
    # the names and they are not known until now. `assemble` closes over the
    # list, so anything added before it runs is in the built agent.
    #
    # Every walked tool, not the granted ones: a request that activated none of
    # them cannot reach one, and narrowing this to the grant would mean building
    # the guard from a set that is computed after it.
    if walked:
        middleware.append(WorkspaceToolErrors(frozenset(entry.name for entry in walked)))

    defined, activated = _activated_subagents(cfg, capabilities, session_dir, catalogue=roots)
    surface = _resolve_tools(
        source_of(roots.tools),
        capabilities,
        walked,
        assemble,
        # Either list naming anything is enough: both are checked against their
        # own offered set, and neither set is knowable without the probe.
        # Either tool list naming anything needs the offered sets. A delegate
        # naming a helper needs the built tool *objects*, which come off the
        # same probe -- so wanting one is equally a reason to run it.
        names_needed=any(
            defined[n].tools not in (ALL, None)
            or defined[n].builtin_tools not in (ALL, None)
            or defined[n].subagents is not None
            for n in activated
        ),
    )
    permitted = surface.permitted

    if interpreter_at is not None and permitted is not None:
        # Re-wired now that the union is known. It had to be in place for the
        # probe -- `eval` is a tool, so a request naming it needs it in the
        # enumerated set -- but unrestricted, since the grant was not resolved
        # yet. A caller that withheld the shell must not reach it from code.
        middleware[interpreter_at] = _interpreter(cfg, permitted)

    if capabilities.subagents is not None:
        offered = available_skills(cfg, session_dir, catalogue=roots)
        registry = middleware_registry or {}
        for name in activated:
            subject = f"subagent {name!r}"
            surface.offers.refuse_unknown(
                defined[name].builtin_tools, defined[name].tools, subject=subject
            )
            # After the unknown-name check, so a definition naming `csv_column`
            # hears that the name is wrong rather than that it has moved. The
            # catalogue's own definitions had their paths checked at
            # construction; this is what covers one a request uploaded.
            surface.offers.refuse_moved(defined[name].tool_sources, subject=subject)

        wanted = _wanted_endpoints(run_on, activated, capabilities.models)

        def _built(
            name: str,
            *,
            helpers: list[Any] | None = None,
            default_model: Any = None,
            tool_objects: list[Any] | None = None,
            caller: str | None = None,
        ) -> dict[str, Any]:
            """One delegate, with the request's ceiling on every axis.

            `helpers` is whatever this delegate names, built first. The
            recursion is the feature: delegation nests to any depth, and what
            stops it running forever is `refuse_cycles` on the catalogue rather
            than a bound here. This used to omit `helpers` for a helper, and
            that omission *was* the depth bound.

            A helper is otherwise built exactly like any other delegate: its own
            tools, its own skills, its own endpoint, each clamped by what the
            *request* granted rather than by the delegate that reached it. The
            caller had to name it too, so the caller has already seen it.

            `caller` is the one thing it does take from above: the model the
            delegate that summoned it is running, which is what it inherits when
            it names none and what `distinct` refuses to match.
            """
            return as_subagent(
                defined[name],
                cfg,
                backend=resolved_backend,
                endpoints=capabilities.endpoints,
                builtin_tools=surface.granted_builtin,
                tools=surface.granted_workspace,
                skills=subagent_skills(defined[name], offered, capabilities.skills),
                skill_sources=skills_sources(roots.registry.folders),
                helpers=helpers,
                default_model=default_model,
                caller=caller,
                tool_objects=tool_objects,
                catalogue=walked,
                run_on=wanted.get(name),
                extra_middleware=subagent_middleware(
                    defined[name], registry, capabilities.middleware
                ),
            )

        # One compiled agent per definition, however many places it appears.
        # Not an optimisation: compiling per *path* is exponential in the shape
        # of the catalogue, and a catalogue with no cycle at all can describe an
        # enormous number of paths. Measured -- 15 definitions each naming three
        # is 6,872 compilations and seven seconds, twenty is two and a half
        # minutes. Compiled once each, the same catalogue is twenty.
        #
        # Safe because `refuse_cycles` already ran: a definition cannot be
        # in-flight when it is asked for again, so this needs no re-entry guard.
        # Keyed by name *and position*, because the two are not the same agent.
        # A delegate the request activated is registered by `create_deep_agent`
        # and inherits its model and its built-in tools; one nested inside
        # another is built by `SubAgentMiddleware` and inherits nothing --
        # deepagents refuses a nested spec with no `model` outright.
        #
        # So a definition used in both places compiles twice, which is still
        # two per definition rather than one per path. Handing the explicit
        # model and tools to a top-level delegate instead would work and would
        # cost it the inheritance: it would stop tracking a parent that changed.
        #
        # The summoner's model is part of the key for the same reason position
        # is: a definition naming no model runs whatever reached it, so `checker`
        # under a cheap parent and `checker` under an expensive one are two
        # different agents wearing one name. Bounded by definitions times the
        # models above them, which is a catalogue's own shape rather than the
        # number of paths through it.
        compiled: dict[tuple[str, bool, str | None], Any] = {}

        # What the main agent itself runs, as an object a helper can be handed.
        # A top-level delegate needs none of this -- deepagents gives it the
        # agent's own model -- but `SubAgentMiddleware` gives a nested one
        # nothing, and deepagents refuses a nested spec with no model at all.
        root = running

        def _with_helpers(
            name: str, *, nested: bool, inherited: Any = None, caller: str | None = None
        ) -> Any:
            key = (name, nested, caller)
            if key not in compiled:
                # This delegate's own model, before its helpers rather than
                # after, because they inherit it. `model_for` is the same call
                # `as_subagent` makes below and answers identically; asking here
                # only moves *when* an unusable model is refused, from part-way
                # through building a tree to before it starts.
                override = wanted.get(name)
                own = model_for(defined[name], cfg, override=override, caller=caller)
                mine = model_object(
                    defined[name],
                    cfg,
                    endpoints=capabilities.endpoints,
                    run_on=override,
                    inherited=inherited,
                    caller=caller,
                )
                helpers = [
                    _with_helpers(
                        helper,
                        nested=True,
                        # Its parent's model, which is what "runs whatever
                        # summoned it" means one level down. This was the main
                        # agent's, so a helper under a delegate pinned to the
                        # cheap model quietly ran the expensive one.
                        inherited=mine if mine is not None else root,
                        caller=own if own is not None else caller,
                    )
                    for helper in subagent_helpers(
                        defined[name], defined, capabilities.subagents
                    )
                ]
                compiled[key] = _built(
                    name,
                    helpers=helpers or None,
                    default_model=inherited if nested else None,
                    caller=caller,
                    tool_objects=list(surface.objects.values()) if nested else None,
                )
            return compiled[key]

        extras["subagents"] = [
            _with_helpers(n, nested=False, caller=agent_model_id) for n in activated
        ]

    if permitted is not None:
        middleware.append(ToolAllowlist(permitted))
        # deepagents supplies a `general-purpose` delegate with "the same
        # capabilities as the main agent" and none of our middleware, present
        # whenever `task` is. Supplying one by the same name *replaces* it --
        # the specs are keyed by name -- so it keeps working and arrives with
        # the caller's ceiling on it, rather than being withheld.
        #
        # Their spec, our middleware: the description and prompt are tuned and
        # there is no reason to reinvent either.
        supplied = list(extras.get("subagents", ()))
        supplied.append(
            {**GENERAL_PURPOSE_SUBAGENT, "middleware": [ToolAllowlist(permitted)]}
        )
        extras["subagents"] = supplied
        # Backstop. Only these names are reachable, so a delegate deepagents
        # adds in some future version does not silently arrive unrestricted.
        reachable = tuple(spec["name"] for spec in supplied)
        middleware.append(DeclaredDelegatesOnly(reachable))

    return assemble(surface.carried)
