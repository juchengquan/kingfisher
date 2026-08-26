"""What the domain needs the world to do for it.

Narrow by intention. A port earns its place only where a rule genuinely
depends on a primitive: `allocate_turn` is atomic *because* `mkdir` fails on an
existing name, and expressing that as "scan, then create" in a caller would
reintroduce the race the loop exists to avoid. Where no primitive is load-
bearing, the domain returns a decision instead and the caller acts on it --
`retention.expired` names the sessions to drop and touches nothing.

Protocols rather than base classes: an adapter satisfies these by shape, and
a test satisfies them with a dict.

Two kinds of port live here, and the rule above is only about the first.

* **Primitives**, above -- `SessionDirs`, `ThreadStore` -- exist because a rule
  depends on what the operation guarantees.
* **Repositories** -- `AssetRepository` and its three kinds -- exist because a
  deployment may hold its definitions somewhere kingfisher did not choose. They
  earn their place by being *swapped*, not by being depended on, and they are
  narrow for a different reason: the port carries only what a replacement must
  provide. Everything a local directory can also answer -- where a definition
  sits on disk, which folders hold one too deep to load -- stays on the local
  implementation, because a store that is not a directory has no answer to give
  and should not be made to pretend.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from kingfisher.domain.agent import AgentSpec
from kingfisher.domain.subagent import SubagentSpec
from kingfisher.domain.tool import Found


@runtime_checkable
class AssetRepository(Protocol):
    """Something a deployment's definitions can be read from.

    One member, because one is all four kinds have in common. The capability
    layer filters skills, subagents and tools by *name* and by nothing else --
    and an agent is *chosen* by name rather than filtered, which wants the same
    listing -- so `names` is the whole shared vocabulary; what a kind is made of --
    a directory listing, parsed documents, imported Python -- differs so
    completely that a shared `load` would unify the word and none of the
    meaning.

    A property rather than a method because reading is what these are for, and
    because an implementation is expected to read once and answer from that.
    `cached_property` satisfies this exactly, which is how the local ones do it.
    """

    @property
    def names(self) -> tuple[str, ...]:
        """Every definition held, by the name a request grants it, in a stable order.

        Stable because the agent is built from this. Two processes reading the
        same definitions must offer the model the same list in the same order,
        or a prompt differs between them for no reason a reader could find.
        `available_skills` used to sort at the call site, which only worked
        while there was one implementation to sort.
        """
        ...


@runtime_checkable
class SkillRepository(AssetRepository, Protocol):
    """Skills: their names, and the files each one is made of.

    kingfisher never *parses* a skill -- deepagents opens them itself, through a
    backend route -- so for a long time `names` was the whole port and the files
    were assumed to be a directory somewhere. That assumption was the one thing
    stopping a deployment from holding its skills anywhere else, because a route
    needs file contents and a name cannot supply them.

    `files` is what closes that. A repository that can hand over a skill's
    contents can be mounted for the agent to read, whatever it is backed by.

    Text, not bytes, and that is worth saying because the neighbouring
    `DefinitionStore.fetch` answers in bytes. It has a reason to: `uploads`
    writes what it returns straight to disk with `write_bytes`, so anything a
    caller uploads has to survive the trip. Nothing does that here -- a skill is
    read, never re-written -- and the one mount decoded immediately, so bytes
    were a round trip dressed as symmetry.
    """

    def files(self, name: str) -> Mapping[str, str]:
        """The files making up one skill, keyed by path relative to the skill.

        `skill.FILENAME` is always among them -- it is what makes a directory a
        skill -- and anything else the skill ships travels with it: scripts,
        templates, data. Raises `KeyError` for a name this does not hold.

        A skill is text: a definition the agent reads and scripts it runs. One
        shipping something genuinely binary is decoded lossily rather than
        refused, because failing a whole catalogue over one stray image is the
        worse trade -- and a binary asset is unusable through a store mount
        either way.
        """
        ...


@runtime_checkable
class AgentRepository(AssetRepository, Protocol):
    """Agent definitions, parsed.

    The same shape as `SubagentRepository` and free of the filesystem for the
    same reason: an agent is a document, and `read_agent` takes text.

    The one kind with no session layer over it. A request may upload skills and
    subagents because those are its own text; an agent decides where every
    prompt in the session goes and is pinned for that session's whole life, so
    it comes from the catalogue or not at all.
    """

    @property
    def specs(self) -> Mapping[str, AgentSpec]:
        """Every agent defined here, by name."""
        ...


@runtime_checkable
class SubagentRepository(AssetRepository, Protocol):
    """Subagent definitions, parsed.

    Free of the filesystem in the same way, for a different reason: a
    definition is a document, and `read_subagent` takes text. Where the text
    came from is the implementation's business.
    """

    @property
    def specs(self) -> Mapping[str, SubagentSpec]:
        """Every subagent defined here, by name."""
        ...


@runtime_checkable
class ToolRepository(AssetRepository, Protocol):
    """Workspace tools, imported, each with the file it came from.

    The one kind that cannot escape the host filesystem, and it is worth saying
    why here rather than leaving it to be rediscovered: a tool is Python that
    gets *imported*, and `importlib.spec_from_file_location` needs a real file.
    An implementation backed by anything else has to stage to disk first. That
    is a constraint on the implementation, not on this port -- what a caller
    receives is still the loaded objects.
    """

    @property
    def found(self) -> tuple[Found, ...]:
        """Every tool held, paired with where it is defined."""
        ...


@runtime_checkable
class ThreadStore(Protocol):
    """The checkpointer, seen from the domain: something that forgets a thread."""

    def delete_thread(self, thread_id: str) -> None: ...


@runtime_checkable
class SessionDirs(Protocol):
    """The directories a session and its turns live in.

    `create_exclusive` is the reason this port exists. It must fail rather than
    succeed when the name is taken, because that failure *is* how concurrent
    turn allocation stays correct.
    """

    def ensure(self, path: Path) -> None:
        """Create `path` and any parents. Succeeds if it already exists."""
        ...

    def create_exclusive(self, path: Path) -> bool:
        """Create `path`, or return False if something already holds the name."""
        ...

    def mark_used(self, path: Path) -> None:
        """Record that `path` was used just now.

        A port because the rule depends on it. `retention.expired` names
        sessions "untouched for longer than X" and reads one timestamp to
        decide -- and a turn writes *inside* a session, into `runs/` and
        `derived/`, which on an ordinary filesystem leaves the session's own
        timestamp alone. Measured: a session was still 10,000s idle by that
        clock immediately after a turn completed in it.
        """
        ...

    def children(self, path: Path) -> tuple[str, ...]:
        """Names of the directories directly inside `path`."""
        ...

    def listing(self, path: Path) -> tuple[tuple[str, float], ...]:
        """`(name, modified_at)` for each directory inside `path`."""
        ...

    def remove_tree(self, path: Path) -> str | None:
        """Delete `path` and its contents. Returns a reason on failure."""
        ...


@runtime_checkable
class DefinitionStore(Protocol):
    """Where a request's own skills and subagents are fetched from, by id.

    One port for both, because they differ in where they land rather than in
    how they arrive. A skill is several files and a subagent is one, so the
    return is a mapping of relative path to bytes either way.

    Ids stop here. They are how a catalogue service names a definition; what
    the agent sees is the name inside the definition, and `capabilities`
    activates by that name. Neither vocabulary reaches the other.
    """

    def fetch(self, definition_id: str) -> Mapping[str, bytes]:
        """The files making up one definition, keyed by path relative to it."""
        ...


class SessionStore(Protocol):
    """Where a session's files live when the machine may not keep them.

    The missing half of a symmetry this package already commits to. `FileStore`
    and `DefinitionStore` are how bytes arrive: *"a remote caller has no host
    paths... a store the deployment wired resolves it."* Bytes leaving had no
    such door -- `artifacts()` hands back a list of paths and the caller opens
    them off the host, which only works for a caller sharing that host.

    That asymmetry is invisible while a deployment may keep files on its own
    disk. It is the whole problem when it may not: a session's directory is
    gone when the process is, and what a turn produced has to be somewhere the
    next turn can find it.

    **A local directory is a perfectly good implementation of this port.** That
    is the point rather than a concession -- what the constraint forbids is
    kingfisher *assuming* a local disk, not a deployment choosing one. The same
    interface fronts a bucket, a database, or a directory, and kingfisher does
    not know which.

    Keys are paths relative to the session root, the same vocabulary
    `artifacts()` already returns and for the same reason: a caller diffing two
    turns needs names it can compare, and an absolute path names a machine.

    Ids stop here, as they do for the other two. What the agent sees is
    `/derived/report.md`; how a store spells that is its own business.
    """

    def fetch(self, session_id: str) -> Mapping[str, bytes]:
        """Everything this session kept, keyed by path relative to its root.

        Empty for a session the store has never seen, which is not an error: a
        first turn has nothing to restore, and refusing here would make the
        common case the exceptional one.
        """
        ...

    def save(self, session_id: str, files: Mapping[str, bytes]) -> None:
        """Keep these files against this session, replacing any it already had.

        The *changed* ones, not all of them. A caller that sends everything each
        time is correct and pays for the whole session on every call; the
        interface does not police that, because which files changed is something
        only the caller can know cheaply.

        Deleting is `save` with the key absent from a later `fetch`, which this
        port deliberately cannot express -- see `forget`.
        """
        ...

    def knows(self, session_id: str) -> bool:
        """Whether this store holds anything for this session.

        Existence, asked cheaply. `fetch` answers the same question by handing
        back every byte, which is the wrong price for a boolean when the store
        is somewhere else.

        It is a *security* question rather than a convenience one. A supplied
        session id may resume and may not create — *"the id is what proves a
        session is the caller's, and it is proof only because it cannot be
        chosen"* — and that proof used to be a directory. Where the machine may
        not keep directories, this is what is left to ask, and it holds for the
        same reason: a caller cannot make a store know an id it never saved.
        """
        ...

    def forget(self, session_id: str) -> None:
        """Drop everything kept for this session. Idempotent.

        Separate from `save` because deletion is the one operation a caller must
        be unable to perform by accident. `save` merges; only this removes, and
        it removes a whole session rather than a file, which is the only
        granularity `reap` ever needs.
        """
        ...


class FileStore(Protocol):
    """Where a request's files are fetched from, by id.

    The same shape as `DefinitionStore` and for the same reason. A remote caller
    has no host paths, so `Request.inputs` and `data` cannot express what they
    want -- they name an id instead, and a store the deployment wired resolves
    it. Kingfisher never receives bytes over its own wire and never holds them
    beyond the turn that asked.

    A mapping rather than plain bytes, so one ref may name a small bundle and
    the two ports read alike. The keys are paths relative to wherever the files
    land, and a caller-supplied key is exactly what `layout.within` refuses to
    let escape.

    Ids stop here, as they do for definitions: what the agent sees is a filename
    in `/data` or the turn's `input/`, never the id it was fetched by.
    """

    def fetch(self, file_id: str) -> Mapping[str, bytes]:
        """The files this reference names, keyed by path relative to it.

        Raises `references.UnknownReferenceError` for a ref it cannot resolve
        and `references.UnsafeReferenceError` for one that names somewhere it
        was not allowed to. Part of the contract rather than each adapter's own
        choice: a bare `FileNotFoundError` cannot be told from the deployment's
        own disk being wrong, and would answer 500 to a caller's typo.
        """
        ...


@dataclass(frozen=True)
class CommandResult:
    """What running one command produced.

    Kingfisher's own vocabulary rather than the harness's. The framework has a
    type of the same shape, and using it here would put the framework into a
    contract a deployment implements -- which is the whole reason only *running*
    a command is delegated and file access is not.

    `exit_code` is not optional. The harness's equivalent allows `None`, and a
    caller deciding whether a command worked has nothing to do with that but
    guess.
    """

    output: str
    exit_code: int
    #: Set when `output` is not all of it. A runner that cuts long output says
    #: so, because a caller cannot tell a truncated result from a short one.
    truncated: bool = False


class CommandRunner(Protocol):
    """How a shell command is run, for a deployment that runs them elsewhere.

    One method, and no path in it. The shell backend it sits behind is also the
    filesystem for everything unrouted -- ten operations it inherits -- so
    handing over "the shell" would hand over file access with it, which belongs
    to whoever supplies the session directory instead.

    `command` arrives already confined **when the runner is local**, which is the
    default. Applying the confinement stays on kingfisher's side so a runner
    cannot forget to -- but the confinement names paths on *this* host, so a
    runner that ships the command elsewhere must say so by setting `local` to
    False, and then receives the command as the model wrote it.
    """

    #: Whether the command runs on this machine.
    #:
    #: Declared rather than inferred, and required of anything type-checked
    #: against this protocol -- which is the right way round for a flag that
    #: decides whether a fence is applied. Saying where your commands run is a
    #: sentence worth writing. An object that never meets a type checker still
    #: gets the safe answer: this is read with a default of True, so forgetting
    #: it yields *more* confinement than needed, never less.
    #:
    #: The case this exists for is not the remote one. A runner that adds
    #: resource limits, or runs as another user, or records timings, is still
    #: here -- and under a rule of "a supplied runner means no local fence" every
    #: one of those would quietly lose `sandbox-exec` on macOS. Losing a fence
    #: without being asked is the failure this whole seam is about.
    local: bool = True

    def run(self, command: str, *, timeout: int | None = None) -> CommandResult:
        """Run `command`, giving up after `timeout` seconds if one is given.

        A timeout is a result, not an exception: `exit_code` 124, the shell's
        own, with output saying so. Raising would make every runner's failure
        the model's problem rather than a tool result it can read and retry.
        """
        ...


class SessionRoot(Protocol):
    """Where one session's files are, for the length of one turn.

    Named for what `build_backend` already calls it -- *"a session directory is
    the backend root"* -- because that is the whole of what this hands back: the
    one directory the agent addresses everything from. `SessionDirs` is the
    neighbour it is easy to confuse this with, and the difference is worth
    holding: that one is the *rules* about session directories (create one
    exclusively so two turns cannot claim it, mark it used, list what is
    inside), and this one is *where the directory is*.

    A directory, not a backend. The file tools and the shell are two views of
    one directory -- `/data/x.csv` through a tool and `data/x.csv` through `cat` are
    the same bytes -- and the harness cannot tell a plain directory from a
    mount: it resolves the root once and checks containment per access. So a
    provider returns a path and never imports the harness at all.

    The one rule that follows: **a symlink out of the root is refused**, because
    that containment check resolves before it compares. A session cannot be
    composed out of links to shared content; it has to be a real directory, or
    a mount that presents as one.

    One turn, because a session here deliberately spans machines -- that is what
    the store is for -- and a mount held between turns assumes the process that
    made it is still there for the next one. Held as a context manager so that
    what was mounted is released when the turn ends, including when it ends
    badly.

    Kingfisher creates the layout inside what it is handed. A provider that had
    to create `data`, `memory` and the rest would be a provider that breaks
    every time this repository adds a directory.

    **Nothing here is ever closed by kingfisher.** Whoever constructs one owns
    shutting it down, which is the rule `threads` already states -- *"an instance
    is a shared store the deployment made and manages"* -- and it applies for the
    same reason: kingfisher does not decide when the service stops, so it cannot
    be the one that decides when a connection to the storage does. Two owners of
    one lifetime is what that rule exists to prevent.

    Two lifetimes, then, and only one of them is kingfisher's. Anything set up
    per *turn* belongs inside `hold`, which closes on the way out however the
    turn ended. Anything set up when the provider was *built* -- a connection
    pool, a thread, a mount made once at startup -- is released by the
    deployment that built it, or by the process exiting.
    """

    def hold(self, session_id: str) -> AbstractContextManager[Path]:
        """The directory this session's turn runs in, for as long as it runs.

        Called once per turn, before anything reads or writes the session --
        restoring from the store writes into it, and keeping from it reads it
        afterwards, so both happen inside.
        """
        ...
