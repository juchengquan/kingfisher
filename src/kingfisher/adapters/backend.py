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

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from deepagents.backends import CompositeBackend, FilesystemBackend, LocalShellBackend

from kingfisher.domain.config import Config

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
        "TMPDIR": str(cfg.workspace / ".kingfisher" / "tmp"),
    }


DATA_ROUTE = "/data/"

#: Routed for the same reason `/data/` is, not because skills need isolating:
#: `FilesystemMiddleware` rejects `permissions=` outright unless every rule path
#: is scoped to a route, and a request that activates a subset of the skills
#: needs deny rules for the rest.
SKILLS_ROUTE = "/skills/"


def build_backend(cfg: Config) -> BackendProtocol:
    """Build the backend rooted at the workspace.

    `virtual_mode` is left at its default (`True`), so file tools address
    virtual paths anchored to the workspace and `..` / `~` are blocked. That is
    what makes the system prompt portable: `/data` means the same thing on
    every machine.

    A `CompositeBackend` is required rather than merely convenient.
    `FilesystemMiddleware` refuses `permissions=` outright when the backend
    supports execution — unless every rule path is scoped to a route. Routing
    `/data/` to its own backend is what makes the write-deny rule legal while
    `execute` still works, because CompositeBackend delegates execution to its
    default backend.
    """
    (cfg.workspace / ".kingfisher" / "tmp").mkdir(parents=True, exist_ok=True)
    for routed in ("data", "skills"):
        (cfg.workspace / routed).mkdir(parents=True, exist_ok=True)

    shell = LocalShellBackend(
        root_dir=str(cfg.workspace),
        env=shell_env(cfg),
        timeout=cfg.timeout_s,
    )
    return CompositeBackend(
        default=shell,
        routes={
            DATA_ROUTE: FilesystemBackend(root_dir=str(cfg.workspace / "data")),
            SKILLS_ROUTE: FilesystemBackend(root_dir=str(cfg.workspace / "skills")),
        },
    )
