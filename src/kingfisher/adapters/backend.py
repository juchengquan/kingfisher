"""Filesystem + shell backend.

`LocalShellBackend` defaults to `inherit_env=False` with `env=None`, which means
`subprocess.run(..., env={})` — no variables at all, not even `PATH`. That is a
good security default and a fatal usability one: nothing resolves. We keep the
default and supply an explicit allowlist instead of inheriting the parent
environment, so the agent's shell can run the toolchain but cannot read
credentials out of the environment.

This is not a sandbox. `execute` still reaches the whole host filesystem and the
network; the allowlist narrows the blast radius, it does not contain it.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from deepagents.backends import CompositeBackend, FilesystemBackend, LocalShellBackend

from kingfisher.config import Config, ConfigError

if TYPE_CHECKING:
    from deepagents.backends import BackendProtocol

_BASE_PATH: tuple[str, ...] = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")


def shell_env(cfg: Config) -> dict[str, str]:
    """The explicit allowlist handed to the shell — no credentials.

    `HOME` points at the workspace rather than the real home directory, so the
    usual credential locations (`~/.aws`, `~/.ssh`, `~/.config`) are not where
    the agent's tooling will look.
    """
    path_parts = [str(Path(sys.executable).parent), *cfg.shell_path_extra, *_BASE_PATH]
    return {
        "PATH": ":".join(path_parts),
        "HOME": str(cfg.workspace),
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


def build_backend(cfg: Config, session_dir: Path) -> BackendProtocol:
    """Build the backend rooted at one session.

    `virtual_mode` is left at its default (`True`), so file tools address
    virtual paths anchored to this session and `..` / `~` are blocked. The
    session being the root is what lets `/data` mean the same thing in every
    session while pointing somewhere different in each — one prompt, many
    tenants — and it makes cross-session access impossible rather than denied:
    there is no path from one root to another to check for.

    `/skills/` is the exception, routed to the workspace because definitions are
    shared by every session rather than owned by one. It therefore sits outside
    the shell's root, which is a gain rather than a compromise: `_skill_denials`
    can only bind file tools, so a skill the shell could still `cat` was never
    really denied.

    A `CompositeBackend` is required rather than merely convenient.
    `FilesystemMiddleware` refuses `permissions=` outright when the backend
    supports execution — unless every rule path is scoped to a route. Routing
    `/data/` to its own backend is what makes the write-deny rule legal while
    `execute` still works, because CompositeBackend delegates execution to its
    default backend.
    """
    prepare_scratch(cfg)
    for routed in ("data", "memory"):
        (session_dir / routed).mkdir(parents=True, exist_ok=True)
    (cfg.workspace / "skills").mkdir(parents=True, exist_ok=True)

    shell = LocalShellBackend(
        root_dir=str(session_dir),
        env=shell_env(cfg),
        timeout=cfg.timeout_s,
    )
    return WorkspaceScopedBackend(
        default=shell,
        routes={
            DATA_ROUTE: FilesystemBackend(root_dir=str(session_dir / "data")),
            SKILLS_ROUTE: FilesystemBackend(root_dir=str(cfg.workspace / "skills")),
            MEMORY_ROUTE: FilesystemBackend(root_dir=str(session_dir / "memory")),
        },
        workspace=session_dir,
    )
