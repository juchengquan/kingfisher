"""Every check that stands between an install and a run."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from kingfisher import Config, ConfigError, Inventory, inventory, kinds_at
from kingfisher.cli import health_definitions, health_isolation
from kingfisher.cli.check import Check, Verdict
from kingfisher.core.backing import MemoryBacking, memory_backing

# Everything below is past the front door on purpose, and this is the file to read if
# you want to know what that door is now for. These are `doctor`'s probes: what fences
# this host offers, what the kernel supports, what the cgroup files say, and the
# sentence that tells a reader where their own definitions go. Every one of them was
# public because *this file* reached for it -- the export table said so in as many words
# -- and a probe only `doctor` has ever wanted is not a promise worth making to
# everybody who runs `pip install kingfisher`. The command ships in this wheel, so it
# takes each name where it lives.
from kingfisher.core.definitions.catalogue import DEFINITION_KINDS
from kingfisher.core.origins import kingfishers_own
from kingfisher.core.session.model import sessions_root
from kingfisher.plugins.sessions.sandbox import confinement
from kingfisher.plugins.sessions.sandbox.confinement import (
    LINUX_FENCES,
    SANDBOX_EXEC,
    shell_confinement,
)

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
    "KINGFISHER_STATE_DIR": "nothing -- claims and pinned agents are inside the "
                            "session they belong to, and run events go to the "
                            "`kingfisher.run` logger",
    "KINGFISHER_SCRATCH_DIR": "nothing -- TMPDIR is a directory inside the session",
    "KINGFISHER_SESSION_KEY": "nothing -- what a session keeps is no longer signed, and "
                              "the `session files` check says what keeps the shell out",
}

#: Prefixes that went whole, matched rather than enumerated: every setting the
#: HTTP service read, under both names it had. No suffix is listed, because a
#: deployment still carrying one is the case this exists for, and a list would
#: miss whichever one nobody remembered setting.
RETIRED_PREFIXES: dict[str, str] = {
    "KINGFISHER_SERVER_": "nothing -- the HTTP service was removed",
    "KINGFISHER_SERVICE_": "nothing -- the HTTP service was removed",
}


def _models(cfg: Config) -> Iterator[Check]:
    """The model catalogue: that it loaded, and that its names can be reached.

    Named for what it checks rather than for the word `models.yaml` shares with the
    definition directories: `_catalogues` two hundred lines below is about those, and
    one letter between two names meaning different things is a reader's problem
    rather than a writer's.
    """
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
    #
    # By endpoint rather than by model, because `doctor` silences the loader's warning
    # and this is what says it instead: an endpoint no model uses has no entry in
    # `unreachable`, and a report built from that alone would call it keyed.
    keyed = "; ".join(
        f"{name}: {_key_source(endpoint)}" for name, endpoint in sorted(models.endpoints.items())
    )
    if models.dropped:
        named = ", ".join(
            f"{name} ({key} is not set)" for name, key in sorted(models.dropped.items())
        )
        riding = (
            f"{len(models.unreachable)} model(s) this file defines cannot be reached: "
            f"{', '.join(sorted(models.unreachable))}"
            if models.unreachable
            else "no model this file defines runs on them"
        )
        yield Check(
            "credentials",
            "warn",
            f"no key for endpoint(s) {named}; {riding}; keyed: {keyed}",
            "set what each one names -- a variable, the keys file setting, or its entry -- "
            "or ignore this if no definition wants them",
        )
    else:
        yield Check("credentials", "ok", keyed)


def _key_source(endpoint: Any) -> str:
    """Where one endpoint's key came from, in the words `models.yaml` used for it."""
    if endpoint.key_entry:
        return f'keys file, entry "{endpoint.key_entry}"'
    return endpoint.key_env or "set in code"


def _keys_file(cfg: Config, sessions: object) -> Iterator[Check]:
    """Whether the agent's shell can read the keys file, by the fence that is running.

    A report and never a refusal: kingfisher keeps nothing from the shell for the
    keys, so where the file sits decides it, and this says what that is.
    """
    path = cfg.models.keys_file
    if path is None:
        return
    if (shell := health_isolation.shell_of(sessions)) is None:
        yield Check(
            "keys file",
            "warn",
            f"the sessions in use are {health_isolation.named(sessions)}, and "
            f"nothing here can see whether their shell can read {path}",
            "a shell on another machine cannot; one on this machine reads what its own "
            "fence allows",
        )
        return
    mechanism = shell_confinement(cfg, shell).mechanism
    reason = confinement.shell_reads(
        path,
        mechanism=mechanism,
        home=Path.home(),
        readable=confinement.shell_readable(cfg, shell, mechanism),
    )
    if reason is None:
        yield Check("keys file", "ok", f"{path}, which the sandbox keeps from the agent's shell")
        return
    if mechanism == SANDBOX_EXEC:
        remedy = "keep it under your home folder and out of the workspace, as in "
        remedy += "~/.config/kingfisher/keys.yaml"
    elif mechanism in LINUX_FENCES:
        remedy = "keep it out of what the fence grants: the system folders, the interpreter's "
        remedy += "and the skills"
    else:
        remedy = "turn the sandbox on, or keep the keys where this shell cannot reach"
    yield Check(
        "keys file", "warn", f"{path}: {reason}, so the agent's shell can read the keys in it",
        remedy,
    )


def _keys_file_mode(cfg: Config) -> Iterator[Check]:
    """Whether other users on this machine can read the keys file -- not the agent."""
    path = cfg.models.keys_file
    if path is None or not path.is_file():
        return
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        yield Check(
            "keys file mode",
            "warn",
            f"{path} is mode {mode:o}, so other users on this machine can read it",
            f"chmod 600 {path}",
        )


def _starting_environment(
    cfg: Config, from_dotenv: frozenset[str] | None, sessions: object
) -> Iterator[Check]:
    """Keys that were in the environment before kingfisher started, where the shell reads it.

    On macOS a sandboxed shell reads any same-user process's environment as it started,
    and with no fence nothing stops it; what `.env` added afterwards is not in there.
    `None` is a caller that cannot say what `.env` added, which is told nothing rather
    than told something that may be wrong -- and so are sessions whose shell
    this cannot see.
    """
    shell = health_isolation.shell_of(sessions)
    if from_dotenv is None or shell is None:
        return
    mechanism = shell_confinement(cfg, shell).mechanism
    if mechanism == SANDBOX_EXEC:
        where = "on this Mac"
    elif mechanism in LINUX_FENCES:
        return
    else:
        where = "with no sandbox"
    exported = sorted(
        {e.key_env for e in cfg.models.endpoints.values() if e.key_env} - from_dotenv
    )
    if exported:
        verb = "was" if len(exported) == 1 else "were"
        yield Check(
            "starting environment",
            "warn",
            f"{', '.join(exported)} {verb} set before kingfisher started, and {where} the "
            f"agent's shell can read a process's starting environment",
            "put the key in the keys file KINGFISHER_MODEL_KEYS_FILE names, or in .env, "
            "rather than exporting it",
        )


def _packs(cfg: Config) -> Iterator[Check]:
    """Whether there is anywhere to seed definitions from."""
    if cfg.assets is None:
        yield Check(
            "definitions to seed",
            "warn",
            "KINGFISHER_ASSETS is not set",
            "set it to a directory of definitions, or pass"
            " `kingfisher seed --from DIR`",
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


def _at_rest(cfg: Config, sessions: object) -> Iterator[Check]:
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
    backing = memory_backing(_sessions_probe(cfg.workspace))
    workspace = memory_backing(cfg.workspace)
    devices = _devices(backing, workspace)
    if not backing.in_memory:
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

    if backing.swap_enabled:
        yield Check(
            "nothing at rest",
            "fail",
            f"{devices} — this cgroup permits swapping",
            "disable swap for the container (`--memory-swap` equal to `--memory`)",
        )
    if backing.fits is False:
        yield Check(
            "nothing at rest",
            "fail",
            f"{devices} — {_mb(backing.size_bytes)} of sessions against a memory limit of "
            f"{_mb(backing.limit_bytes)}; filling it swaps to disk, or kills the container",
            "size the filesystem below the limit, leaving room for this process",
        )
    elif backing.fits is None:
        yield Check(
            "nothing at rest",
            "warn",
            f"{devices} — and this process has no memory limit",
            "set one, so a full filesystem fails rather than exhausting the host",
        )
    elif not backing.swap_enabled:
        yield Check(
            "nothing at rest",
            "ok",
            f"{devices} — {_mb(backing.size_bytes)} under a "
            f"{_mb(backing.limit_bytes)} limit, no swap",
        )

    # Two things that only matter once sessions are in memory, and both are
    # silent until the moment they are expensive.
    if kingfishers_own(sessions, cfg):
        yield Check(
            "sessions survive",
            "fail",
            f"{devices} — nothing is configured to keep sessions, so everything a "
            "session produced goes with the process",
            "mount durable storage at <workspace>/sessions, or set "
            "KINGFISHER_SESSIONS to sessions that keep them",
        )
    else:
        yield Check(
            "sessions survive", "ok", f"{devices} — kept by {health_isolation.named(sessions)}"
        )

    if cfg.session_max_bytes is None:
        yield Check(
            "session quota",
            "fail",
            f"{devices} — no KINGFISHER_SESSION_MAX_BYTES, and sessions share "
            f"{_mb(backing.size_bytes)} of memory; one can starve every other in "
            "this container",
            "set it below the filesystem size divided by the sessions you expect",
        )
    elif backing.size_bytes is not None and cfg.session_max_bytes > backing.size_bytes:
        yield Check(
            "session quota",
            "warn",
            f"{devices} — one session may reach {_mb(cfg.session_max_bytes)} and the "
            f"filesystem holds {_mb(backing.size_bytes)}, so the quota can never bind",
            "lower it, or the limit is the filesystem and it arrives as a write failure",
        )
    else:
        yield Check(
            "session quota", "ok", f"{devices} — {_mb(cfg.session_max_bytes)} per session"
        )


def _mb(value: int | None) -> str:
    """Bytes as megabytes, because these numbers are read by people."""
    return "unknown" if value is None else f"{value // (1024 * 1024)}MB"


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
            name: instead
            for name in values
            for prefix, instead in RETIRED_PREFIXES.items()
            if name.startswith(prefix) and (values.get(name) or "").strip()
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


def examine(
    cfg: Config,
    found: Inventory | None = None,
    *,
    from_dotenv: frozenset[str] | None = None,
    sessions: object | None = None,
) -> tuple[Check, ...]:
    """Every check, in the order somebody diagnosing would want them.

    `from_dotenv` is the names `.env` added, which only the command line knows.
    `sessions` are the ones the command runs on, built from its settings when not given.
    """
    if sessions is None:
        # Here rather than at the top, for the reason `shell_of` gives.
        from kingfisher.cli.configured import configured_sessions  # noqa: PLC0415

        sessions = configured_sessions()
    checks: list[Check] = []
    try:
        # First, because it is the one check that explains another being wrong:
        # a setting that stopped being read looks exactly like one nobody set.
        checks += _retired()
        checks += _models(cfg)
        checks += _keys_file(cfg, sessions)
        checks += _keys_file_mode(cfg)
        checks += _starting_environment(cfg, from_dotenv, sessions)
        checks += _packs(cfg)
        checks += _at_rest(cfg, sessions)
        if found is None:
            found = inventory(cfg, sessions=sessions)
        checks += health_definitions.checks(cfg, found)
        checks += health_isolation.checks(cfg, sessions)
    except ConfigError as exc:  # pragma: no cover -- belt and braces
        checks.append(Check("configuration", "fail", str(exc)))
    return tuple(checks)


def worst(checks: tuple[Check, ...]) -> Verdict:
    """The exit code, decided in one place."""
    if any(check.verdict == "fail" for check in checks):
        return "fail"
    return "warn" if any(check.verdict == "warn" for check in checks) else "ok"
