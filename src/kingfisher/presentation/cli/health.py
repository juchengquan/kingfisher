"""Every check that stands between an install and a run.

`doctor` answers one question -- *why will this not start?* -- and the value is
that the answers already existed and were scattered: a `ConfigError` here, a
warning inside `model_catalogue.load` there, `warn_if_unconfined` in a driver
that is not in the wheel at all. Somebody diagnosing a deployment had to
provoke each one in turn.

Nothing here calls a model. A credential that is present can still be wrong, and
the only way to know is to spend money on a call nobody asked for -- so a check
says what it can see and is honest that reachable is not the same as working.
That line is what keeps this cheap enough to run before every deployment rather
than after the first failure.

Checks, not prose: each returns a verdict and the driver prints it. A library
that writes to stdout cannot be used by a server, and `--json` needs the same
answers in a different shape.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

from kingfisher import (
    DEFINITION_KINDS,
    Config,
    ConfigError,
    Inventory,
    inventory,
    kinds_at,
    shell_confinement,
)

#: `fail` means this deployment will not run. `warn` means it will, and
#: something about it is worth knowing -- an unconfined shell runs fine.
Verdict = Literal["ok", "warn", "fail"]


@dataclass(frozen=True)
class Check:
    """One question, its answer, and what to do about it.

    `remedy` is empty when there is nothing to do. It is a separate field rather
    than part of `detail` because a caller reading JSON wants the diagnosis and
    the instruction apart, and because an instruction that is optional reads
    badly when it is glued on.
    """

    name: str
    verdict: Verdict
    detail: str
    remedy: str = ""


def _catalogue(cfg: Config) -> Iterator[Check]:
    """The model catalogue: that it loaded, and that its names can be reached.

    `from_env` has already refused a missing or unreadable one before we get
    here, so what is left is the half that loads and still cannot run: an
    endpoint whose credential is absent is dropped with a warning, and an alias
    a definition names but nothing binds refuses at build time, one request in.
    """
    models = cfg.models
    yield Check(
        "catalogue",
        "ok",
        f"{len(models.models)} model(s) on {len(models.endpoints)} endpoint(s), "
        f"default {models.default!r} -- credentials present, not tested",
    )

    # A dropped endpoint is a warning, not a failure. A shared catalogue naming
    # endpoints this machine cannot reach is the normal case by the loader's own
    # account -- "one reviewed file works across a fleet holding different
    # subsets of keys" -- so failing here would fail on the arrangement the
    # format encourages. It becomes a failure through the definitions check,
    # when something actually names one.
    #
    # Reported at all because it was not: the drop is announced by a warning at
    # load and then discarded, so this printed a tick over a lost endpoint.
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
    """Whether there is anywhere to seed definitions from.

    This asked whether they had arrived inside the install, which was only ever
    wrong if an install was damaged -- a check that could realistically only
    pass. Nothing ships them now, so the question is about a configured
    directory, and a configured directory has four ordinary ways to be wrong:
    unset, mistyped, deleted, or named one level too high. Before, there was one
    exotic way.

    `warn` and never `fail`, in all four. A deployment that seeded its workspace
    six months ago runs perfectly well with nothing set here, and `doctor` exits
    non-zero on any failure -- so failing would turn a working install red for a
    setting it does not need. That is the trap `worst` already names, using the
    unconfined shell as its example.

    Four separate details rather than one, because the remedies differ and a
    diagnosis that cannot be acted on is a line people scroll past. "Holds none
    of them" is the one worth spelling out: pointing one level off is the
    easiest mistake to make with a path, and naming what was looked for is what
    turns it from a puzzle into a fix.
    """
    if cfg.assets is None:
        yield Check(
            "definitions to seed",
            "warn",
            "KINGFISHER_ASSETS is not set",
            "set it to a directory of definitions, or pass `kingfisher seed --from DIR`",
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
        detail += ", and KINGFISHER_SKILLS is off so none will be offered"
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


def _definitions(cfg: Config, found: Inventory) -> Iterator[Check]:
    """Which definitions this deployment cannot actually run.

    The check the dropped-endpoint bug produces, and the one nothing else does:
    a delegate binding an alias to a model on an endpoint with no key leaves a
    workspace that loads, lists cleanly, and fails on the first request naming
    it. The build refuses it then, with a message worth reading -- but then is
    after somebody waited, and `doctor` exists to be the before.

    A failure rather than a warning, unlike a merely unreachable endpoint: a
    catalogue naming endpoints this machine cannot use is ordinary, and a
    definition that cannot run is a workspace promising something it will not
    deliver.

    Imported inside the function. `unrunnable_delegates` reaches deepagents as
    it loads -- 868ms and 3,137 modules, measured -- and at module scope every
    other verb would pay it, so `kingfisher help` would cost a second to print
    text. The CLI starts in 40ms and should keep doing so.
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

    from kingfisher import unrunnable_delegates  # noqa: PLC0415

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


def _shell(cfg: Config) -> Iterator[Check]:
    """What is keeping `execute` off the host, if anything.

    The check most worth having and the one that was hardest to reach: it lived
    in `main.py`, which an installed kingfisher does not have. An unconfined
    shell is a warning rather than a failure because plenty of deployments mean
    it -- but silence would make an unconfined one look exactly like a confined
    one, which is how this went unnoticed until it was measured.
    """
    confined = shell_confinement(cfg)
    if confined.confined:
        yield Check("shell", "ok", "confined")
    else:
        yield Check(
            "shell",
            "warn",
            confined.warning or "nothing is confining `execute` to the workspace",
            "set KINGFISHER_SHELL_SANDBOX, or confine the process itself",
        )


def examine(cfg: Config) -> tuple[Check, ...]:
    """Every check, in the order somebody diagnosing would want them.

    Configuration first, because nothing else matters if that is wrong; then
    what supplies definitions; then the definitions; then the boundary around
    the shell. A `ConfigError` from any of it is caught and becomes a failed
    check rather than an exception, because a diagnosis that stops at the first
    problem is the thing this command exists to replace.
    """
    checks: list[Check] = []
    try:
        checks += _catalogue(cfg)
        checks += _packs(cfg)
        found = inventory(cfg)
        checks += _catalogues(found)
        checks += _definitions(cfg, found)
        checks += _shell(cfg)
    except ConfigError as exc:  # pragma: no cover -- belt and braces
        checks.append(Check("configuration", "fail", str(exc)))
    return tuple(checks)


def worst(checks: tuple[Check, ...]) -> Verdict:
    """The exit code, decided in one place.

    A warning is not a failure. `doctor` exiting non-zero because a shell is
    unconfined would make it useless in the deployments that chose that, and a
    check nobody can run is a check nobody heeds.
    """
    if any(check.verdict == "fail" for check in checks):
        return "fail"
    return "warn" if any(check.verdict == "warn" for check in checks) else "ok"
