"""Every check that stands between an install and a run."""

from __future__ import annotations

import os
import ssl
import stat
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from kingfisher import Config, ConfigError, Inventory, inventory, kinds_at
from kingfisher.core.backing import MemoryBacking, memory_backing

# Past the front door on purpose: a probe only `doctor` wants is no public promise, and
# the command ships in this wheel, so it takes each name where it lives.
from kingfisher.core.definitions.catalogue import DEFINITION_KINDS
from kingfisher.core.origins import kingfishers_own
from kingfisher.plugins.sessions.sandbox import confinement
from kingfisher.plugins.sessions.sandbox.confinement import (
    LINUX_FENCES,
    SANDBOX_EXEC,
    Confinement,
)

from . import health_definitions, health_isolation
from .check import Check, Verdict

#: Settings nothing reads any more, and what to write instead. A renamed variable fails
#: in silence, falling back to its default, and this is the only place that names one to
#: a deployment still carrying it.
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
    "KINGFISHER_SOURCE_IDS_FILE": "nothing -- the source ids and datazones in use are "
                                  "collected from the definitions' locks",
}

#: Prefixes that went whole, matched rather than enumerated: a list of suffixes would
#: miss whichever one nobody remembered setting.
RETIRED_PREFIXES: dict[str, str] = {
    "KINGFISHER_SERVER_": "nothing -- the HTTP service was removed",
    "KINGFISHER_SERVICE_": "nothing -- the HTTP service was removed",
}


def _models(cfg: Config) -> Iterator[Check]:
    """The model catalogue: that it loaded, and that its names can be reached."""
    models = cfg.models
    yield Check(
        "catalogue",
        "ok",
        f"{len(models.models)} model(s) on {len(models.endpoints)} endpoint(s), "
        f"default {models.default!r} -- credentials present, not tested",
    )

    # A dropped endpoint is a warning: a shared catalogue naming endpoints this machine
    # has no key for is the normal case, and it fails in the definitions check once
    # something names one. Listed by endpoint, not from `unreachable`, which leaves out an
    # endpoint no model uses and would call it keyed.
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


def _certificates() -> Iterator[Check]:
    """Whether this Python verifies HTTPS by itself, as a tool using its defaults needs."""
    loaded = ssl.create_default_context().cert_store_stats()["x509_ca"]
    if loaded:
        yield Check("certificates", "ok", f"{loaded} in this Python's own store")
        return
    # A folder of hashed certificates is read lazily, so none count as loaded yet.
    folder = ssl.get_default_verify_paths().capath
    if folder and Path(folder).is_dir() and any(Path(folder).iterdir()):
        yield Check("certificates", "ok", f"read from {folder} as needed")
        return
    yield Check(
        "certificates",
        "warn",
        "this Python has no CA certificates of its own, so a tool fetching HTTPS with "
        "its defaults fails on every address; model calls carry certifi's and are "
        "unaffected",
        "on python.org's macOS build run its Install Certificates.command, or point "
        "SSL_CERT_FILE at a bundle",
    )


def _key_source(endpoint: Any) -> str:
    """Where one endpoint's key came from, in the words `models.yaml` used for it."""
    if endpoint.key_entry:
        return f'keys file, entry "{endpoint.key_entry}"'
    return endpoint.key_env or "set in code"


def _keys_file(cfg: Config, sessions: object, confined: Confinement) -> Iterator[Check]:
    """Whether the agent's shell can read the keys file, by the fence that is running.

    A report, never a refusal: nothing keeps the shell from the keys but where the file sits.
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
    mechanism = confined.mechanism
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
    cfg: Config, from_dotenv: frozenset[str] | None, sessions: object, confined: Confinement
) -> Iterator[Check]:
    """Keys that were in the environment before kingfisher started, where the shell reads it.

    On macOS a sandboxed shell reads any same-user process's starting environment, and with
    no fence nothing stops it; what `.env` added later is not in it. Silent, rather than
    possibly wrong, where `from_dotenv` is `None` or the shell cannot be seen.
    """
    if from_dotenv is None or health_isolation.shell_of(sessions) is None:
        return
    mechanism = confined.mechanism
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


def _sessions_probe(cfg: Config) -> Path:
    """Where to measure a deployment's sessions, before it has any.

    A missing directory reports no size, and `doctor` before `seed` is when a tmpfs gets
    sized; with nothing mounted, sessions land on the workspace's filesystem anyway.
    """
    return cfg.sessions_root if cfg.sessions_root.is_dir() else cfg.workspace


def _devices(sessions: MemoryBacking, workspace: MemoryBacking) -> str:
    """Both filesystems, named even where they agree, so nobody infers which the numbers mean."""
    return f"sessions on {sessions.filesystem}, workspace on {workspace.filesystem}"


def _at_rest(cfg: Config, sessions: object) -> Iterator[Check]:
    """Whether sessions kept in memory can actually keep nothing.

    Asked of the sessions tree, not the workspace: once `<workspace>/sessions` is a mount,
    the workspace's device is not the one sessions are written to. Measured: a memory
    filesystem larger than the memory limit swaps silently when it fills, and with swap
    off is an OOM kill of every session; only one smaller gives a clean `ENOSPC`.
    """
    backing = memory_backing(_sessions_probe(cfg))
    workspace = memory_backing(cfg.workspace)
    devices = _devices(backing, workspace)
    if not backing.in_memory:
        # Sessions on disk under a tmpfs workspace: every check below gates on
        # sessions, so without this that arrangement would say nothing at all.
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

    A warning, not a failure: the deployment runs, on defaults that may be what it wanted.
    """
    values = os.environ if environ is None else environ
    stale = {
        name: instead
        for name, instead in RETIRED.items()
        if (values.get(name) or "").strip()
    }
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
        # Here, not at the top: importing it loads deepagents.
        from .configured import configured_sessions  # noqa: PLC0415

        sessions = configured_sessions()
    checks: list[Check] = []
    # First: a setting that stopped being read looks exactly like one nobody set.
    checks += _retired()
    checks += _models(cfg)
    checks += _certificates()
    confined = health_isolation.fence_of(cfg, sessions)
    checks += _keys_file(cfg, sessions, confined)
    checks += _keys_file_mode(cfg)
    checks += _starting_environment(cfg, from_dotenv, sessions, confined)
    checks += _packs(cfg)
    checks += _at_rest(cfg, sessions)
    try:
        if found is None:
            found = inventory(cfg, sessions=sessions)
        checks += health_definitions.checks(cfg, found)
    except ConfigError as exc:
        checks.append(
            Check(
                "definitions",
                "fail",
                str(exc),
                "fix it; until then the rows on the definitions are not checked",
            )
        )
    checks += health_isolation.checks(cfg, sessions, confined)
    return tuple(checks)


def worst(checks: tuple[Check, ...]) -> Verdict:
    """The exit code, decided in one place."""
    if any(check.verdict == "fail" for check in checks):
        return "fail"
    return "warn" if any(check.verdict == "warn" for check in checks) else "ok"
