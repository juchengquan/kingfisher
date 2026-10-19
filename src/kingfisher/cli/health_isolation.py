"""doctor's checks on whether one session's shell stays inside its session."""

from __future__ import annotations

import platform
from collections.abc import Iterator

from kingfisher import Config, ConfigError
from kingfisher.plugins import ShellSettings
from kingfisher.plugins.sessions.sandbox import confinement
from kingfisher.plugins.sessions.sandbox.bubblewrap import bubblewrap_available
from kingfisher.plugins.sessions.sandbox.confinement import (
    LINUX_FENCES,
    SANDBOX_EXEC,
    Confinement,
    harness_unfenced,
    shell_confinement,
)
from kingfisher.plugins.sessions.sandbox.sandbox_exec import fenced_sessions

from .check import Check

#: Appended to every answer this check gives, because it qualifies all of them.
FROM_CONFIG = " (from configuration; an injected runner is not visible here)"


def _mechanism(confined: Confinement) -> str:
    """What is doing the confining, named rather than implied."""
    named = (
        "bubblewrap (Landlock is unavailable here, and the shell has no network)"
        if confined.mechanism == confinement.BUBBLEWRAP
        else confined.mechanism
    )
    # A local supplied runner still receives the confined command, so the mechanism
    # holds; it is just not the whole story.
    return f"{named}, with commands run by a supplied runner" if confined.supplied else named


def _or_bubblewrap() -> str:
    """What is left without Landlock's full ruleset, as on the 6.1 kernels EKS nodes often run."""
    if bubblewrap_available():
        return (
            "bubblewrap works here, so set KINGFISHER_SHELL_SANDBOX=bubblewrap -- it "
            "closes the shell's network too, and this container already permits the "
            "one seccomp rule it needs"
        )
    return (
        "bubblewrap cannot be used here either -- it needs a seccomp profile "
        "permitting `clone` with CLONE_NEWUSER, which Docker's default denies -- so "
        "run it in a container that mounts only the workspace and set "
        "KINGFISHER_SHELL_SANDBOX=external"
    )


def _what_this_host_could_do() -> str:
    """The remedy, from what the kernel actually answers rather than its name.

    Asked through the fence's own probes, so the advice and the fence cannot disagree.
    """
    if platform.system() != "Linux":
        return "set KINGFISHER_SHELL_SANDBOX, or confine the process itself"
    release, abi = platform.release(), confinement.landlock_abi()
    if abi is None:
        return f"this kernel ({release}) offers no Landlock. {_or_bubblewrap()}"
    if abi < confinement.REQUIRED_LANDLOCK_ABI:
        return (
            f"this kernel ({release}) has Landlock ABI {abi}, below the "
            f"{confinement.REQUIRED_LANDLOCK_ABI} a full ruleset needs -- a fence here "
            f"would be weaker than one on a newer node. {_or_bubblewrap()}"
        )
    if not confinement.landlock_ready():
        # Installed but refusing this kernel, which `pip install` would not fix.
        wants = confinement.sandlock_minimum()
        if wants is not None:
            return (
                f"this kernel ({release}) has Landlock ABI {abi} and the installed "
                f"`sandlock` needs {wants}: upgrade the kernel, or install a `sandlock` "
                f"that runs here. {_or_bubblewrap()}"
            )
        return (
            f"this kernel ({release}) has Landlock ABI {abi}, enough for a full fence, "
            "and `sandlock` is the missing half: pip install 'kingfisher[fence]', and "
            "KINGFISHER_SHELL_SANDBOX=auto, the default, fences `execute` here"
        )
    return (
        f"this kernel ({release}) has Landlock ABI {abi} and `sandlock` is installed, "
        "so KINGFISHER_SHELL_SANDBOX=auto, the default, fences `execute` here"
    )


def shell_of(sessions: object) -> ShellSettings | None:
    """The shell settings of kingfisher's own sessions, or `None` for any other.

    By exact class: a subclass can open its backend with anything changed, the fence included.
    """
    # Here rather than at the top: `LocalSessions` loads deepagents, and the command
    # imports this module before it knows its verb.
    from kingfisher.plugins import LocalSessions  # noqa: PLC0415

    return sessions.shell if type(sessions) is LocalSessions else None


def named(sessions: object) -> str:
    """Whose sessions these are, as a reader can look them up."""
    return f"{type(sessions).__module__}.{type(sessions).__qualname__}"


def fence_of(cfg: Config, sessions: object) -> Confinement:
    """The confinement a run of `sessions` gives its shell, asked once for every row.

    Once, because each asking rewrites the macOS profile, and rows asking separately can
    name different fences. Sessions doctor cannot see into get the environment's settings,
    which only the `shell` row reads: every other row is quiet or warns for them.
    """
    # Here, not at the top: it loads deepagents, as `LocalSessions` does.
    from kingfisher.plugins.sessions.shell import shell_skills  # noqa: PLC0415

    try:
        skills = shell_skills(cfg)
    except ConfigError:
        # A mount a run refuses before it has a shell. Raised here, it would drop every
        # row between this one and the row that says why.
        skills = None
    return shell_confinement(cfg, shell_of(sessions) or ShellSettings.from_env(), skills=skills)


def _shell(confined: Confinement) -> Iterator[Check]:
    """What is keeping `execute` off the host, if anything.

    An unconfined shell is a warning, not a failure, because plenty of deployments mean it;
    but said, because silence would make it look exactly like a confined one.
    """
    if confined.confined:
        yield Check("shell", "ok", f"confined by {_mechanism(confined)}{FROM_CONFIG}")
    elif confined.elsewhere:
        # `ok`, not the warning: a container mounting only the workspace must not
        # look like nobody having thought about it.
        yield Check(
            "shell", "ok", f"confined by the runtime, not by this process{FROM_CONFIG}"
        )
    else:
        yield Check(
            "shell",
            "warn",
            confined.warning or "nothing is confining `execute` to the workspace",
            _what_this_host_could_do(),
        )


def _session_files(sessions: object, confined: Confinement) -> Iterator[Check]:
    """Whether anything this command can see keeps the agent's shell out of `.harness`.

    Nothing signs what kingfisher keeps there, so a shell that can write it can rewrite the
    pinned agent, the conversation and a paused turn. A warning, because the deployment
    runs either way and may be fenced by something out of sight, such as backends in code.
    """
    if (shell := shell_of(sessions)) is None:
        yield Check(
            "session files",
            "warn",
            f"the sessions in use are {named(sessions)}, "
            "and nothing here can see whether their shell is kept out of .harness",
            "check it with shell_denied, in kingfisher's tests/checks/backend.py: it "
            "drives the shell at /.harness",
        )
    elif (why := harness_unfenced(shell, confined)) is not None:
        yield Check(
            "session files",
            "warn",
            f"{why}, so the agent's shell can rewrite the session's pinned agent, "
            "conversation and paused turn",
            "turn the sandbox on, or confine the shell another way -- shell_denied, in "
            "kingfisher's tests/checks/backend.py, says whether it holds",
        )
    else:
        yield Check(
            "session files", "ok", "the sandbox keeps the agent's shell out of .harness"
        )


def _linked_sessions(
    cfg: Config, sessions: object, confined: Confinement
) -> Iterator[Check]:
    """Sessions reached through a link, which a static profile cannot bind.

    `fenced_sessions` resolves the sessions root, so one link moving all of them is
    followed. A single session that is a link lands where no rule names it, since the
    profile is written per workspace before sessions exist, and every command in it
    fails at `cd` with an error naming no fence. A warning: the other sessions are
    untouched. Only the macOS mechanism, because the Linux fences are built from the
    opened session directory and so follow the link.
    """
    if shell_of(sessions) is None or confined.mechanism != SANDBOX_EXEC:
        return
    root = fenced_sessions(cfg.sessions_root)
    if not root.is_dir():
        return
    strayed = sorted(
        one.name
        for one in root.iterdir()
        if one.is_dir() and not one.resolve().is_relative_to(root)
    )
    if not strayed:
        return
    yield Check(
        "session paths",
        "warn",
        f"{', '.join(strayed)} {'is' if len(strayed) == 1 else 'are'} reached through a "
        f"link out of {root}, and the macOS profile matches real paths -- so every "
        f"command in {'it' if len(strayed) == 1 else 'them'} is refused, down to "
        "reading the session's own files",
        "keep each session inside the sessions directory -- or move all of them at "
        "once by making `sessions` itself the link, which the profile follows",
    )


def _sessions_apart(sessions: object, confined: Confinement) -> Iterator[Check]:
    """Whether one session's agent can read and change another session's files.

    A report, never a refusal: kingfisher is mostly run as a package, and on many servers
    no fence can start. Quiet under a Linux fence, which hands each turn its own session,
    and for sessions this cannot see into, whose contract asks it of them.
    """
    if shell_of(sessions) is None or confined.mechanism in LINUX_FENCES:
        return
    if confined.mechanism == SANDBOX_EXEC:
        why = "the macOS sandbox hides the home folder, not one session from another"
        remedy = "give each person a workspace of their own"
    elif confined.elsewhere:
        why = (
            "KINGFISHER_SHELL_SANDBOX is 'external', which keeps the host from the shell, "
            "not one session from another"
        )
        remedy = "give each person a workspace of their own"
    else:
        why = "nothing kingfisher runs confines the shell here"
        remedy = "turn the sandbox on, or give each person a workspace of their own"
    yield Check(
        "sessions apart",
        "warn",
        f"this workspace can hold many sessions and {why}, so one session's agent can "
        f"read and change another session's files",
        remedy,
    )


def checks(cfg: Config, sessions: object, confined: Confinement) -> Iterator[Check]:
    """These, in the order `examine` reports them, all of `fence_of`'s one answer."""
    yield from _shell(confined)
    yield from _session_files(sessions, confined)
    yield from _linked_sessions(cfg, sessions, confined)
    yield from _sessions_apart(sessions, confined)
