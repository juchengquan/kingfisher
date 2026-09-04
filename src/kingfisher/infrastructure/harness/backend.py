"""Filesystem + shell backend.

`LocalShellBackend` defaults to `inherit_env=False` with `env=None`, which means
`subprocess.run(..., env={})` — no variables at all, not even `PATH`. That is a
good security default and a fatal usability one: nothing resolves. We keep the
default and supply an explicit allowlist instead of inheriting the parent
environment, so the agent's shell can run the toolchain but cannot read
credentials out of the environment.

The environment allowlist is not by itself a boundary, which is what
`confinement` adds: `execute` otherwise reaches the whole host filesystem
regardless of `virtual_mode`, and the network is still open either way.
"""

from __future__ import annotations

import os
import stat
import sys
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from deepagents.backends import CompositeBackend, FilesystemBackend, LocalShellBackend
from deepagents.backends.protocol import ExecuteResponse
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from kingfisher.config import Config, ConfigError
from kingfisher.domain.layout import (
    AGENT_HOME,
    SESSION_DIRS,
    SESSION_PLUMBING,
    UPLOADED_SKILLS,
)
from kingfisher.domain.ports import CommandRunner
from kingfisher.domain.references import UnsafeReferenceError, within
from kingfisher.domain.subagent import SubagentError
from kingfisher.infrastructure.catalogue import Definitions, catalogue_root
from kingfisher.infrastructure.sandbox import confinement
from kingfisher.skills.backend import skills_backend

if TYPE_CHECKING:

    from deepagents.backends import BackendProtocol

_BASE_PATH: tuple[str, ...] = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")


def agent_home(session_dir: Path) -> Path:
    """`HOME` for the agent's shell: per session, and disposable.

    Not a real home directory but the place tools *believe* is one, so it fills
    up with whatever they cache -- `~/.cache/uv`, `~/Library/Caches/pip`. It
    used to be the workspace root, where three things went wrong at once. The
    caches accumulated beside `skills/` and `subagents/` and nothing ever swept
    them (59MB in one real workspace); `Library/` was untracked but *not*
    ignored, so a `git add -A` in a workspace would commit a pip cache; and none
    of it counted toward `session_max_bytes`, because the quota measures a
    session and this sat above every session.

    Inside the session fixes all three without a new janitor: `reap` already
    removes session directories, and `session_bytes` already counts everything
    in one. A session that caches a gigabyte now says so.

    Dotted, and not in `SESSION_DIRS`, because those are "the names the agent
    addresses" and this is plumbing. It is reachable at `/.home` -- the shell
    backend roots at the session -- which is harmless and not worth a route.

    The name is `layout.AGENT_HOME`, beside the rest of a session's names.
    Spelling it here as well is how this directory came to be created in one
    file and listed in none.
    """
    return Path(session_dir) / AGENT_HOME


def shell_env(
    cfg: Config, session_dir: Path, *, catalogue: Definitions | None = None
) -> dict[str, str]:
    """The explicit allowlist handed to the shell — no credentials.

    `HOME` is this session's `.home`, so tools that resolve `~` look there
    instead of at `~/.aws`, `~/.ssh` or `~/.config`.

    That is all it does, and it used to be described as more. Redirecting `HOME`
    moves where a path is *resolved*; the files stay where they are, and an
    absolute path still reached them. Measured on this machine, the shell could
    read `~/.aws` and `~/.config/gh` right through it. Keeping those closed is
    `confinement`'s job, not this function's.

    `KINGFISHER_SKILLS` is here because the catalogue is the one virtual path
    the shell cannot reach by dropping its leading slash: it is shared between
    sessions, so it lives above them. That used to be spelled `$HOME/skills`,
    which was only true while `HOME` was the workspace.

    It follows `catalogue` for the same reason the sandbox profile does. Left on
    `cfg` while the route and the profile pointed elsewhere, a skill's own
    scripts would be told to look in a directory the deployment had moved away
    from -- and `$KINGFISHER_SKILLS` is exactly how a skill addresses them.
    """
    path_parts = [str(Path(sys.executable).parent), *cfg.shell_path_extra, *_BASE_PATH]
    env = {
        "PATH": ":".join(path_parts),
        "HOME": str(agent_home(session_dir)),
        "LANG": "en_US.UTF-8",
        "LC_ALL": "en_US.UTF-8",
        "TMPDIR": str(cfg.scratch_dir),
    }
    # Only when there is a directory to name. A catalogue held in a store is
    # readable by the file tools -- `skills.backend` mounts it -- but a skill's
    # *scripts* are run by the shell, and a store has no path for the shell to
    # reach. Setting this to something that is not there would turn "this
    # deployment cannot run skill scripts" into `no such file or directory` on
    # a path the operator never configured. Absent, `sh "$KINGFISHER_SKILLS/x"`
    # fails immediately and says the variable is unset, which is the truth.
    root = catalogue_root((catalogue or Definitions.from_config(cfg)).skills)
    if root is not None:
        env["KINGFISHER_SKILLS"] = str(root)
    return env


#: Absolute prefixes that can never name a workspace directory, so a file-tool
#: path starting with one is a host path that was passed to the wrong kind of
#: tool. Deliberately a short, explicit list rather than a rule inferred from
#: the filesystem: it has to be readable, and it has to be the same on every
#: machine regardless of what happens to exist at `/`.
_HOST_ROOTS: tuple[str, ...] = (
    "/Users/",
    "/home/",
    # S108 reads a "/tmp" literal as insecure temp-file use. This is the
    # inverse: an entry in a deny-list, naming the prefix a file tool must
    # refuse. Nothing is written here.
    "/tmp/",  # noqa: S108
    "/private/",
    "/etc/",
    "/var/",
    "/usr/",
    "/opt/",
)


class HostPathError(ValueError):
    """A host path reached a file tool.

    A named type rather than a bare `ValueError` so the recovery middleware can
    catch exactly this and nothing else. Raised from inside the backend, which
    is past the point where deepagents converts errors into tool results: its
    file tools catch `ValueError` around *path validation*, then call
    `backend.write()` outside that guard. So this escapes on its own, and
    `HostPathGuard` is what turns it back into a tool error.
    """


def reject_host_path(key: str, workspace: Path) -> None:
    """Refuse a host path handed to a file tool.

    `virtual_mode` reads a leading `/` as the workspace root, so a host path
    does not fail — it is recreated *inside* the workspace. Writing
    `/<workspace>/runs/s/t/report.md` produces
    `<workspace>/Users/.../runs/s/t/report.md`: the call reports success, the
    file is not where the caller believes, and nothing downstream that looks
    for `report.md` will find it.

    `system.md` already warns against this and it happened anyway, which is the
    argument for a guard rather than another paragraph. Rejecting rather than
    silently rewriting: the error is what corrects the model mid-turn, and a
    rewrite would let a wrong mental model keep working.

    This is not a sandbox — the shell is unaffected and *should* be, since host
    paths are how the shell is meant to address the workspace.
    """
    if not key.startswith("/"):
        return

    prefix = f"{workspace}/"
    if key.startswith(prefix):
        suggestion = f"/{key[len(prefix) :]}"
        msg = (
            f"{key!r} is a host path, and file tools take virtual paths rooted at the "
            f"workspace. Use {suggestion!r} instead. (Passing the host path would have "
            f"created it inside the workspace, under a mirror of its own location.)"
        )
        raise HostPathError(msg)

    if key.startswith(_HOST_ROOTS):
        msg = (
            f"{key!r} is a host path, and file tools take virtual paths rooted at the "
            f"workspace — it would have been created inside the workspace, not where "
            f"you meant. Use the shell for host paths, or a virtual path such as "
            f"/runs/<session>/<turn>/ for files that belong to this task."
        )
        raise HostPathError(msg)


class ConfinedLocalShellBackend(LocalShellBackend):
    """`LocalShellBackend` with every command run through a confinement.

    Named for what it is rather than for what it does, because what it *is* is
    the part that constrains callers: a `LocalShellBackend`, which is also a
    `FilesystemBackend`, sitting in the composite's default slot where its ten
    inherited file operations serve every path no route matches. "Shell" alone
    hid all of that behind a word that sounds like one method.

    "Local" describes this object, not necessarily where the command ends up. A
    `runner` with `local = False` runs it somewhere else entirely -- and that is
    the point of the seam rather than a contradiction: the *backend* is local,
    serving this host's files, while only the last step of running a command may
    not be.

    Wrapping the command is the whole mechanism, which is why it subclasses
    rather than composes: `CompositeBackend` delegates execution to its default
    backend by calling these two methods, so anything that is not a
    `LocalShellBackend` stops being usable as one.

    Only `execute` is overridden, and that is a decision about upstream rather
    than an oversight. `LocalShellBackend.aexecute` is `asyncio.to_thread(self.
    execute, ...)`, so the async path -- which is what the interpreter's
    code-side dispatch runs on -- arrives here anyway. Overriding it as well
    wrapped every async command *twice*, nesting one `sandbox-exec` inside
    another; it still confined, so every test passed, and the only sign was the
    command string.

    Leaning on that delegation is a coupling, so
    `test_the_async_path_still_routes_through_execute` pins it. If a deepagents
    release gives `aexecute` its own implementation, that test fails rather than
    the boundary quietly going missing on one path.

    A `runner` moves the last step -- actually running the command -- out of
    this process, without moving file access with it. The two are one object
    upstream: `LocalShellBackend` *is* a `FilesystemBackend`, adding only
    `execute` and `id` to the ten file operations it inherits, and this object
    sits in the composite's default slot where those ten serve every path no
    route matches. So a deployment that supplied "the shell" would be supplying
    `/derived` as well, and a session's files belong to whoever supplies the
    directory.

    `None` means run it here, which is what upstream already does -- a default
    runner would be 110 lines of upstream's truncation, timeout and exit-code
    shaping, copied to be kept in step. The confinement is applied either way,
    before the runner sees the command, so a runner cannot forget to.
    """

    def __init__(
        self,
        confined: confinement.Confinement,
        *,
        runner: CommandRunner | None = None,
        **kwargs: Any,
    ) -> None:
        self.confinement = confined
        self.runner = runner
        super().__init__(**kwargs)

    def execute(self, command: str, *, timeout: int | None = None) -> Any:
        if self.runner is None:
            return super().execute(self.confinement.wrap(command), timeout=timeout)
        # A confinement is a command prefix naming paths on *this* host, so
        # applying it to something that runs elsewhere produces a
        # `sandbox-exec -f /Users/.../shell.sb` shipped to a machine with no
        # such file -- which fails looking like a broken remote shell rather
        # than like a wrong prefix. `local` defaults to True so a runner that
        # says nothing keeps the fence.
        outcome = self.runner.run(
            self.confinement.wrap(command) if getattr(self.runner, "local", True) else command,
            timeout=timeout,
        )
        return ExecuteResponse(
            output=outcome.output,
            exit_code=outcome.exit_code,
            truncated=outcome.truncated,
        )


def _once(result: Any, *, key: Callable[[Any], Any]) -> Any:
    """A result with its repeated matches dropped, first occurrence kept.

    `replace` rather than assigning `result.matches`: the dataclass is
    deepagents', and rebuilding it keeps `error` and `truncated` exactly as they
    came back rather than reasoning about what they should be.

    `None` matches mean a hard failure and are passed through untouched -- there
    is nothing to deduplicate and an empty list would say something different.
    """
    if result.matches is None:
        return result
    seen: set[Any] = set()
    kept = []
    for one in result.matches:
        identity = key(one)
        if identity in seen:
            continue
        seen.add(identity)
        kept.append(one)
    return replace(result, matches=kept)


class WorkspaceScopedBackend(CompositeBackend):
    """A `CompositeBackend` that refuses host paths instead of re-rooting them.

    `_get_backend_and_key` is the one place every path-addressed file operation
    resolves through — read, write, edit, delete, upload, download — so the
    check sits there rather than being repeated across a dozen overrides.
    `ls` resolves separately and is left alone: it creates nothing, and a
    listing that comes back empty is self-correcting.

    `glob` and `grep` are not, and used to be. They merge every backend's
    answer, and three of the routes here point *inside* the default backend's
    own root -- `/data`, `/memory`, `/skills/uploaded` are all real directories
    under the session. So each of them saw the same file twice, and said so:
    measured, one file supplied with `--data` came back as two matches with one
    path between them, on every pattern. `/skills` escapes it only by pointing
    at the catalogue, which is somewhere else entirely.

    A listing that comes back doubled is not self-correcting. It reads as two
    files, and the reader is a model that was about to count them.

    It is a private method of a third-party class, which is a real coupling.
    `test_backend.py` pins it, so a deepagents upgrade that renames it fails the
    build rather than quietly removing the guard.
    """

    def glob(self, pattern: str, path: str | None = None) -> Any:
        """Every match, minus the ones a second backend already gave.

        By path, because a file is a file: two backends reaching one produce
        entries that agree about everything, and any that did not agree would
        still be the same file.
        """
        return _once(super().glob(pattern, path), key=lambda one: one.get("path"))

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> Any:
        """The same, keyed by the line rather than the file.

        A file may match on twenty lines and each is its own result; what
        repeats is the whole match, so that is what identifies one.
        """
        return _once(
            super().grep(pattern, path, glob, max_count=max_count),
            key=lambda one: (one.get("path"), one.get("line"), one.get("text")),
        )

    def __init__(
        self,
        default: Any,
        routes: dict[str, Any],
        *,
        workspace: Path,
    ) -> None:
        super().__init__(default=default, routes=routes)
        self.workspace = workspace

    def _get_backend_and_key(self, key: str) -> tuple[Any, str]:
        reject_host_path(key, self.workspace)
        return super()._get_backend_and_key(key)


def prepare_scratch(cfg: Config) -> Path:
    """Create the scratch directory and refuse to use an unsafe one.

    Scratch defaults inside the workspace, where ownership is not in question.
    Pointing it at `/tmp` — one fixed location per machine — puts it in a
    world-writable directory (`/tmp` is mode 1777), which introduces two
    problems that do not exist inside the workspace:

    * anything the agent derives from `/data` becomes readable by every local
      user unless the directory itself is private, and
    * another user can pre-create the name, so finding the directory already
      there is not proof that we own it.

    So it is created `0o700` and then checked. `mkdir(mode=…)` alone is not
    enough: the mode is subject to umask, and is ignored entirely when the
    directory already exists — which it does for every workspace created before
    this check, all of them `0o755`.

    Loose permissions on a directory we own are tightened rather than rejected.
    Refusing would break those existing workspaces to no purpose, and the same
    argument for privacy applies inside the workspace as in `/tmp`. What cannot
    be repaired is a directory that is not ours, or not a directory at all —
    that is someone else's, and this raises instead of touching it.
    """
    scratch = cfg.scratch_dir
    scratch.mkdir(mode=0o700, parents=True, exist_ok=True)

    info = scratch.lstat()
    if not stat.S_ISDIR(info.st_mode):
        msg = f"scratch directory {scratch} is a symlink or not a directory"
        raise ConfigError(msg)
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        msg = f"scratch directory {scratch} is owned by uid {info.st_uid}, not by us"
        raise ConfigError(msg)
    if info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        scratch.chmod(0o700)
    return scratch


DATA_ROUTE = "/data/"

#: Routed for the same reason `/data/` is, not because skills need isolating:
#: `FilesystemMiddleware` rejects `permissions=` outright unless every rule path
#: is scoped to a route, and a request that activates a subset of the skills
#: needs deny rules for the rest.
SKILLS_ROUTE = "/skills/"

#: Routed for the same reason again: a request that declines the memory a
#: deployment wired needs a deny rule, and FilesystemMiddleware rejects
#: `permissions=` outright unless every rule path is scoped to a route.
MEMORY_ROUTE = "/memory/"

#: A request's own skills, unpacked into its session. A *longer* prefix than
#: SKILLS_ROUTE, and CompositeBackend matches longest-first, so this wins for
#: paths beneath it while everything else under /skills/ still reaches the
#: shared catalogue. That is why the catalogue kept its plain path: uploads
#: nest underneath it rather than forcing it to be renamed.
UPLOADED_SKILLS_ROUTE = "/skills/uploaded/"

#: Where a subagent's own skills are mounted, one folder per bundle, keyed by
#: the bundle's path under the catalogue so two folders may each hold a
#: `surveyor`.
#:
#: **Under `/skills/` rather than beside it, and that is the whole of why this
#: path and not a shorter one.** Two things make the catalogue read-only -- the
#: `SKILLS_ARE_READ_ONLY` tool permission and the sandbox profile -- and both
#: are scoped to this prefix. A route at `/subagent-skills/` would have been a
#: writable skills mount: the exact hole measured in `test_skills_read_only`,
#: where `backend.write("/skills/demo/PWNED.md")` created a file and
#: `backend.edit` tampered with one, reopened for the newest kind of skill.
#:
#: Longer than `SKILLS_ROUTE`, so `CompositeBackend` matches it first, the same
#: way uploads nest underneath rather than forcing a rename.
BUNDLED_SKILLS_ROUTE = "/skills/subagents/"

#: The folder name a catalogue may not use for its own skills, because
#: `BUNDLED_SKILLS_ROUTE` already means something under this root.
RESERVED_SKILL_FOLDER = "subagents"


def _bundles_with_skills(catalogue: Definitions) -> tuple[Any, ...]:
    """Every bundle that has skills to mount, or none.

    Asked of the repository rather than required of the port, the way
    `catalogue_root` asks for a root: a catalogue served over the wire has no
    folders and therefore no bundles, correctly rather than as a gap.
    """
    try:
        bundles = getattr(catalogue.subagents, "bundles", None) or {}
    except SubagentError:
        # A catalogue that will not parse has no bundles to mount, and this is
        # not the place that says so. `--list` exists to be run *because*
        # something is broken -- it catches the loader error and prints it over
        # the rest of the inventory -- and it builds a backend on the way.
        # Raising here took that listing down with it, which is the same shape
        # as warming inside `resolve_definitions`, and a test caught that one
        # too. `warm()` still refuses at startup, so nothing is being excused.
        return ()
    return tuple(bundle for bundle in bundles.values() if bundle.skills is not None)


def bundled_skills_route(where: str) -> str:
    """The route one bundle's skills are mounted at.

    Keyed on the bundle's path under the subagent catalogue rather than on the
    delegate's name, because two folders may each define a `surveyor` and a
    route has to tell them apart. `analysis/surveyor` and `surveyor` are
    different paths and stay different routes.
    """
    return f"{BUNDLED_SKILLS_ROUTE}{where}/"


def skills_sources(folders: tuple[str, ...] = ()) -> list[tuple[str, str]]:
    """Every place the agent should look for skills, labelled.

    The catalogue root, then each folder under it that holds skills, then the
    session's own. A folder has to be its own source or its skills are
    invisible: deepagents lists a source exactly one level deep, so a skill in
    `skills/research/` is found by a source at `/skills/research/` and by
    nothing else.

    The labels are not decoration. They are the first half of a skill's
    `source::name`, which is what a request grants when two parties both ship a
    `lookup` -- so what is written here is what a caller types, and it has to
    match what `skills.registry` computed from the same folders.

    `uploaded` is last, and that is the one place deepagents' own precedence
    still shows: it merges later sources over earlier ones. Nothing relies on
    it, because `uploads` refuses a name a catalogue already offers -- a request
    may not stand its own text in for a reviewed skill.
    """
    if RESERVED_SKILL_FOLDER in folders:
        # Refused rather than skipped, which is the other half of the `uploaded`
        # decision below and deliberately not the same answer. `uploaded` is
        # skipped because a session's route existed first and a catalogue folder
        # of that name is merely confusing; `subagents/` under the skills root
        # would shadow *every* bundle at once, so the skills of every delegate
        # that has any would silently stop being found. A folder that vanishes
        # is the failure this package keeps naming, and the fix is one rename.
        msg = (
            f"the skills catalogue has a folder called {RESERVED_SKILL_FOLDER!r}, "
            f"which is where each subagent's own skills are mounted "
            f"({BUNDLED_SKILLS_ROUTE}). Rename it -- left as it is, it would hide "
            "every bundled skill in the deployment"
        )
        raise ConfigError(msg)
    catalogue = [(SKILLS_ROUTE, "catalogue")]
    catalogue += [
        (f"{SKILLS_ROUTE}{name}/", name)
        for name in folders
        # `/skills/uploaded/` is already a route of its own, and a catalogue
        # folder of that name would mount over it. Skipped rather than renamed:
        # a folder called `uploaded` in a shared catalogue is confusing enough
        # without it also silently becoming the session's.
        if f"{SKILLS_ROUTE}{name}/" != UPLOADED_SKILLS_ROUTE
    ]
    return [*catalogue, (UPLOADED_SKILLS_ROUTE, "uploaded")]

#: The one memory file the agent is told to read. `/memory/` is a route so a
#: request that declined memory can be given a deny rule for it.
MEMORY_SOURCES = [f"{MEMORY_ROUTE}AGENTS.md"]


def _fence_for(
    cfg: Config,
    session_dir: Path,
    confined: confinement.Confinement,
    skills_dir: Path | None,
    env: Mapping[str, str],
) -> CommandRunner | None:
    """The Linux fence, when the confinement says there is one.

    Derived from the `Confinement` rather than deciding again. Whether to fence
    depends on the mode, the platform, the kernel's Landlock ABI and whether
    `sandlock` is installed, and two places answering that would eventually
    answer it differently -- with the failure being a shell that runs unfenced
    while `doctor` reports it confined.

    The policy is generated here from what this session already has: writable is
    the session and the scratch directory `TMPDIR` points at, readable is the
    shared catalogue and the toolchain. A deployment never writes one -- see
    `fence.py` for the hand-written policy that failed open and why that is the
    rule.
    """
    if confined.mechanism not in ("bubblewrap", "Landlock"):
        return None

    # One answer for both fences rather than the same three lines twice.
    # `argv_for` and `policy_for` take the same arguments and mean the same
    # thing by them, so a path added to one branch and not the other fences the
    # shell differently depending on which mechanism the host happens to have --
    # and the suite would not catch it, because a run only ever exercises the
    # one mechanism its own kernel offers. That is the divergence this function
    # already avoids one question earlier by deriving from `Confinement`.
    #
    # The toolchain, because `shell_env` puts this venv's `bin` first on the
    # agent's `PATH` and an allow-list that has not heard of it does not refuse
    # -- it falls through to whatever interpreter is under `/usr`, which is a
    # different Python without the `agent` dependency group, and the venv's
    # `site-packages` is unreadable besides. Silent, and invisible on macOS,
    # where the profile is `(allow default)` and has granted the same roots
    # through `readable_roots` since the day denying the home broke Python
    # there.
    #
    # Not `readable_roots`, which is the obvious call and the wrong one: it also
    # returns the *workspace*, which is right where the home is denied and the
    # workspace re-allowed inside it, and catastrophic here. Sessions live under
    # the workspace, so granting it hands every tenant back the directory this
    # fence exists to take away -- measured, before the fence: tenant B read
    # tenant A's `derived/secret.txt` with `cat ../<A>/...`, exit 0.
    readable = [*confinement.toolchain_roots(cfg.shell_path_extra)]
    if skills_dir is not None:
        readable.append(skills_dir)
    writable = [cfg.scratch_dir]

    if confined.mechanism == "bubblewrap":
        from kingfisher.infrastructure.sandbox.bubblewrap import (  # noqa: PLC0415
            BubblewrapRunner,
            argv_for,
        )

        return BubblewrapRunner(
            argv_for(session_dir, readable=readable, writable=writable),
            env=env,
        )

    # Imported here for the reason the module explains: `sandlock` is a
    # Linux-only optional install, and this function is called on every turn on
    # every platform.
    from kingfisher.infrastructure.sandbox.fence import LandlockRunner, policy_for  # noqa: PLC0415

    return LandlockRunner(
        policy_for(session_dir, readable=readable, writable=writable),
        cwd=session_dir,
        env=env,
    )


def _require_layout(session_dir: Path) -> None:
    """Refuse a session directory that has not been made yet.

    This function used to create what it needed, which is why the names lived
    in two places. Now `ensure_session_layout` is the only thing that makes a
    session, and the point of that is a directory this builder does not have to
    have come from a local disk -- so creating one here would put the assumption
    straight back.

    Loudly, because the quiet version is worse than it looks: a missing
    `/memory` is a backend whose route resolves to nothing, and the first sign
    is a tool error the model tries to work around mid-turn.
    """
    missing = [
        name
        for name in (*SESSION_DIRS, *SESSION_PLUMBING)
        if not (session_dir / name).is_dir()
    ]
    if missing:
        msg = (
            f"session directory {session_dir} is missing {', '.join(missing)}; "
            "call ensure_session_layout on it before building a backend"
        )
        raise ValueError(msg)


def build_backend(
    cfg: Config,
    session_dir: Path,
    *,
    catalogue: Definitions | None = None,
    runner: CommandRunner | None = None,
) -> BackendProtocol:
    """Build the backend rooted at one session.

    `virtual_mode` is left at its default (`True`), so file tools address
    virtual paths anchored to this session and `..` / `~` are blocked. The
    session being the root is what lets `/data` mean the same thing in every
    session while pointing somewhere different in each — one prompt, many
    tenants — and it makes cross-session access impossible rather than denied:
    there is no path from one root to another to check for.

    `/skills/` is the exception, routed to the catalogue because definitions are
    shared by every session rather than owned by one. It therefore sits outside
    the shell's root, which is a gain rather than a compromise: `_skill_denials`
    can only bind file tools, so a skill the shell could still `cat` was never
    really denied.

    `catalogue` is where that route points, and where the shell is granted read
    access. Omitted, it comes from `cfg` — the same fallback `model=` takes in
    `build_agent`: derive from `cfg`, never invent. A deployment that stages its
    definitions elsewhere passes them, and both the route and the sandbox
    profile follow, because a catalogue the file tools could read and the shell
    could not would be two different answers to one question.

    A `CompositeBackend` is required rather than merely convenient.
    `FilesystemMiddleware` refuses `permissions=` outright when the backend
    supports execution — unless every rule path is scoped to a route. Routing
    `/data/` to its own backend is what makes the write-deny rule legal while
    `execute` still works, because CompositeBackend delegates execution to its
    default backend.
    """
    skills = (catalogue or Definitions.from_config(cfg)).skills
    # A directory on this host stays a directory: cheaper than copying every
    # skill into a store, and the only shape whose skills can also be *run*,
    # since a skill's scripts are executed by the shell against
    # `$KINGFISHER_SKILLS` and a store has no path for the shell to reach.
    # Anything else is mounted from what the repository can hand over.
    skills_dir = catalogue_root(skills)

    prepare_scratch(cfg)
    _require_layout(session_dir)
    uploaded = session_dir / UPLOADED_SKILLS
    # `FilesystemBackend` wants the root to exist. A *supplied* catalogue was
    # already refused by `resolve_definitions` if it did not, so this only ever
    # creates a derived one -- and stays here for the callers that build a
    # backend directly, without a service to have resolved anything for them.
    if skills_dir is not None:
        skills_dir.mkdir(parents=True, exist_ok=True)

    confined = confinement.shell_confinement(cfg, skills=skills_dir)
    env = shell_env(cfg, session_dir, catalogue=catalogue)
    if runner is None:
        chosen = _fence_for(cfg, session_dir, confined, skills_dir, env)
    else:
        # Said once, here, rather than left for a reader to work out from two
        # places. What confines the shell depends on what runs the command, and
        # a supplied runner that is not local receives nothing this process
        # applied -- so the confinement has to stop claiming otherwise.
        chosen = runner
        confined = confinement.with_supplied_runner(
            confined, local=getattr(runner, "local", True)
        )
    shell = ConfinedLocalShellBackend(
        confined,
        runner=chosen,
        root_dir=str(session_dir),
        env=env,
        timeout=cfg.execution_timeout_s,
    )
    return WorkspaceScopedBackend(
        default=shell,
        routes={
            DATA_ROUTE: FilesystemBackend(root_dir=str(session_dir / "data")),
            SKILLS_ROUTE: (
                FilesystemBackend(root_dir=str(skills_dir))
                if skills_dir is not None
                else skills_backend(skills)
            ),
            MEMORY_ROUTE: FilesystemBackend(root_dir=str(session_dir / "memory")),
            UPLOADED_SKILLS_ROUTE: FilesystemBackend(root_dir=str(uploaded)),
            # One per bundle, so a delegate's own skills are readable by the
            # file tools that read every other skill -- and read-only for the
            # same two reasons, since both enforcement points are scoped to
            # `/skills/` and this sits underneath it.
            **{
                bundled_skills_route(bundle.where): FilesystemBackend(
                    root_dir=str(bundle.skills)
                )
                for bundle in _bundles_with_skills(catalogue or Definitions.from_config(cfg))
            },
        },
        workspace=session_dir,
    )


# `HostPathGuard` lives beside `reject_host_path` rather than with the other
# middleware, and the two are one mechanism: this catches what that raises. It
# spent a while in `narrowing` on the grounds that it was an `AgentMiddleware`
# like its neighbours there -- but that file is about applying a request's
# capabilities, and this applies none. It turns an error into something the
# model can act on, and the error is raised twenty lines up.


#: Which arguments name a file. The convention this repository already keeps --
#: `test_every_shipped_tool_taking_a_path_says_which_kind` walks the shipped
#: tools looking for exactly this parameter name -- so widening it is a line
#: here and a test, rather than a design question.
#:
#: A tool calling it `input_file` is missed, and that is visible rather than
#: silent: the translation does not happen, the tool gets the agent's own name,
#: and it fails to find the file on the first call. The failure that matters is
#: the other direction, and it cannot happen -- a name that is *not* translated
#: cannot reach outside the session, because nothing gave it a way to.
PATH_ARGUMENTS: frozenset[str] = frozenset({"path"})


class WorkspaceToolPaths(AgentMiddleware):
    """Translate the agent's own paths into real ones, per session.

    A workspace tool is an ordinary Python function. It runs inside this
    process, receives whatever the model produced, and opens files with the
    operating system -- so `/data/config.ini` means `/data/config.ini` on this
    machine, which is not there. The built-in file tools do not have that problem
    because they are defined *inside* deepagents' filesystem middleware, closing
    over the backend that roots them at a session; nothing hands that backend to
    a tool the caller supplied, and `ToolRuntime` does not carry one.

    So there were two routes to the filesystem and the session was only on one
    of them. This is the bridge.

    **It closes a leak and a usability bug with one change, and the second is how
    the first was found.** `system.md` teaches virtual paths and says the two
    views do not mix; the tools wanted host paths and the agent is never told
    one. Measured in a real run: the model passed `/data/config.ini` and the tool
    raised `FileNotFoundError`. The only way it could succeed was to go looking
    -- `pwd` in the shell, learn the layout -- and from there it can name *any*
    session: `line_count('/workspace/sessions/<other>/secret.txt')` returned an
    answer.

    After this, that argument resolves under *this* session and finds nothing,
    for the same reason `/etc/passwd` does. Not by refusing it -- by there being
    no way to say it.

    Rewriting the call rather than wrapping each tool, because the tools are not
    alike: some are `BaseTool`s from `@tool` and some are plain functions. The
    call is the one shape they share, and langgraph documents the rewrite --
    `{**request.tool_call, "args": {...}}`.
    """

    def __init__(self, names: frozenset[str], session_dir: Path) -> None:
        self.names = names
        self.session_dir = Path(session_dir)
        super().__init__()

    def _translated(self, request: Any) -> Any:
        """The same call with its path arguments made real, or the request
        unchanged when it names no tool of ours and no path."""
        call = request.tool_call
        if call.get("name") not in self.names:
            return request
        args = call.get("args") or {}
        wanted = {key: args[key] for key in args if key in PATH_ARGUMENTS}
        if not wanted:
            return request
        return replace(
            request,
            tool_call={
                **call,
                "args": {**args, **{key: self._real(value) for key, value in wanted.items()}},
            },
        )

    def _real(self, value: Any) -> Any:
        """One argument, resolved against the session the way a file tool would.

        A leading slash means the session root, exactly as it does for
        `read_file`, so the agent has one vocabulary rather than two. Anything
        that is not a string is handed back untouched: a tool may take a number
        called `path` and this is not the place to have an opinion about that.
        """
        if not isinstance(value, str) or not value.strip():
            return value
        landed = within(self.session_dir, value.lstrip("/"))
        # The second check `within` tells adapters to do, and it is not optional
        # here: that one is lexical, on purpose, because the domain may not touch
        # the filesystem -- and a session directory is one the agent can write
        # to. `execute` is rooted there, so it can make a symlink pointing out,
        # hand a tool the virtual path to it, and be read the target.
        #
        # Measured before this existed: a link at `/derived/link.txt` pointing at
        # another session returned `TENANT-A-PRIVATE` through a tool, while
        # `read_file` refused the same path. deepagents resolves and compares;
        # this had only half of that.
        #
        # Both sides resolved, since a workspace can itself sit under a symlink
        # -- `/tmp` is `/private/tmp` on macOS -- and comparing one resolved path
        # to one unresolved root refuses everything.
        real = landed.resolve()
        if not real.is_relative_to(self.session_dir.resolve()):
            msg = (
                f"reference {value!r} resolves outside this session; a link inside it "
                "does not widen it"
            )
            raise UnsafeReferenceError(msg)
        return str(real)

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        try:
            return handler(self._translated(request))
        except UnsafeReferenceError as escaped:
            return self._refused(request, escaped)

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        try:
            return await handler(self._translated(request))
        except UnsafeReferenceError as escaped:
            return self._refused(request, escaped)

    def _refused(self, request: Any, escaped: UnsafeReferenceError) -> ToolMessage:
        """A path that climbs out, reported the way `reject_host_path` reports one.

        Raising would end the turn on a mistake the model can correct; a tool
        error names the rule and lets it try again with a path inside the
        session.
        """
        call = request.tool_call
        return ToolMessage(
            content=(
                f"Error: {escaped}. Tool paths are the same virtual paths the file "
                "tools take, rooted at this session -- `/data/<name>`, "
                "`/derived/<name>` -- and cannot climb out of it."
            ),
            tool_call_id=call.get("id", ""),
            name=call.get("name"),
            status="error",
        )


class WorkspaceToolErrors(AgentMiddleware):
    """Turn a workspace tool's exception into a failed tool result.

    A built-in reports its failures through `_tool_error` and the model carries
    on; `HostPathGuard` below gives a rejected host path the same treatment. A
    workspace tool had neither, so upstream's default applied -- bad *arguments*
    are converted, everything else is re-raised -- and one wrong path killed a
    sixteen-call run. Measured, on one deployment: the same mistake through
    `read_file` cost nothing, and through `csv_profile` cost the run. Which of
    the two happened depended on the tool the model reached for, which the
    deployment cannot predict.

    Not swallowing. `status="error"` and the text carried whole, so the model
    sees a failure rather than a value and the transcript records it. The
    objection this answers -- that catching everything would "hide real faults
    behind a retry" -- is about hiding, and a failed tool result is the opposite:
    a tool that always raises now fails `recursion_limit` times in a log
    somebody can read, rather than once with a traceback.

    Only tools the workspace defined. Built-ins already report properly and
    `HostPathGuard` covers the one thing they do not; widening this to them
    would put a second opinion between deepagents and its own error handling.

    `BaseException` is deliberately not caught -- an interrupt or a memory error
    is not a tool telling the model something.
    """

    def __init__(self, names: frozenset[str]) -> None:
        super().__init__()
        self.names = names

    def _mine(self, request: Any) -> str | None:
        """The tool's name if this middleware speaks for it, else `None`."""
        name = request.tool_call.get("name")
        return name if name in self.names else None

    def _as_tool_error(self, request: Any, exc: Exception) -> ToolMessage:
        call = request.tool_call
        # The type as well as the message. A workspace tool is somebody else's
        # code and its exceptions were not written to be read by a model, so
        # `FileNotFoundError: /data/x.csv` reads far better than the path alone.
        return ToolMessage(
            content=f"Error: {type(exc).__name__}: {exc}",
            tool_call_id=call.get("id", ""),
            name=call.get("name"),
            status="error",
        )

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        try:
            return handler(request)
        except Exception as exc:
            if self._mine(request) is None:
                raise
            return self._as_tool_error(request, exc)

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        try:
            return await handler(request)
        except Exception as exc:
            if self._mine(request) is None:
                raise
            return self._as_tool_error(request, exc)


class HostPathGuard(AgentMiddleware):
    """Turn a rejected host path back into something the agent can act on.

    `reject_host_path` exists to correct the model mid-turn -- its message
    names the virtual path to use instead. But it raises from inside the
    backend, and deepagents' file tools only convert `ValueError` raised during
    *path validation*; `backend.write()` is called outside that guard. So the
    exception escaped the tool, escaped the graph, and killed the run. The
    message meant to teach the model never reached it.

    Returning it as a failed `ToolMessage` is what makes the correction work,
    exactly as `ToolAllowlist` does for a tool the request did not activate.
    Only `HostPathError` is caught: a middleware that swallowed every
    `ValueError` would hide real faults behind a retry.
    """

    def _as_tool_error(self, request: Any, exc: HostPathError) -> ToolMessage:
        call = request.tool_call
        return ToolMessage(
            content=f"Error: {exc}",
            tool_call_id=call.get("id", ""),
            name=call.get("name"),
            status="error",
        )

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        try:
            return handler(request)
        except HostPathError as exc:
            return self._as_tool_error(request, exc)

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        try:
            return await handler(request)
        except HostPathError as exc:
            return self._as_tool_error(request, exc)
