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

    Read off the shell, not the sessions that made it: a subclass swapping in its own
    sandbox would otherwise inherit kingfisher's wording for a shell it does not have.
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

    Asked of the shell, not its class as deepagents does: kingfisher's is one class
    whatever its runner, and a runner may ship commands elsewhere. A deployment's own
    shell is not known to run here.
    """
    return getattr(_shell_of(backend), "runs_here", False) is True


def skills_sources(folders: tuple[str, ...] = ()) -> list[tuple[str, str]]:
    """Every place the agent should look for skills, labelled."""
    if RESERVED_SKILL_FOLDER in folders:
        # Refused, not skipped: it would silently shadow every subagent's bundled skills.
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
    for it.
    """
    return [f"{layout.memory_route}AGENTS.md"]


