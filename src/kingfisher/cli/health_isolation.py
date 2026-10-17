"""doctor's checks on whether one session's shell stays inside its session."""

from __future__ import annotations

import platform
from collections.abc import Iterator

from kingfisher import Config
from kingfisher.cli.check import Check
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

#: Appended to every answer this check gives, because it qualifies all of them.
FROM_CONFIG = " (from configuration; an injected runner is not visible here)"


def _mechanism(confined: Confinement) -> str:
    """What is doing the confining, named rather than implied."""
    named = (
        "bubblewrap (Landlock is unavailable here, and the shell has no network)"
        if confined.mechanism == confinement.BUBBLEWRAP
        else confined.mechanism or "the platform's sandbox"
    )
    # A supplied runner that is *local* still receives the confined command, so
    # the mechanism above holds -- it is just no longer the whole story, and an
    # operator asking what runs their commands deserves the rest of it.
    return f"{named}, with commands run by a supplied runner" if confined.supplied else named


def _or_bubblewrap() -> str:
    """What is left when Landlock is not an option, which is the case that matters: EKS
    nodes are commonly on 6.1, where a full ruleset is unavailable and kingfisher
    would otherwise have nothing to suggest but a container.
    """
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

    Asked through `confinement`, the same questions the fence asks of the same probes,
    so this advice and the fence cannot disagree about what the host can do.
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
        # Installed and refusing this kernel is not the same missing half as absent,
        # and it used to be told to `pip install` what it already had.
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
    """The shell settings of kingfisher's own sessions, or `None` for sessions whose
    shell this command cannot see into.

    By exact class, as `Kingfisher.origins` decides whose sessions they are: a subclass
    can open its backend with anything changed, the fence included.
    """
    # Here rather than at the top: `LocalSessions` loads deepagents, and the command
    # imports this module before it knows its verb.
    from kingfisher.plugins import LocalSessions  # noqa: PLC0415

    return sessions.shell if type(sessions) is LocalSessions else None


def named(sessions: object) -> str:
    """Whose sessions these are, as a reader can look them up."""
    return f"{type(sessions).__module__}.{type(sessions).__qualname__}"


def _shell(cfg: Config, sessions: object) -> Iterator[Check]:
    """What is keeping `execute` off the host, if anything.

    The check most worth having and the one that was hardest to reach: it lived
    in `tests/integration/driver.py`, which an installed kingfisher does not
    have. An unconfined
    shell is a warning rather than a failure because plenty of deployments mean
    it -- but silence would make an unconfined one look exactly like a confined
    one, which is how this went unnoticed until it was measured.
    """
    confined = shell_confinement(cfg, shell_of(sessions) or ShellSettings.from_env())
    if confined.confined:
        yield Check("shell", "ok", f"confined by {_mechanism(confined)}{FROM_CONFIG}")
    elif confined.elsewhere:
        # The case `EXTERNAL` exists for, and reporting it as the warning below
        # would recreate the confusion it was invented to remove: a container
        # that mounts only the workspace looked exactly like nobody having
        # thought about it.
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


def _session_files(cfg: Config, sessions: object) -> Iterator[Check]:
    """Whether anything this command can see keeps the agent's shell out of `.harness`.

    Nothing signs what kingfisher keeps there -- the pinned agent, the conversation, a
    paused turn -- so a shell that can write it can rewrite them. Asked of what this
    command can see: the sessions it runs on, and the sandbox. A deployment wiring its
    own backends, graph or runner in code is out of its sight.
    A warning rather than a failure, because the deployment runs either way and may
    well be fenced by something this cannot look into.
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
    elif (why := harness_unfenced(cfg, shell)) is not None:
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


def _linked_sessions(cfg: Config, sessions: object) -> Iterator[Check]:
    """Sessions reached through a link, which a static profile cannot bind.

    `fenced_sessions` resolves the sessions root, so moving all of them with one
    symlink works and the fence follows. A single session that is itself a link lands
    where no rule names it, and no rule can: the profile is written per workspace,
    before the session exists, for the reason `_session_folder_denial` gives. What
    that costs is not a hole but a session nothing can use -- measured, every command
    in it failed at `cd` with `shell-init: error retrieving current directory`, which
    mentions no fence and sends the reader to their shell.

    A warning, like the rows around it, because the deployment runs: the other
    sessions in that workspace are untouched. Quiet where there is nothing to say.
    Only the sessions that exist when this runs -- one made later breaks the same way
    and is not here to be seen -- and only under the macOS mechanism, since the Linux
    fences are built from the session directory itself, so a link is followed when it
    is opened and the rules land on the real one.
    """
    if (shell := shell_of(sessions)) is None:
        return
    if shell_confinement(cfg, shell).mechanism != SANDBOX_EXEC:
        return
    root = fenced_sessions(cfg.workspace)
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


def _sessions_apart(cfg: Config, sessions: object) -> Iterator[Check]:
    """Whether one session's agent can read and change another session's files.

    `shell` says what the host is exposed to; this says the consequence for the people
    one workspace serves. A report, never a refusal: kingfisher is mostly run as a
    package, and on many servers no fence can start. Quiet where it cannot happen -- a
    Linux fence handing each turn its own session -- and for sessions this
    command cannot see into, whose contract asks it of them.
    """
    if (shell := shell_of(sessions)) is None:
        return
    confined = shell_confinement(cfg, shell)
    if confined.mechanism in LINUX_FENCES:
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


def checks(cfg: Config, sessions: object) -> Iterator[Check]:
    """These, in the order `examine` reports them."""
    yield from _shell(cfg, sessions)
    yield from _session_files(cfg, sessions)
    yield from _linked_sessions(cfg, sessions)
    yield from _sessions_apart(cfg, sessions)
