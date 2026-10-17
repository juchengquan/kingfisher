"""The agent's shell on this host: what it is handed, what fences it, and whether it runs
here."""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from deepagents.backends import LocalShellBackend
from deepagents.backends.protocol import ExecuteResponse

from kingfisher.core.agent.prompting import ShellPrompt
from kingfisher.core.config import Config
from kingfisher.core.definitions.catalogue import Definitions
from kingfisher.core.definitions.skills.catalogue import LocalSkillRepository
from kingfisher.core.interfaces import CommandRunner
from kingfisher.plugins.sessions.sandbox import confinement

_BASE_PATH: tuple[str, ...] = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")


def shell_env(
    cfg: Config,
    session_dir: Path,
    *,
    path_extra: tuple[str, ...] = (),
    catalogue: Definitions | None = None,
) -> dict[str, str]:
    """The explicit allowlist handed to the shell — no credentials.

    That is all it does. Redirecting `HOME` moves where a path is *resolved*; the
    files stay where they are, and an absolute path still reaches them. Measured on
    this machine, the shell could read `~/.aws` and `~/.config/gh` right through it.
    Keeping those closed is `confinement`'s job, not this function's.
    """
    path_parts = [str(Path(sys.executable).parent), *path_extra, *_BASE_PATH]
    env = {
        "PATH": ":".join(path_parts),
        # Inside the session, so `reap` sweeps what tools cache under `~` and
        # `session_bytes` counts it; above the session such caches reached 59MB
        # in one workspace with nothing sweeping them.
        "HOME": str(session_dir / cfg.layout.scratchpad),
        "LANG": "en_US.UTF-8",
        "LC_ALL": "en_US.UTF-8",
        "TMPDIR": str(session_dir / cfg.layout.scratchpad),
    }
    skills = (catalogue or Definitions.from_config(cfg)).skills
    env["KINGFISHER_SKILLS"] = str(skills_view(skills, cfg.kingfisher_dir))
    return env


#: Where the shell's view of a catalogue with mounts is built, one directory per
#: arrangement of it. Inside `Config.kingfisher_dir` because the shell must read through
#: it and never write it, and that directory is already both.
SKILLS_VIEW = "skills-view"


def skills_view(skills: LocalSkillRepository, kingfisher_dir: Path) -> Path:
    """Where the shell finds the catalogue, so `/skills/X` is `$KINGFISHER_SKILLS/X`.

    The catalogue itself when nothing is mounted. With mounts, a directory of
    symlinks: one per entry of the catalogue and one per mount under its label,
    which `refuse_unmountable` has already made sure cannot collide.
    """
    if not skills.mounts:
        return skills.root
    root = skills.root
    links = {e.name: e.absolute() for e in sorted(root.iterdir())} if root.is_dir() else {}
    links.update({label: mount.absolute() for label, mount in skills.mounts.items()})
    # Named for what it holds, so a catalogue that gained a skill gets a new view
    # rather than a stale one, and two turns building the same view build one.
    listing = "\n".join(f"{name}\t{target}" for name, target in sorted(links.items()))
    view = (
        kingfisher_dir / SKILLS_VIEW
        / hashlib.sha256(listing.encode()).hexdigest()[:16]
    )
    if view.is_dir():
        return view
    # Built aside and renamed into place, so no turn ever reads a half-built view.
    # A rename onto a directory that another turn finished first fails, and that
    # turn's view is this one.
    staging = view.with_name(f"{view.name}.{os.getpid()}.{uuid.uuid4().hex}")
    staging.mkdir(parents=True)
    for name, target in links.items():
        (staging / name).symlink_to(target)
    try:
        staging.rename(view)
    except OSError:
        shutil.rmtree(staging)
    return view


class ConfinedLocalShellBackend(LocalShellBackend):
    """`LocalShellBackend` with every command run through a confinement.

    `None` means run it here, which is what upstream already does -- a default runner
    would be 110 lines of upstream's truncation, timeout and exit-code shaping,
    copied to be kept in step. The confinement is applied either way, before the
    runner sees the command, so a runner cannot forget to.
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
        # A confinement is a command prefix naming paths on *this* host, so
        # applying it to something that runs elsewhere produces a
        # `sandbox-exec -f /Users/.../shell.sb` shipped to a machine with no
        # such file -- which fails looking like a broken remote shell rather
        # than like a wrong prefix.
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

    **The default is the safe one, and it is read here so it is one answer rather
    than two.** `CommandRunner` declares `local` as a property answering `True`, but a
    duck-typed runner -- the case this default exists for -- inherits nothing from a
    Protocol, so every reader has to supply the default itself. Two readers did: the
    shell decides whether to wrap a command with it, and `backend_at` decides what the
    `Confinement` beside that command claims. The second was written out separately
    and held by nothing: flipped to `False` it left all 2,109 tests passing, while a
    supplied local runner that says nothing got `wrap=_unwrapped` and ran with no
    sandbox at all, under a `Confinement` reporting `confined` as false and warning
    about nothing.

    `testing.a_runner_says_where_it_runs` keeps its own reading on purpose. It is the
    kit a deployment checks its own adapter with, and importing this module would take
    it from 70 loaded modules to 3,104 -- measured -- to share one `getattr`, because it
    reaches deepagents and deepagents reaches the provider SDKs.
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

    # One answer for both fences rather than the same three lines twice. `argv_for` and
    # `policy_for` take the same arguments and mean the same thing by them, so a path
    # added to one branch and not the other fences the shell differently depending on
    # which mechanism the host happens to have -- and the suite would not catch it,
    # because a run only ever exercises the one mechanism its own kernel offers. That is
    # the divergence this function already avoids one question earlier by deriving from
    # `Confinement`.
    #
    # Not `readable_roots`, which is the obvious call and the wrong one: it also returns
    # the *workspace*, which is right where the home is denied and the workspace
    # re-allowed inside it, and catastrophic here. Sessions live under the workspace, so
    # granting it hands every tenant back the directory this fence exists to take away
    # -- measured, before the fence: tenant B read tenant A's `outputs/secret.txt` with
    # `cat ../<A>/...`, exit 0.
    readable = [*confinement.toolchain_roots(path_extra), *skills]
    # Nothing beyond the session, which both fences grant already: `TMPDIR` is
    # inside it now, so the writable set is exactly the session this turn owns.
    writable: list[Path] = []

    if confined.mechanism == confinement.BUBBLEWRAP:
        from kingfisher.plugins.sessions.sandbox.bubblewrap import (  # noqa: PLC0415
            BubblewrapRunner,
            argv_for,
        )

        return BubblewrapRunner(
            argv_for(
                session_dir,
                readable=readable,
                writable=writable,
                inputs=cfg.layout.inputs,
                saved_results=cfg.layout.saved_results,
            ),
            env=env,
        )

    # Imported here for the reason the module explains: `sandlock` is a
    # Linux-only optional install, and this function is called on every turn on
    # every platform.
    from kingfisher.plugins.sessions.sandbox.fence import (  # noqa: PLC0415
        LandlockRunner,
        policy_for,
    )

    return LandlockRunner(
        policy_for(
            session_dir,
            readable=readable,
            writable=writable,
            session_dirs=cfg.layout.session_dirs,
            inputs=cfg.layout.inputs,
        ),
        cwd=session_dir,
        env=env,
    )
