"""How the core reads a session's backend without naming whose it is: the shell it
runs, and the skills it serves."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from deepagents.backends import CompositeBackend

# Re-exported: `ports.md` has named this path to deployments writing their own
# backend, which may import it from here.
from kingfisher.core.agent.host_paths import (
    HostPathError as HostPathError,  # noqa: PLC0414 -- the re-export above
)
from kingfisher.core.agent.prompting import ShellPrompt
from kingfisher.core.config import DEFAULT_LAYOUT, ConfigError
from kingfisher.core.definitions.catalogue import Definitions
from kingfisher.core.definitions.subagents.spec import SubagentError
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


def _bundles_with_skills(catalogue: Definitions) -> tuple[Any, ...]:
    """Every bundle that has skills to mount, or none."""
    try:
        bundles = catalogue.subagents.bundles
    except SubagentError:
        # A catalogue that will not parse has no bundles to mount, and this is
        # not the place that says so. `--list` exists to be run *because*
        # something is broken -- it catches the loader error and prints it over
        # the rest of the inventory -- and it builds a backend on the way.
        # Raising here took that listing down with it, which is the same shape
        # as warming inside `resolve_definitions`, and a test caught that one
        # too. `warm()` still refuses at startup, so nothing is being excused.
        return ()
    return tuple(bundle for bundle in bundles.values() if bundle.skills)


def bundled_skills_route(where: str) -> str:
    """The route one bundle's skills are mounted at."""
    return f"{BUNDLED_SKILLS_ROUTE}{where}/"


def bundled_skill_mounts(where: str, directories: tuple[Path, ...]) -> tuple[tuple[str, Path], ...]:
    """`(route, directory)` for each of one bundle's skill directories.

    One directory keeps the bundle's own route. Several are numbered beneath it in
    the order the definition listed them, because a directory's name is no label:
    a package's are all called `skills`. Beneath rather than beside, so each stays
    under `/skills/` and inherits its read-only rule.
    """
    if len(directories) == 1:
        return ((bundled_skills_route(where), directories[0]),)
    return tuple(
        (bundled_skills_route(f"{where}/{n}"), directory)
        for n, directory in enumerate(directories, start=1)
    )


def skill_mounts(catalogue: Definitions) -> tuple[tuple[str, Path], ...]:
    """`(route, directory)` for every skills directory mounted beneath the catalogue.

    Each bundle's, then each mount a deployment named. All under `/skills/`, because
    both things that keep the catalogue read-only -- the deny rule and the sandbox
    profile -- are scoped to that prefix. `backend_at` mounts this list and
    `a_session_serves_the_skills_kingfisher_read` checks a `Sessions` against it: a kind of
    skills directory mounted anywhere but here would escape the check.
    """
    return (
        *(
            pair
            for bundle in _bundles_with_skills(catalogue)
            for pair in bundled_skill_mounts(bundle.where, bundle.skills)
        ),
        # The segment the registry lists a mount under, so the deny rule for a skill
        # in it lands here.
        *((f"{SKILLS_ROUTE}{label}/", mount) for label, mount in catalogue.skills.mounts.items()),
    )


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


