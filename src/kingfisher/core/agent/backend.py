"""What the core asks of any session's backend: the `Sessions` interface, and how a
backend's shell and skills are read without naming whose backend it is."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from deepagents.backends import CompositeBackend

from kingfisher.config import DEFAULT_LAYOUT, Config, ConfigError

# Re-exported: `ports.md` has named this path to deployments writing their own
# backend, which may import it from here.
from kingfisher.core.agent.host_paths import (
    HostPathError as HostPathError,  # noqa: PLC0414 -- the re-export above
)
from kingfisher.core.agent.prompting import ShellPrompt
from kingfisher.core.definitions.catalogue import Definitions
from kingfisher.core.definitions.subagents.spec import SubagentError
from kingfisher.core.session.threads import off_loop, thread_pool
from kingfisher.layout import (
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
            "a kingfisher.ShellPrompt, whose parts say where each piece of wording goes"
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


@runtime_checkable
class Sessions(Protocol):
    """How a deployment says where its sessions are, and what each one runs on.

    Asked per turn to `open` the session it is for, and asked the questions only
    something that sees every session can answer: which there are, how big, and
    when each was last used. Keyed by session id rather than by a directory,
    because a backend that runs elsewhere has no directory here to be handed.

    `open` is typed against its call and not its return, which is the asymmetry
    worth knowing. deepagents decides what a backend *is* with `isinstance` against its
    own abstract base class, so a protocol describing the return would describe the
    requirement wrongly -- an object satisfying it exactly would still be handed no
    shell. `refuse_unusable_backend` asks that question at the only time it can be
    answered, which is once there is an object to ask about.

    Each takes the `Config` because the default is one object serving every
    workspace, and the workspace is where it keeps them. A deployment's own may
    ignore it.

    What one has to keep, each held by a check in this repository's
    `tests/checks/sessions.py`:

    - two session ids never share storage;
    - what one turn's backend wrote, the next `open` of that session finds;
    - a session asked for is listed, and one deleted is gone;
    - a claim is exclusive and one session's: a second claim on a held session fails,
      and a claim on another does not;
    - a host path a backend names for a file is that session's file;
    - a session serves under `/skills/` the skills kingfisher read;
    - every async twin answers as its sync one does.

    What else kingfisher reads, it reads where an implementation declares it, by name:
    `where(cfg)` here, the folder these sessions are kept in on this host, for the
    origins line; `host_path`, `shell_prompt` and `unprotected` on a backend `open`
    returns; and `runs_here` on its shell.

    And what `open` returns, held by `tests/checks/backend.py`: deepagents recognises it
    as running commands and it routes every path kingfisher denies -- both refused at
    startup by `refuse_unusable_backend` -- and it reads back what it was given, keeps
    deepagents' offloads in the scratchpad, keeps the shell out of `/.harness` and
    `/inputs`, and refuses a host path.
    """

    def open(
        self,
        cfg: Config,
        session_id: str,
        # Positional-only, or this would be dictating parameter *names*: a protocol
        # matches those, so without the slash a deployment whose `open` reads
        # `(config, which)` fails to satisfy it for no reason anybody could act on.
        /,
        *,
        catalogue: Definitions | None = None,
    ) -> Any:
        """This session's backend, making the session if it is new."""
        ...

    def sessions(self, cfg: Config) -> tuple[tuple[str, float], ...]:
        """`(session_id, last_used)` for every session there is."""
        ...

    def mark_used(self, cfg: Config, session_id: str) -> None:
        """Record that a turn is running in this session now.

        A port because retention depends on it. A turn writes *inside* a session, and
        on an ordinary filesystem that leaves the session's own timestamp alone --
        measured, a session was still 10,000s idle by that clock immediately after a
        turn completed in it.
        """
        ...

    def size(self, cfg: Config, session_id: str) -> int:
        """How many bytes this session holds."""
        ...

    def delete(self, cfg: Config, session_id: str) -> str | None:
        """Remove this session and everything in it. A reason on failure, or `None`."""
        ...

    def claim(
        self, cfg: Config, session_id: str, /, *, stale_after: float, now: float | None = None
    ) -> bool:
        """Take this session's turn lock, or answer False because a live turn holds it.

        Two turns in one session share a conversation and the last write wins, so this
        is what refuses the second. A claim older than `stale_after` seconds belongs to
        a turn that died, and is taken over; `now` is the clock that is judged by.
        """
        ...

    def release(self, cfg: Config, session_id: str, /) -> None:
        """Give the lock back. Safe where it was never taken."""
        ...

    def held(
        self, cfg: Config, session_id: str, /, *, stale_after: float, now: float | None = None
    ) -> bool:
        """Whether a claim on this session is young enough that its turn may be running."""
        ...

    # The async twins. Written out here, so that a class subclassing this protocol has
    # them as `open` and `sessions` on kingfisher's pool, and overrides one only where
    # its own is a round trip it can await.

    async def aopen(
        self,
        cfg: Config,
        session_id: str,
        /,
        *,
        catalogue: Definitions | None = None,
    ) -> Any:
        """`open`, for the async path."""
        return await off_loop(thread_pool(), self.open, cfg, session_id, catalogue=catalogue)

    async def asessions(self, cfg: Config) -> tuple[tuple[str, float], ...]:
        """`sessions`, for the async path."""
        return await off_loop(thread_pool(), self.sessions, cfg)

    async def adelete(self, cfg: Config, session_id: str) -> str | None:
        """`delete`, for the async path."""
        return await off_loop(thread_pool(), self.delete, cfg, session_id)

    async def amark_used(self, cfg: Config, session_id: str) -> None:
        """`mark_used`, for the async path."""
        return await off_loop(thread_pool(), self.mark_used, cfg, session_id)

    async def asize(self, cfg: Config, session_id: str) -> int:
        """`size`, for the async path."""
        return await off_loop(thread_pool(), self.size, cfg, session_id)

    async def aclaim(
        self, cfg: Config, session_id: str, /, *, stale_after: float, now: float | None = None
    ) -> bool:
        """`claim`, for the async path."""
        return await off_loop(
            thread_pool(), self.claim, cfg, session_id, stale_after=stale_after, now=now
        )

    async def arelease(self, cfg: Config, session_id: str, /) -> None:
        """`release`, for the async path."""
        return await off_loop(thread_pool(), self.release, cfg, session_id)
