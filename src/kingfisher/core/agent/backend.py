"""How the core reads a session's backend without naming whose it is: the shell it
runs, and the skills it serves."""

from __future__ import annotations

from typing import Any

from deepagents.backends import CompositeBackend

from kingfisher.core.agent.prompting import ShellPrompt
from kingfisher.core.config import DEFAULT_LAYOUT, ConfigError
from kingfisher.core.layout import (
    BUNDLED_SKILLS_ROUTE,
    RESERVED_SKILL_FOLDER,
    SKILLS_ROUTE,
    Layout,
)


def _shell_of(backend: Any) -> Any:
    """The object `backend` runs `execute` on."""
    return backend.default if isinstance(backend, CompositeBackend) else backend


def shell_prompt_of(backend: Any) -> ShellPrompt | None:
    """What the shell `backend` runs `execute` on says of itself, or `None` for nothing.

    Read off the shell rather than off the sessions that made it. A
    `LocalSessions` subclass that swaps in a sandbox of its own would inherit
    kingfisher's wording from the class, and its agents be told about a shell they do not
    have; on the shell, a sandbox that says nothing is described neutrally until it does.
    """
    shell = _shell_of(backend)
    said = getattr(shell, "shell_prompt", None)
    if said is not None and not isinstance(said, ShellPrompt):
        msg = (
            f"{type(shell).__name__}.shell_prompt is a {type(said).__name__}; it has to be "
            "a kingfisher.plugins.ShellPrompt, whose parts say where each piece of wording goes"
        )
        raise TypeError(msg)
    return said


def shell_runs_here(backend: Any) -> bool:
    """Whether `backend` runs `execute` on kingfisher's shell, on this host.

    The one shell whose paths on this host deepagents gives rightly: it decides by the
    shell's class, and kingfisher's is the same class whatever its runner, so a runner
    shipping commands elsewhere was handed paths that machine does not have. A shell of a
    deployment's own is not known to run here, and says what it sees for itself.
    """
    return getattr(_shell_of(backend), "runs_here", False) is True


def skills_sources(folders: tuple[str, ...] = ()) -> list[tuple[str, str]]:
    """Every place the agent should look for skills, labelled."""
    if RESERVED_SKILL_FOLDER in folders:
        # Refused rather than skipped: `subagents/` under the skills root would
        # shadow *every* bundle at once, so the skills of every subagent that has
        # any would silently stop being found. A folder that vanishes is the
        # failure this package keeps naming, and the fix is one rename.
        msg = (
            f"the skills catalogue has a folder called {RESERVED_SKILL_FOLDER!r}, "
            f"which is where each subagent's own skills are mounted "
            f"({BUNDLED_SKILLS_ROUTE}). Rename it -- left as it is, it would hide "
            "every bundled skill in the deployment"
        )
        raise ConfigError(msg)
    return [
        (SKILLS_ROUTE, "catalogue"),
        *((f"{SKILLS_ROUTE}{name}/", name) for name in folders),
    ]


def memory_sources(layout: Layout = DEFAULT_LAYOUT) -> list[str]:
    """The one memory file the agent is told to read, under this layout's `/memory/`.

    `/memory/` is a route so a request that declined memory can be given a deny rule
    for it. The default reproduces the route kingfisher has always used.
    """
    return [f"{layout.memory_route}AGENTS.md"]


