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
from pathlib import Path
from typing import TYPE_CHECKING, Any

from deepagents.backends import CompositeBackend, FilesystemBackend, LocalShellBackend

from kingfisher.config import Config, ConfigError
from kingfisher.infrastructure import confinement

if TYPE_CHECKING:
    from collections.abc import Mapping

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
    """
    return Path(session_dir) / ".home"


def shell_env(
    cfg: Config, session_dir: Path, *, catalogue: Mapping[str, Path] | None = None
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
    return {
        "PATH": ":".join(path_parts),
        "HOME": str(agent_home(session_dir)),
        "KINGFISHER_SKILLS": str((catalogue or cfg.catalogue_roots)["skills"]),
        "LANG": "en_US.UTF-8",
        "LC_ALL": "en_US.UTF-8",
        "TMPDIR": str(cfg.scratch_dir),
    }


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


class ConfinedShell(LocalShellBackend):
    """`LocalShellBackend` with every command run through a confinement.

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
    """

    def __init__(self, confined: confinement.Confinement, **kwargs: Any) -> None:
        self.confinement = confined
        super().__init__(**kwargs)

    def execute(self, command: str, *, timeout: int | None = None) -> Any:
        return super().execute(self.confinement.wrap(command), timeout=timeout)


class WorkspaceScopedBackend(CompositeBackend):
    """A `CompositeBackend` that refuses host paths instead of re-rooting them.

    `_get_backend_and_key` is the one place every path-addressed file operation
    resolves through — read, write, edit, delete, upload, download — so the
    check sits there rather than being repeated across a dozen overrides.
    `ls`, `glob` and `grep` resolve separately and are left alone: they create
    nothing, and a listing that comes back empty is self-correcting.

    It is a private method of a third-party class, which is a real coupling.
    `test_backend.py` pins it, so a deepagents upgrade that renames it fails the
    build rather than quietly removing the guard.
    """

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
    if info.st_uid != os.getuid():
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

#: What deepagents is told to load skills from: the catalogue first, then this
#: session's uploads. It loads sources in order and lets a later one override an
#: earlier, which is exactly what `uploads` refuses to allow -- a collision is
#: rejected before it can happen, so the ordering here never decides anything.
#:
#: Built from the routes rather than written out again. These paths were spelled
#: as literals in `agent.py` while living here as constants, which is two copies
#: of the same string kept in step by nobody -- and both `agent` and `delegation`
#: need them, so they belong with the routes they name.
SKILLS_SOURCES = [(SKILLS_ROUTE, "Catalogue"), (UPLOADED_SKILLS_ROUTE, "Uploaded")]

#: The one memory file the agent is told to read. `/memory/` is a route so a
#: request that declined memory can be given a deny rule for it.
MEMORY_SOURCES = [f"{MEMORY_ROUTE}AGENTS.md"]


def build_backend(
    cfg: Config, session_dir: Path, *, catalogue: Mapping[str, Path] | None = None
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
    skills_dir = (catalogue or cfg.catalogue_roots)["skills"]

    prepare_scratch(cfg)
    for routed in ("data", "memory"):
        (session_dir / routed).mkdir(parents=True, exist_ok=True)
    agent_home(session_dir).mkdir(parents=True, exist_ok=True)
    uploaded = session_dir / "skills" / "uploaded"
    uploaded.mkdir(parents=True, exist_ok=True)
    # `FilesystemBackend` wants the root to exist. A *supplied* catalogue was
    # already refused by `resolve_catalogue` if it did not, so this only ever
    # creates a derived one -- and stays here for the callers that build a
    # backend directly, without a service to have resolved anything for them.
    skills_dir.mkdir(parents=True, exist_ok=True)

    shell = ConfinedShell(
        confinement.resolve(
            cfg.shell_sandbox,
            workspace=cfg.workspace,
            state_dir=cfg.state_dir,
            scratch_dir=cfg.scratch_dir,
            extra=cfg.shell_path_extra,
            skills=skills_dir,
        ),
        root_dir=str(session_dir),
        env=shell_env(cfg, session_dir, catalogue=catalogue),
        timeout=cfg.timeout_s,
    )
    return WorkspaceScopedBackend(
        default=shell,
        routes={
            DATA_ROUTE: FilesystemBackend(root_dir=str(session_dir / "data")),
            SKILLS_ROUTE: FilesystemBackend(root_dir=str(skills_dir)),
            MEMORY_ROUTE: FilesystemBackend(root_dir=str(session_dir / "memory")),
            UPLOADED_SKILLS_ROUTE: FilesystemBackend(root_dir=str(uploaded)),
        },
        workspace=session_dir,
    )
