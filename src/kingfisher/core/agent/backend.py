"""How the core reads a session's backend without naming whose it is: the shell it
runs, and the skills it serves."""

from __future__ import annotations

from typing import Any

from deepagents.backends import CompositeBackend

from kingfisher.core.config import ConfigError
from kingfisher.core.layout import (
    BUNDLED_SKILLS_ROUTE,
    HARNESS,
    RESERVED_SKILL_FOLDER,
    SKILLS,
    SKILLS_ROUTE,
    Layout,
)

from .host_paths import HOST_ROOTS
from .prompting import ShellPrompt, rooted


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


def session_root_of(backend: Any) -> str | None:
    """Where the shell `backend` runs `execute` on sees the session folder, which it
    exports as `KINGFISHER_SESSION_ROOT`, or `None` where it does not say.

    Read off the shell, as `shell_prompt_of` is, and for the same reason.
    """
    shell = _shell_of(backend)
    root = getattr(shell, "session_root", None)
    if root is not None and not isinstance(root, str):
        msg = (
            f"{type(shell).__name__}.session_root is a {type(root).__name__}; it has to be "
            "a str, the session folder's path as that shell sees it"
        )
        raise TypeError(msg)
    if root == "":
        # Expanded empty, `"$KINGFISHER_SESSION_ROOT/inputs/x"` is the host's `/inputs/x`.
        msg = (
            f"{type(shell).__name__}.session_root is empty; it has to be the session "
            "folder's path as that shell sees it, or None where the shell does not export one"
        )
        raise ValueError(msg)
    return root


def shell_told(backend: Any) -> ShellPrompt | None:
    """What the agent is told of the shell `backend` runs `execute` on: what it says of
    itself, and of `$KINGFISHER_SESSION_ROOT` where it exports one."""
    said = shell_prompt_of(backend)
    return said if session_root_of(backend) is None else rooted(said)


def shell_runs_here(backend: Any) -> bool:
    """Whether `backend` runs `execute` on kingfisher's shell, on this host.

    Asked of the shell, not its class as deepagents does: kingfisher's is one class
    whatever its runner, and a runner may ship commands elsewhere. A deployment's own
    shell is not known to run here.
    """
    return getattr(_shell_of(backend), "runs_here", False) is True


def folders_of(layout: Layout, backend: Any = None) -> frozenset[str]:
    """Every folder at the top of a session a file tool may save in: the layout's, and
    any `backend` mounts of its own, which a deployment's prompt says are the agent's."""
    routes = backend.routes if isinstance(backend, CompositeBackend) else {}
    mounted = (route.strip("/").split("/", 1)[0] for route in routes)
    return frozenset({*layout.session_dirs, SKILLS, HARNESS, *mounted})


def strays(where: str, folders: frozenset[str]) -> bool:
    """Whether a save at `where`, a path `validate_path` returned, lands in none of
    `folders`: nothing keeps or returns it, and the tool that saved it says it succeeded."""
    # A host path is the backend's to refuse, naming the virtual path it meant.
    if where.startswith(HOST_ROOTS):
        return False
    return where.lstrip("/").split("/", 1)[0] not in folders


def miscased(where: str, layout: Layout, folders: frozenset[str]) -> str | None:
    """`where` with the session's own folders spelled as they are, where `where`, a path
    `validate_path` returned, spells one in other capitals; `None` otherwise.

    By name, never by asking the disk: the rules and the routes match exactly, so on a
    case-insensitive volume `/.HARNESS/` matches neither and the default backend opens the
    real `.harness`. The folders are `folders` and every route of `layout`, nested ones
    included, since `/scratchpad/TOOL_RESULTS/` gets past the deny on the saved results.
    """
    parts = where.strip("/").split("/")
    spelled = list(parts)
    for known in {*(route.path for route in layout.routes), *(f"/{f}/" for f in folders)}:
        names = known.strip("/").split("/")
        if len(names) <= len(parts) and all(
            name.casefold() == part.casefold() for name, part in zip(names, parts, strict=False)
        ):
            spelled[: len(names)] = names
    return None if spelled == parts else "/" + "/".join(spelled)


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


def memory_sources(layout: Layout) -> list[str]:
    """The one memory file the agent is told to read, under this layout's `/memory/`.

    `/memory/` is a route so a request that declined memory can be given a deny rule
    for it.
    """
    return [layout.memory_file_route]


