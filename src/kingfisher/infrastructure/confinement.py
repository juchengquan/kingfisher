"""Keeping the shell out of everything that is not the workspace.

`virtual_mode` roots the *file tools* at a session. It does nothing to
`execute`, which deepagents documents plainly: commands run through
`subprocess.run(shell=True)` and can "access any file on the filesystem
(regardless of `virtual_mode`)", with the advice to pair it with a
human-in-the-loop. A harness that runs unattended has no such reviewer, so the
boundary has to come from the operating system instead.

What that boundary is worth closing was measured rather than assumed. With
nothing in place the agent's shell could read the deployment's own `.env` --
both API keys -- along with `~/.aws` and `~/.config/gh`, where the GitHub CLI
keeps its token. `http_fetch` is a registered tool, so reading and sending are
one turn apart for anything that gets an injection into a document.

Redirecting `HOME` at the workspace, which `shell_env` already does, is not this
guarantee and was never able to be: it only moves where tools *resolve* `~`. The
files stay where they are and an absolute path still reaches them.

Deployments differ in who provides the boundary, which is why this is a choice
rather than a constant. A container that mounts only the workspace has already
provided it and should not pay for a second one; a developer's machine has
provided nothing at all. `EXTERNAL` is the first case said out loud, so that
"nothing is wrapping the shell" can mean "the runtime already did it" instead of
being indistinguishable from nobody having thought about it.
"""

from __future__ import annotations

import platform
import shlex
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from kingfisher.config import Config

#: Pick whatever the platform offers, and say so when it offers nothing.
AUTO = "auto"
#: The runtime already confines this process -- a container mounting only the
#: workspace. Nothing is wrapped and nothing is warned about, because the
#: deployment has asserted the boundary exists somewhere this code cannot see.
EXTERNAL = "external"
#: Deliberately unconfined. Warned about on every start, because an exposure
#: nobody is reminded of is one nobody fixes.
OFF = "off"

MODES = (AUTO, EXTERNAL, OFF)


@dataclass(frozen=True)
class Confinement:
    """How to run one command, and what to say about it at startup."""

    #: Wraps a command so it runs confined. Identity when nothing is applied.
    wrap: Callable[[str], str]
    #: Empty when the shell is confined, or when a deployment has said it is
    #: confined elsewhere. Non-empty text is printed once at startup.
    warning: str = ""

    @property
    def confined(self) -> bool:
        return self.warning == "" and self.wrap is not _unwrapped


def _unwrapped(command: str) -> str:
    return command


def shell_confinement(cfg: Config, *, skills: Path | None = None) -> Confinement:
    """The confinement this deployment will actually use, from its `Config`.

    `resolve` takes one argument per root the profile has to name, and two
    callers were assembling those six from the same `Config` -- the backend that
    runs commands, and a driver that warns when nothing is confining them. Two
    assemblies of one fact is how they come to disagree, and disagreeing here
    means warning about a confinement other than the one in force.

    `skills` overrides `cfg.skills_dir` because the backend has a derived one: a
    session's skills directory is not always the workspace's. Nothing else
    varies, so nothing else is a parameter.
    """
    return resolve(
        cfg.shell_sandbox,
        workspace=cfg.workspace,
        state_dir=cfg.state_dir,
        scratch_dir=cfg.scratch_dir,
        extra=cfg.shell_path_extra,
        skills=cfg.skills_dir if skills is None else skills,
    )


def profile(
    *,
    home: Path,
    readable: tuple[Path, ...],
    writable: tuple[Path, ...],
    protected: tuple[Path, ...] = (),
) -> str:
    """A `sandbox-exec` profile denying the operator's home, minus what runs code.

    Deny-the-home rather than allow-only-the-workspace, deliberately. An
    allow-list is the stronger shape and the one to grow into, but it fails
    closed on every path a workload happens to need -- fonts, certificates, a
    homebrew prefix -- and this is on by default. Denying the one directory
    where a person's credentials actually live closes the measured hole at a
    fraction of the breakage risk.

    The re-allowed paths are not a convenience. A virtualenv's `python3` is
    typically a symlink onto an interpreter installed under the home -- uv puts
    it in `~/.local/share/uv/python/...` -- so denying the home without
    re-allowing `sys.base_prefix` leaves the agent unable to run Python at all.
    That was found by doing it.

    Writes are the other direction and take the opposite shape: an allow-list,
    denied from `/` down. `system.md` already tells the agent to stop rather
    than reach outside the workspace, and to write scratch under `$TMPDIR`
    rather than a literal `/tmp`. Both were ignored inside a single observed
    run -- it wrote `/tmp/preview.pdf` and twice tried `pip install`, which
    failed only because this venv happens to have no `pip`. Prose that the
    model overrides is not a boundary; this is the same rules with the kernel
    behind them.

    An allow-list is affordable here where it was not for reads, because what a
    shell legitimately writes to is short and known: the workspace, `$TMPDIR`,
    and the character devices that make `2>/dev/null` work.

    The known cost, and it is now measured rather than guessed at: a program
    that writes to the operating system's own temp directory stops working, even
    when everything it was *told* to write is inside the workspace.

    Headless Chrome is the proven case. Unsandboxed it renders a PDF in 2.0s;
    under this profile it fails in 0.4s with `Failed to create a ProcessSingleton
    for your profile directory`. The cause is this rule and no other -- the same
    profile with writes unrestricted works, and adding socket permissions does
    not help. No flag avoids it: `--user-data-dir` inside the workspace still
    fails, because Chrome reaches `/private/var/folders/<user>` regardless. An
    earlier note here said the case was unproven because Chrome "hung"; it does
    not hang, it writes the PDF and then never exits, so the measurement was
    watching the wrong thing.

    Left broken deliberately. Nothing here needs a browser: not this codebase,
    not the definitions a pack ships, and not the `pdf` skill, which prescribes `pypdf`,
    `pdfplumber`, `pdftotext`, `qpdf` and `reportlab`. Chrome appeared once,
    when an agent improvised it to look at its own HTML output. An agent
    spawning a network-capable browser is nearer to what a boundary is for than
    to something worth widening one to keep.

    The fix, if a tool anyone actually depends on ever needs it, is one line:
    allow writes to this user's own temp folder -- the parent of
    `tempfile.gettempdir()`, not all of `/private/var/folders`. Verified to make
    Chrome work while `.env`, the home and the repository stay refused. It is
    not here because it should be added for a dependency, not for a guess.
    """
    lines = [
        "(version 1)",
        "(allow default)",
        f"(deny file-read* (subpath {_sb(home)}))",
    ]
    lines += [f"(allow file-read* (subpath {_sb(p)}))" for p in readable]
    # Re-allowing a directory inside the home re-opens the destination and not
    # the way to it, and something has to say the way is walkable. Metadata on
    # the exact directories in between, never a subpath: that is `stat`, which
    # is all a walk needs, and it leaks nothing -- the home stays unlistable and
    # every file in it stays unreadable. See `traversable`.
    lines += [
        f"(allow file-read-metadata (path {_sb(p)}))" for p in traversable(home, readable)
    ]
    lines.append('(deny file-write* (subpath "/"))')
    lines += [f"(allow file-write* (subpath {_sb(p)}))" for p in writable]
    # `2>/dev/null` is in half the commands an agent writes, and stdout and
    # stderr are themselves entries here.
    lines.append('(allow file-write* (subpath "/dev"))')
    # Last, because sandbox-exec takes the last matching rule. `protected` names
    # directories that must stay read-only even though they sit inside somewhere
    # writable -- which is the default layout for the skills catalogue, since it
    # lives in the workspace unless a deployment relocates it. Written after the
    # allows rather than instead of them: the workspace has to stay writable, and
    # only this carve-out inside it does not.
    lines += [f"(deny file-write* (subpath {_sb(p)}))" for p in protected]
    return "\n".join(lines) + "\n"


def _sb(path: Path) -> str:
    """A path as a sandbox-profile string literal."""
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def traversable(home: Path, readable: tuple[Path, ...]) -> tuple[Path, ...]:
    """The directories between the denied home and each root re-allowed inside it.

    `deny (subpath ~)` followed by `allow (subpath ~/x/ws)` describes a
    destination with no route: `~` and `~/x` are still denied, and they are what
    a path to the workspace goes through. Whether that matters depends entirely
    on how a program asks. `chdir` and `open` hand the whole path to the kernel
    and resolve in one operation, so they succeed -- which is most things, and
    why this survived so long. A program that canonicalises component by
    component gets refused on the first denied one.

    Two do it, and both are in the hot path. `/bin/sh` is the shell every
    command runs in, and its `cd` builtin walks the path: `cd runs/t001` came
    back "Not a directory" for every directory in the workspace, which reads as
    a broken run directory rather than as a permission rule. `uv` walks it too,
    and reports "failed to canonicalize path" for the venv's own `python3` --
    so the agent could run Python but nothing could inspect or extend it.

    Only the directories, and only `file-read-metadata`. The alternative was to
    stop denying the home as a subpath and deny its contents individually, which
    is a list nobody can keep complete. This adds `stat` on a handful of exact
    paths -- not their contents, not their entries -- so the home stays
    unlistable and every file in it stays unreadable.

    Roots outside the home contribute nothing: `/opt/homebrew/bin` was never
    denied, so nothing has to be said about `/opt`.
    """
    home = Path(home)
    found: dict[Path, None] = {}
    for root in readable:
        if root == home or not root.is_relative_to(home):
            continue
        for parent in root.parents:
            found.setdefault(parent, None)
            if parent == home:
                break
    return tuple(found)


def readable_roots(workspace: Path, extra: tuple[str, ...] = (),
                   skills: Path | None = None) -> tuple[Path, ...]:
    """What has to stay readable for the shell to remain useful.

    `sys.prefix` is the virtualenv, `sys.base_prefix` the interpreter it was
    built from; they differ exactly when a venv is in use, which is the case
    that breaks. `extra` carries `shell_path_extra`, since a deployment that
    added a directory to the agent's `PATH` meant for it to be runnable.

    `skills` is the catalogue, and it is here because it is the one definition
    directory the *shell* reads: skills ship scripts, and running one is the
    point of `KINGFISHER_SKILLS`. It defaults inside the workspace and is
    already covered there, so this only matters when `KINGFISHER_SKILLS_DIR`
    moves it out -- which is the whole reason that setting exists, a catalogue
    shared by several deployments.

    Without it the agent got a split view rather than a refusal: file tools are
    routed and reached the catalogue, the shell was denied, so reading a skill's
    definition worked while running the script beside it did not. Subagent and
    tool directories are deliberately absent -- those are read by this process,
    never by the shell.
    """
    roots = [Path(workspace), Path(sys.prefix), Path(sys.base_prefix)]
    roots += [Path(e) for e in extra]
    if skills is not None:
        roots.append(Path(skills))
    return tuple(dict.fromkeys(p.resolve() for p in roots if str(p)))


def writable_roots(workspace: Path, scratch: Path) -> tuple[Path, ...]:
    """Everywhere the shell is allowed to write.

    `scratch` is named separately rather than assumed to sit inside the
    workspace, because `KINGFISHER_SCRATCH_DIR` and `KINGFISHER_STATE_DIR` can
    move it out. It is the directory handed to the shell as `TMPDIR`, so leaving
    it off would deny the one place the prompt tells the agent to put scratch.
    """
    roots = (Path(workspace), Path(scratch))
    return tuple(dict.fromkeys(p.resolve() for p in roots))


def _sandbox_exec(profile_path: Path) -> Callable[[str], str]:
    def wrap(command: str) -> str:
        # The inner `/bin/sh -c` is what deepagents would have run anyway; the
        # quoting keeps the agent's command one argument rather than letting its
        # operators reach the outer shell.
        return f"sandbox-exec -f {shlex.quote(str(profile_path))} /bin/sh -c {shlex.quote(command)}"

    return wrap


def resolve(  # noqa: PLR0913 -- one parameter per root the profile has to name,
    # and each is separately relocatable by its own environment variable
    mode: str, *, workspace: Path, state_dir: Path, scratch_dir: Path,
    extra: tuple[str, ...] = (), skills: Path | None = None,
) -> Confinement:
    """Choose a confinement for this deployment, writing any profile it needs.

    The profile is written under the harness's own state directory rather than
    the workspace: it is host-side configuration, and a file the agent could
    edit is not a boundary.
    """
    if mode == EXTERNAL:
        return Confinement(wrap=_unwrapped)
    if mode == OFF:
        return Confinement(
            wrap=_unwrapped,
            warning="the agent's shell is unconfined: it can read this host's files, "
                    "including credentials. Set KINGFISHER_SHELL_SANDBOX=auto to confine it.",
        )
    if mode != AUTO:
        msg = f"unknown shell sandbox mode {mode!r}; expected one of {MODES}"
        raise ValueError(msg)

    if platform.system() != "Darwin" or not shutil.which("sandbox-exec"):
        return Confinement(
            wrap=_unwrapped,
            warning=f"no shell confinement is wired for {platform.system()}, so the "
                    "agent's shell can read this host's files, including credentials. "
                    "Run it in a container that mounts only the workspace and set "
                    "KINGFISHER_SHELL_SANDBOX=external.",
        )

    home = Path.home().resolve()
    path = Path(state_dir) / "shell.sb"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        profile(
            home=home,
            readable=readable_roots(workspace, extra, skills),
            writable=writable_roots(workspace, scratch_dir),
            # The catalogue is instructions the agent follows, and by default it
            # sits inside the workspace -- so "the workspace is writable" made a
            # skill something the agent could rewrite for every later request,
            # including in the other deployments sharing a relocated one. Read at
            # the tool level too, by `SKILLS_ARE_READ_ONLY`; both are needed,
            # because the shell bypasses tool permissions entirely.
            protected=(Path(skills).resolve(),) if skills is not None else (),
        ),
        encoding="utf-8",
    )
    return Confinement(wrap=_sandbox_exec(path))
