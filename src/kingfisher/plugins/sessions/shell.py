"""The agent's shell on this host: what it is handed, what fences it, and whether it runs
here."""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import uuid
from collections.abc import Mapping
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

from deepagents.backends import LocalShellBackend
from deepagents.backends.protocol import ExecuteResponse

from kingfisher.core.agent.prompting import ShellPrompt
from kingfisher.core.config import Config
from kingfisher.core.definitions.catalogue import Definitions
from kingfisher.core.interfaces import CommandRunner
from kingfisher.plugins.sessions.sandbox import confinement

from .links import UnlinkedBackend

_BASE_PATH: tuple[str, ...] = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")


def shell_env(
    cfg: Config,
    session_dir: Path,
    *,
    path_extra: tuple[str, ...] = (),
    catalogue: Definitions | None = None,
) -> dict[str, str]:
    """The explicit allowlist handed to the shell — no credentials.

    Not a fence: a redirected `HOME` moves where `~` resolves, and an absolute path still
    reaches the operator's files. Keeping those closed is `confinement`'s job.
    """
    path_parts = [str(Path(sys.executable).parent), *path_extra, *_BASE_PATH]
    env = {
        "PATH": ":".join(path_parts),
        # Inside the session, so `reap` sweeps what tools cache under `~` and
        # `session_bytes` counts it.
        "HOME": str(session_dir / cfg.layout.scratchpad),
        "LANG": "en_US.UTF-8",
        "LC_ALL": "en_US.UTF-8",
        "TMPDIR": str(session_dir / cfg.layout.scratchpad),
    }
    skills = (catalogue or Definitions.from_config(cfg)).skills
    env["KINGFISHER_SKILLS"] = str(skills_view(skills.root, skills.mounts, cfg.kingfisher_dir))
    return env


#: What kingfisher's own shell says of itself: its section, its paragraph under skills,
#: and its line in `execute`'s description.
_HERE = ("shell_here.md", "shell_here_skills.md", "shell_here_execute.md")


@lru_cache(maxsize=1)
def kingfishers_shell() -> ShellPrompt:
    """What kingfisher's own shell says of itself.

    It starts in the session root, with `TMPDIR` the scratchpad and `KINGFISHER_SKILLS`
    the catalogue, which is true of no runner a deployment supplies.
    """
    prompts = resources.files("kingfisher.plugins.sessions.prompts")
    prompt, skills, execute = (
        prompts.joinpath(name).read_text(encoding="utf-8").strip() for name in _HERE
    )
    return ShellPrompt(prompt=prompt, skills=skills, execute=execute, starts_in_session=True)


#: Where the shell's view of a catalogue with mounts is built. Inside
#: `Config.kingfisher_dir`, which the shell may already read and never write.
SKILLS_VIEW = "skills-view"


def skills_view(root: Path, mounts: Mapping[str, Path], kingfisher_dir: Path) -> Path:
    """Where the shell finds the catalogue, so `/skills/X` is `$KINGFISHER_SKILLS/X`.

    With mounts, a directory of symlinks: one per catalogue entry and one per mount
    label, which `Definitions` refuses to let collide.
    """
    if not mounts:
        return root
    links = {e.name: e.absolute() for e in sorted(root.iterdir())} if root.is_dir() else {}
    links.update({label: mount.absolute() for label, mount in mounts.items()})
    # Named for what it holds, so a catalogue that gained a skill gets a new view
    # rather than a stale one, and two turns building the same view build one.
    listing = "\n".join(f"{name}\t{target}" for name, target in sorted(links.items()))
    view = (
        kingfisher_dir / SKILLS_VIEW
        / hashlib.sha256(listing.encode()).hexdigest()[:16]
    )
    if view.is_dir():
        return view
    # Built aside and renamed into place, so no turn reads a half-built view. A rename
    # onto one another turn finished first fails, and that view is this one.
    staging = view.with_name(f"{view.name}.{os.getpid()}.{uuid.uuid4().hex}")
    staging.mkdir(parents=True)
    for name, target in links.items():
        (staging / name).symlink_to(target)
    try:
        staging.rename(view)
    except OSError:
        shutil.rmtree(staging)
    return view


def shell_skills(cfg: Config, catalogue: Definitions | None = None) -> tuple[Path, ...]:
    """Every directory the shell reaches skills through, as a run's fence is asked them.

    The view as well as its targets: every fence checks the resolved path, and the Linux
    ones also have to reach the links. The skills folder is not created where missing: a
    start only reads the definitions folder, and a missing one means no skills.
    """
    skills = (catalogue or Definitions.from_config(cfg)).skills
    view = skills_view(skills.root, skills.mounts, cfg.kingfisher_dir) if skills.mounts else None
    return (skills.root, *skills.mounts.values(), *((view,) if view else ()))


class ConfinedLocalShellBackend(UnlinkedBackend, LocalShellBackend):
    """`LocalShellBackend` with every command run through a confinement.

    `runner=None` runs it here through upstream, rather than a default runner copying
    upstream's truncation, timeout and exit-code shaping. The confinement is applied
    before any runner sees the command, so a runner cannot forget to.
    """

    def __init__(
        self,
        confined: confinement.Confinement,
        *,
        runner: CommandRunner | None = None,
        shell_prompt: ShellPrompt | None = None,
        **kwargs: Any,
    ) -> None:
        self.confinement = confined
        self.runner = runner
        #: What this shell says of itself, which `shell_prompt_of` reads. Set by
        #: `backend_at`, which knows whether the runner is kingfisher's own.
        self.shell_prompt = shell_prompt
        super().__init__(**kwargs)

    @property
    def runs_here(self) -> bool:
        """Whether commands run on this host, which `shell_runs_here` reads."""
        return self.runner is None or runs_locally(self.runner)

    def execute(self, command: str, *, timeout: int | None = None) -> Any:
        if self.runner is None:
            return super().execute(self.confinement.wrap(command), timeout=timeout)
        # A confinement is a prefix naming paths on *this* host: shipped elsewhere, it
        # fails looking like a broken remote shell rather than a wrong prefix.
        outcome = self.runner.run(
            self.confinement.wrap(command) if runs_locally(self.runner) else command,
            timeout=timeout,
        )
        return ExecuteResponse(
            output=outcome.output,
            exit_code=outcome.exit_code,
            truncated=outcome.truncated,
        )


def runs_locally(runner: Any) -> bool:
    """Whether a supplied runner runs the command on this machine.

    The one reading, so whether a command is wrapped and what its `Confinement` claims
    cannot disagree. A duck-typed runner inherits no `local` from the Protocol, and the
    safe default is local. The contract kit keeps its own reading on purpose: importing
    this module would load deepagents to share one `getattr`.
    """
    return getattr(runner, "local", True)


def fence_for(  # noqa: PLR0913 -- one argument per thing a fence is built from
    cfg: Config,
    session_dir: Path,
    confined: confinement.Confinement,
    skills: tuple[Path, ...],
    env: Mapping[str, str],
    *,
    path_extra: tuple[str, ...] = (),
) -> CommandRunner | None:
    """The Linux fence, when the confinement says there is one."""
    if confined.mechanism not in confinement.LINUX_FENCES:
        return None

    # One list for both fences: a path added to one branch alone would fence the shell by
    # whichever mechanism the host has, and a run only exercises the one its kernel offers.
    # Not `readable_roots`: it returns the workspace, which holds every other tenant's
    # session.
    readable = [*confinement.toolchain_roots(path_extra), *skills]
    # Empty: both fences grant the session, and `TMPDIR` is inside it.
    writable: list[Path] = []

    if confined.mechanism == confinement.BUBBLEWRAP:
        from kingfisher.plugins.sessions.sandbox.bubblewrap import (  # noqa: PLC0415
            BubblewrapRunner,
            argv_for,
        )

        return BubblewrapRunner(
            argv_for(
                session_dir,
                cfg.layout,
                readable=readable,
                writable=writable,
            ),
            env=env,
        )

    # Imported here: `sandlock` is a Linux-only optional install, and this runs on every
    # platform.
    from kingfisher.plugins.sessions.sandbox.fence import (  # noqa: PLC0415
        LandlockRunner,
        policy_for,
    )

    return LandlockRunner(
        policy_for(
            session_dir,
            cfg.layout,
            readable=readable,
            writable=writable,
        ),
        cwd=session_dir,
        env=env,
    )
