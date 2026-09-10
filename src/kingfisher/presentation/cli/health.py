"""Every check that stands between an install and a run."""

from __future__ import annotations

import os
import platform
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from kingfisher import Config, ConfigError, Inventory, inventory, kinds_at
from kingfisher.domain.session import sessions_root

# Everything below is past the front door on purpose, and this is the file to read if
# you want to know what that door is now for. These are `doctor`'s probes: what fences
# this host offers, what the kernel supports, what the cgroup files say, and the
# sentence that tells a reader where their own definitions go. Every one of them was
# public because *this file* reached for it -- the export table said so in as many words
# -- and a probe only `doctor` has ever wanted is not a promise worth making to
# everybody who runs `pip install kingfisher`. The command ships in this wheel, so it
# takes each name where it lives.
from kingfisher.infrastructure.catalogue import DEFINITION_KINDS
from kingfisher.infrastructure.sandbox.bubblewrap import bubblewrap_available
from kingfisher.infrastructure.sandbox.confinement import (
    Confinement,
    landlock_abi,
    shell_confinement,
)
from kingfisher.infrastructure.workspace.backing import MemoryBacking, memory_backing
from kingfisher.infrastructure.workspace.seeding import destination_hint

#: `fail` means this deployment will not run. `warn` means it will, and
#: something about it is worth knowing -- an unconfined shell runs fine.
Verdict = Literal["ok", "warn", "fail"]

#: Settings nothing reads any more, and what to write instead. Renaming an
#: environment variable is the one rename that fails in silence: a moved import
#: stops the program and says which, while a variable nobody reads falls back to
#: its default -- a server on port 8000, skills switched off -- with nothing
#: anywhere to connect the behaviour to the line that used to cause it. Reading
#: both names was how that was survivable; this list is what replaced it, and it
#: is the only thing in the tree that will ever mention one of these to a
#: deployment still carrying it.
RETIRED: dict[str, str] = {
    "KINGFISHER_SKILLS": "KINGFISHER_SKILLS_ENABLED",
    "KINGFISHER_MEMORY": "KINGFISHER_MEMORY_ENABLED",
    "KINGFISHER_INTERPRETER": "KINGFISHER_INTERPRETER_ENABLED",
    "KINGFISHER_CONVERSATION": "KINGFISHER_CONVERSATION_ENABLED",
    "KINGFISHER_TIMEOUT_S": "KINGFISHER_EXECUTION_TIMEOUT_S, and timeout_s per model",
    "KINGFISHER_MODEL": "models.yaml, where a model names its endpoint",
    "KINGFISHER_API_STYLE": "models.yaml, where an endpoint names its wire format",
    "KINGFISHER_MAX_TOKENS": "models.yaml, beside the model it bounds",
    "KINGFISHER_MODEL_SUBAGENT": "`model:` in the subagent's own .yaml",
    "KINGFISHER_PROVIDER_SUBAGENT": "the endpoint that model names",
    "KINGFISHER_STATE_DIR": "nothing -- run logs, claims and pinned agents are "
                            "inside the session they belong to",
    "KINGFISHER_SCRATCH_DIR": "nothing -- TMPDIR is a directory inside the session",
}

#: The prefix that went whole, matched rather than enumerated. Every service
#: setting was renamed at once, so listing the suffixes here would put the
#: service's own table in the base package for the second time -- and would
#: quietly stop covering whichever one it gains next.
RETIRED_PREFIX = "KINGFISHER_SERVER_"

#: What that prefix became. Spelled rather than imported, because `doctor` runs
#: in deployments where the service is not installed at all.
SERVICE_PREFIX = "KINGFISHER_SERVICE_"


@dataclass(frozen=True)
class Check:
    """One question, its answer, and what to do about it."""

    name: str
    verdict: Verdict
    detail: str
    remedy: str = ""


def _catalogue(cfg: Config) -> Iterator[Check]:
    """The model catalogue: that it loaded, and that its names can be reached."""
    models = cfg.models
    yield Check(
        "catalogue",
        "ok",
        f"{len(models.models)} model(s) on {len(models.endpoints)} endpoint(s), "
        f"default {models.default!r} -- credentials present, not tested",
    )

    # A dropped endpoint is a warning, not a failure. A shared catalogue naming
    # endpoints this machine cannot reach is the normal case by the loader's own account
    # -- "one reviewed file works across a fleet holding different subsets of keys" --
    # so failing here would fail on the arrangement the format encourages. It becomes a
    # failure through the definitions check, when something actually names one.
    if models.unreachable:
        named = ", ".join(f"{name} ({why})" for name, why in sorted(models.unreachable.items()))
        yield Check(
            "credentials",
            "warn",
            f"{len(models.unreachable)} model(s) this file defines cannot be reached: {named}",
            "set the variable each one names, or ignore this if no definition wants them",
        )
    else:
        yield Check("credentials", "ok", "every endpoint this file names has a key")


def _packs(cfg: Config) -> Iterator[Check]:
    """Whether there is anywhere to seed definitions from."""
    if cfg.assets is None:
        yield Check(
            "definitions to seed",
            "warn",
            "KINGFISHER_ASSETS is not set",
            "set it to a directory of definitions, or pass"
            f" `kingfisher seed --from DIR`{destination_hint()}",
        )
        return
    if not cfg.assets.is_dir():
        yield Check(
            "definitions to seed",
            "warn",
            f"{cfg.assets} does not exist",
            "check the path, or fetch the definitions into it",
        )
        return
    kinds = kinds_at(cfg.assets)
    if not kinds:
        yield Check(
            "definitions to seed",
            "warn",
            f"{cfg.assets} holds none of {', '.join(DEFINITION_KINDS)}",
            "point at the directory holding those, not at one inside it",
        )
        return
    yield Check("definitions to seed", "ok", f"{', '.join(kinds)} — from {cfg.assets}")


def _sessions_probe(workspace: Path) -> Path:
    """Where to measure a deployment's sessions, before it has any.

    `_size_of` stats the path, so a sessions directory that is not there yet
    reports no size -- and `doctor` before `kingfisher seed` is exactly when
    somebody is sizing a tmpfs. The workspace is the honest fallback: with
    nothing mounted, that is the filesystem sessions will land on anyway.
    """
    root = sessions_root(workspace)
    return root if root.is_dir() else Path(workspace)


def _devices(sessions: MemoryBacking, workspace: MemoryBacking) -> str:
    """Both readings, in every message this check yields.

    Named unconditionally rather than only where they differ: a reader deciding
    whether the numbers below are about the disk they mounted should not have to
    infer it from an absence.
    """
    return f"sessions on {sessions.filesystem}, workspace on {workspace.filesystem}"


def _at_rest(cfg: Config) -> Iterator[Check]:
    """Whether sessions kept in memory can actually keep nothing.

    Measured against the sessions tree rather than the workspace, and the two are
    not the same question once `<workspace>/sessions` is a mount:
    `_mounted_filesystem` answers for the longest mountpoint containing the path
    it is handed, so asking about the workspace answers about the workspace's
    device and never about the one sessions are actually written to.

    Where sessions *are* in memory, this is the one check here that can fail on
    something which appears to work. Measured, and it is not what the obvious
    reading predicts:

    - A memory filesystem **larger** than the container's memory limit does not
      refuse when it fills. The kernel swaps its pages out — data at rest, the
      write succeeding, no error anywhere. That is exactly the guarantee such a
      deployment has asserted, broken invisibly.
    - With swap disabled the same overrun becomes an **OOM kill**, which takes
      every session in the container rather than one.
    - Only a filesystem **smaller** than the limit gives a clean `ENOSPC` on a
      full one, which is a thing kingfisher can refuse on.
    """
    sessions = memory_backing(_sessions_probe(cfg.workspace))
    workspace = memory_backing(cfg.workspace)
    devices = _devices(sessions, workspace)
    if not sessions.in_memory:
        # The one thing worth saying about a sessions tree on a disk, and only
        # because the workspace claims otherwise: every check below gates on
        # sessions, so without this the arrangement that breaks the promise
        # loudest is the one that says nothing at all.
        if workspace.in_memory:
            yield Check(
                "nothing at rest",
                "fail",
                f"{devices} — a deployment that meant to keep nothing is keeping "
                "every session",
                "mount the sessions tree in memory too, or drop the tmpfs workspace",
            )
        return

    if sessions.swap_enabled:
        yield Check(
            "nothing at rest",
            "fail",
            f"{devices} — this cgroup permits swapping",
            "disable swap for the container (`--memory-swap` equal to `--memory`)",
        )
    if sessions.fits is False:
        yield Check(
            "nothing at rest",
            "fail",
            f"{devices} — {_mb(sessions.size_bytes)} of sessions against a memory limit of "
            f"{_mb(sessions.limit_bytes)}; filling it swaps to disk, or kills the container",
            "size the filesystem below the limit, leaving room for this process",
        )
    elif sessions.fits is None:
        yield Check(
            "nothing at rest",
            "warn",
            f"{devices} — and this process has no memory limit",
            "set one, so a full filesystem fails rather than exhausting the host",
        )
    elif not sessions.swap_enabled:
        yield Check(
            "nothing at rest",
            "ok",
            f"{devices} — {_mb(sessions.size_bytes)} under a "
            f"{_mb(sessions.limit_bytes)} limit, no swap",
        )

    # Two things that only matter once sessions are in memory, and both are
    # silent until the moment they are expensive.
    if cfg.session_store is None:
        yield Check(
            "sessions survive",
            "fail",
            f"{devices} — nothing is configured to keep sessions, so everything a "
            "session produced goes with the process",
            "set KINGFISHER_SESSION_STORE, or wire a SessionStore",
        )
    else:
        yield Check("sessions survive", "ok", f"{devices} — kept at {cfg.session_store}")

    if cfg.session_max_bytes is None:
        yield Check(
            "session quota",
            "fail",
            f"{devices} — no KINGFISHER_SESSION_MAX_BYTES, and sessions share "
            f"{_mb(sessions.size_bytes)} of memory; one can starve every other in "
            "this container",
            "set it below the filesystem size divided by the sessions you expect",
        )
    elif sessions.size_bytes is not None and cfg.session_max_bytes > sessions.size_bytes:
        yield Check(
            "session quota",
            "warn",
            f"{devices} — one session may reach {_mb(cfg.session_max_bytes)} and the "
            f"filesystem holds {_mb(sessions.size_bytes)}, so the quota can never bind",
            "lower it, or the limit is the filesystem and it arrives as a write failure",
        )
    else:
        yield Check(
            "session quota", "ok", f"{devices} — {_mb(cfg.session_max_bytes)} per session"
        )


def _mb(value: int | None) -> str:
    """Bytes as megabytes, because these numbers are read by people."""
    return "unknown" if value is None else f"{value // (1024 * 1024)}MB"


def _catalogues(found: Inventory) -> Iterator[Check]:
    """The three definition directories, each of which can fail on its own."""
    if found.tools_error is not None:
        yield Check("tools", "fail", found.tools_error, "fix or remove the module it names")
    else:
        yield Check(
            "tools",
            "ok",
            f"{len(found.tools)} in the workspace, {len(found.builtin_tools)} built in",
        )

    if found.subagents_error is not None:
        yield Check("subagents", "fail", found.subagents_error, "fix or remove the file it names")
    else:
        yield Check("subagents", "ok", f"{len(found.subagents)} defined")

    detail = f"{len(found.skills)} loadable"
    if not found.skills_enabled:
        detail += ", and KINGFISHER_SKILLS_ENABLED is off so none will be offered"
    # `misfiled` is not hidden -- the agent has it. It is a warning of its own
    # because the failure is a caller typing the directory name and being told
    # there is no such skill.
    hidden = found.skills_unloadable + found.skills_misplaced
    if found.skills_misfiled:
        yield Check(
            "skill names",
            "warn",
            f"{len(found.skills_misfiled)} offered under a name their directory "
            f"does not have: "
            + ", ".join(f"{d}/ as {n}" for d, n in found.skills_misfiled),
            "grant them by the name shown, or rename the directory to match",
        )
    if hidden:
        yield Check(
            "skills",
            "warn",
            f"{detail}; {len(hidden)} present and invisible to the agent: {', '.join(hidden)}",
            "run `kingfisher list` for why each one is not loadable",
        )
    else:
        yield Check("skills", "ok", detail)


def _where(cfg: Config, found: Inventory) -> Iterator[Check]:
    """Two ways a catalogue is somewhere other than you think."""
    for kind in DEFINITION_KINDS:
        origin = getattr(found.origins, kind)

        if origin.kind == "overridden":
            yield Check(
                f"{kind} directory",
                "warn",
                f"read from {origin.path}, while the configuration names "
                f"{cfg.catalogue_roots[kind]} — a catalogue was supplied when this "
                f"kingfisher was built, and the setting does nothing",
                "drop the setting, or point it at what is actually read",
            )
            continue

        # An empty catalogue at the derived path is a fresh workspace, and
        # `SEED_HINT` already covers that everywhere it matters. An empty one at
        # a path somebody *typed* is the other thing entirely -- and the two
        # look identical, because resolving a catalogue creates the directory it
        # was pointed at rather than refusing an absent one.
        if origin.kind == "relocated" and not _holds(found, kind):
            yield Check(
                f"{kind} directory",
                "warn",
                f"{origin.path} is where this deployment points {kind}, and it holds "
                f"none — a mistyped path is created rather than refused, so this "
                f"reads the same as a workspace nobody has seeded",
                "check the path, or seed it",
            )


def _holds(found: Inventory, kind: str) -> bool:
    """Whether a catalogue produced anything the agent can reach."""
    return bool(getattr(found, kind))


def _definitions(cfg: Config, found: Inventory) -> Iterator[Check]:
    """Which definitions this deployment cannot actually run.

    Imported inside the function. `unrunnable_delegates` reaches deepagents as it
    loads -- 868ms and 3,137 modules, measured -- and at module scope every other
    verb would pay it, so `kingfisher help` would cost a second to print text. The
    CLI starts in 40ms and should keep doing so.
    """
    # Asked only when the catalogue parsed. `unrunnable_delegates` reads the
    # same files, so a definition that will not load raises out of here instead
    # of being reported -- and a diagnosis that stops at the first problem is
    # what this command exists to replace. The check above already said so, so
    # this one says nothing rather than saying it twice.
    if found.subagents_error is not None:
        yield Check(
            "definitions run",
            "warn",
            "not checked -- the subagent catalogue did not load, which is above",
        )
        return

    from kingfisher.infrastructure.harness.activation import (  # noqa: PLC0415
        unrunnable_delegates,
    )

    unrunnable = unrunnable_delegates(cfg)
    if not unrunnable:
        yield Check("definitions run", "ok", "every definition resolves to a model")
        return
    for name, why in unrunnable:
        yield Check(
            f"definition {name!r}",
            "fail",
            why,
            "set the credential it needs, or bind its alias to a model you can run",
        )


#: What `sandlock` wants for its full ruleset. Below this it offers to run
#: degraded, which S6 of `2026-08-25-a-fence-for-the-shell.md` says to report
#: rather than accept quietly.
FULL_LANDLOCK_ABI = 6


#: Appended to every answer this check gives, because it qualifies all of them.
FROM_CONFIG = " (from configuration; an injected runner is not visible here)"


def _mechanism(confined: Confinement) -> str:
    """What is doing the confining, named rather than implied."""
    named = (
        "bubblewrap (Landlock is unavailable here, and the shell has no network)"
        if confined.mechanism == "bubblewrap"
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
    """The remedy, from what the kernel actually answers rather than its name."""
    if platform.system() != "Linux":
        return "set KINGFISHER_SHELL_SANDBOX, or confine the process itself"
    abi = landlock_abi()
    if abi is None:
        return (
            f"this kernel ({platform.release()}) offers no Landlock. {_or_bubblewrap()}"
        )
    if abi < FULL_LANDLOCK_ABI:
        return (
            f"this kernel ({platform.release()}) has Landlock ABI {abi}, below the "
            f"{FULL_LANDLOCK_ABI} a full ruleset needs -- a fence here would be weaker "
            f"than one on a newer node. {_or_bubblewrap()}"
        )
    return (
        f"this kernel ({platform.release()}) has Landlock ABI {abi}, which is enough to fence "
        "`execute` -- until that is wired, set KINGFISHER_SHELL_SANDBOX=external and run it in "
        "a container that mounts only the workspace"
    )


def _shell(cfg: Config) -> Iterator[Check]:
    """What is keeping `execute` off the host, if anything.

    The check most worth having and the one that was hardest to reach: it lived
    in `tests/integration/driver.py`, which an installed kingfisher does not
    have. An unconfined
    shell is a warning rather than a failure because plenty of deployments mean
    it -- but silence would make an unconfined one look exactly like a confined
    one, which is how this went unnoticed until it was measured.
    """
    confined = shell_confinement(cfg)
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


def _retired(environ: Mapping[str, str] | None = None) -> Iterator[Check]:
    """Settings this deployment still carries that nothing reads any more.

    Nothing warns about an unknown `KINGFISHER_` variable, so a line surviving an
    upgrade is silent by construction: it stops taking effect, and the first sign
    is behaviour nobody chose. A warning rather than a failure because the
    deployment does run -- on the defaults, which may well be what it wanted.
    """
    values = os.environ if environ is None else environ
    stale = {
        name: instead
        for name, instead in RETIRED.items()
        if (values.get(name) or "").strip()
    }
    # Matched on the prefix, so a suffix nobody thought to list is still caught.
    stale.update(
        {
            name: SERVICE_PREFIX + name[len(RETIRED_PREFIX) :]
            for name in values
            if name.startswith(RETIRED_PREFIX) and (values.get(name) or "").strip()
        }
    )
    if not stale:
        return

    yield Check(
        "retired settings",
        "warn",
        f"{', '.join(sorted(stale))} — set here, and read by nothing",
        "; ".join(f"{name} -> {instead}" for name, instead in sorted(stale.items())),
    )


def examine(cfg: Config, found: Inventory | None = None) -> tuple[Check, ...]:
    """Every check, in the order somebody diagnosing would want them."""
    checks: list[Check] = []
    try:
        # First, because it is the one check that explains another being wrong:
        # a setting that stopped being read looks exactly like one nobody set.
        checks += _retired()
        checks += _catalogue(cfg)
        checks += _packs(cfg)
        checks += _at_rest(cfg)
        if found is None:
            found = inventory(cfg)
        checks += _catalogues(found)
        # After the counts, because it explains one: a zero that is ordinary and
        # a zero that means the path is wrong print the same number.
        checks += _where(cfg, found)
        checks += _definitions(cfg, found)
        checks += _shell(cfg)
    except ConfigError as exc:  # pragma: no cover -- belt and braces
        checks.append(Check("configuration", "fail", str(exc)))
    return tuple(checks)


def worst(checks: tuple[Check, ...]) -> Verdict:
    """The exit code, decided in one place."""
    if any(check.verdict == "fail" for check in checks):
        return "fail"
    return "warn" if any(check.verdict == "warn" for check in checks) else "ok"
