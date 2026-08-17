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
    Config,
    ConfigError,
    Inventory,
    inventory,
    shell_confinement,
    shipped_kinds,
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
        f"default {models.default!r}",
    )

    # Named with what each points at, not just which alias is wrong. The reader
    # has to edit one of the two, and `cheap` alone does not say which model was
    # meant -- `cheap -> gpt-5` does, and is usually enough to spot the typo.
    unbound = sorted(
        f"{name} -> {target}"
        for name, target in models.aliases.items()
        if target not in models.models
    )
    if unbound:
        yield Check(
            "aliases",
            "fail",
            f"{', '.join(unbound)}: named in `aliases:`, not defined under `models:`",
            "bind each alias to a model this catalogue defines",
        )
    elif models.aliases:
        bound = ", ".join(sorted(models.aliases))
        yield Check("aliases", "ok", f"{len(models.aliases)} bound: {bound}")
    else:
        # Not a failure. A deployment that names no alias has nothing to bind,
        # and saying so beats an empty line somebody reads as a problem.
        yield Check("aliases", "ok", "none defined")


def _packs() -> Iterator[Check]:
    """Whether there are definitions to seed from.

    They ship with kingfisher, so this is only ever wrong if an install is
    damaged -- but that is exactly the case worth naming, since an empty
    workspace and a broken install look identical from the outside.

    It reported which asset *packs* were installed until the definitions stopped
    being a separate distribution. There is nothing to enumerate now: one
    directory either came with the install or did not.
    """
    kinds = shipped_kinds()
    if kinds:
        yield Check("definitions to seed", "ok", ", ".join(kinds))
    else:
        yield Check(
            "definitions to seed",
            "warn",
            "the definitions that ship with kingfisher are missing",
            "reinstall kingfisher, or seed from your own directory",
        )


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
    hidden = found.skills_unloadable + found.skills_misplaced
    if hidden:
        yield Check(
            "skills",
            "warn",
            f"{detail}; {len(hidden)} present and invisible to the agent: {', '.join(hidden)}",
            "run `kingfisher list` for why each one is not loadable",
        )
    else:
        yield Check("skills", "ok", detail)


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
        checks += _packs()
        checks += _catalogues(inventory(cfg))
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
