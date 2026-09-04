"""Reading `models.yaml`: which endpoints exist, and which models run on them.

`config` owns the records — `Endpoint` and `ModelProfile` belong to no layer, so
they sit at the package root with `Config` itself. `models` owns the closed
adapter table and construction. This owns the step in between: turning one
authored document into those records, and refusing the ways it can be wrong.

Not `definitions.py`, whose charter is stated and narrow -- "reading a
definition document into the value *the domain* works with". A model profile is
not a domain value: `domain/` may not read deployment configuration at all, and
a test enforces it. Widening that module to cover both would make its name a
guess, which its own closing paragraph refuses.

`safe_load`, for a different reason than there. Definitions arrive from a
catalogue service, which makes them input rather than something we wrote; this
file is operator-authored. But it names credential variables and is read at
startup, and `yaml.load` would let a crafted document construct arbitrary
objects before anything else runs.

**A key this format does not define is refused, not ignored.** The same rule
`subagents.reading` states, for the same reason: ignoring a key is
indistinguishable from honouring it, and `max_token:` singular would otherwise
parse, be dropped, and hand back the default with no error anywhere.
"""

from __future__ import annotations

import warnings
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

import yaml

from kingfisher.config import ConfigError, Endpoint, ModelProfile, Models
from kingfisher.domain import fields
from kingfisher.infrastructure.harness.models import ADAPTERS
from kingfisher.infrastructure.workspace.fs import EXAMPLE

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

#: Top-level keys. `default` names a model; the other two are the tables.
KNOWN_TOP: frozenset[str] = frozenset({"endpoints", "models", "default"})

#: What an endpoint entry may say. `api` picks a wire format from
#: `models.ADAPTERS`; `base_url` is literal because it is topology, not a
#: secret, and because two gateways speaking one wire format cannot both be
#: described by a single conventional variable name -- which is the whole reason
#: this table exists apart from the adapter table.
KNOWN_ENDPOINT: frozenset[str] = frozenset({"api", "base_url", "key_env"})

#: What a model entry may say, beside `extra`. Every one is optional except
#: `endpoint`; see `ModelProfile` for why omitted means "not passed at all"
#: rather than "passed as a default we chose".
KNOWN_MODEL: frozenset[str] = frozenset(
    {"endpoint", "max_tokens", "timeout_s", "temperature", "top_p", "extra"}
)


#: Keys this format used to define, named individually rather than folded into
#: "unknown key" for the reason `NOT_COMPILED` gives one layer out: the generic
#: message reads as a typo and sends its reader looking for the right spelling,
#: when what they need is to know the key is gone and what replaces it.
#:
#: This one is the upgrade path. A deployment that bound aliases has a
#: `models.yaml` that stopped loading, and the fix is two lines of editing --
#: but only if the message says so.
REMOVED: Mapping[str, str] = MappingProxyType(
    {
        "aliases": (
            "is no longer a table this format defines. It bound general names -- "
            "`cheap`, `alternate` -- for definitions to write as `alias:`, and that "
            "field is gone too: a definition names a model from `models:` or names "
            "nothing and runs whatever summoned it. Delete this block, and replace "
            "any `alias: <name>` in your definitions with `model: <the model it was "
            "bound to>`"
        ),
    }
)


def _refuse_unknown(document: Mapping[str, Any], known: frozenset[str], where: str) -> None:
    """Refuse every key this format does not define, and guess at the typos.

    The wording is `fields.unrecognised`, shared with the subagent format, which
    had the careful version of this rule while here it was five lines that
    listed the valid keys and nothing else. That was the wrong way round: this
    is the file a deployment writes *first*, and the only one that decides where
    prompts go, so `defualt:` is exactly the mistake worth naming as a typo.

    What stays here is the raising. A malformed catalogue is a `ConfigError`,
    not a `SubagentError`, and `where` is a path plus which entry it was in.
    """
    for gone, reason in REMOVED.items():
        if gone in document:
            msg = f"{where}: {gone!r} {reason}"
            raise ConfigError(msg)
    complaint = fields.unrecognised(document, known=known, noun="key")
    if complaint is not None:
        msg = f"{where}: {complaint}"
        raise ConfigError(msg)


def _mapping(value: Any, where: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        msg = f"{where}: expected a mapping, got {type(value).__name__}"
        raise ConfigError(msg)
    return value


def _endpoints(
    document: Mapping[str, Any], environ: Mapping[str, str], source: Path
) -> tuple[dict[str, Endpoint], dict[str, str]]:
    """Every endpoint whose key is actually present, and the names dropped.

    Dropping rather than refusing is what makes one reviewed file shareable
    across a fleet. The alternative -- every machine must hold every key -- is
    the thing `key_env` was chosen to avoid, and it would make a shared
    catalogue useless the moment it listed an endpoint some machine did not pay
    for.
    """
    resolved: dict[str, Endpoint] = {}
    dropped: dict[str, str] = {}
    for name, raw in _mapping(document.get("endpoints"), f"{source}: endpoints").items():
        entry = _mapping(raw, f"{source}: endpoint {name!r}")
        _refuse_unknown(entry, KNOWN_ENDPOINT, f"{source}: endpoint {name!r}")
        for required in ("api", "base_url", "key_env"):
            if not str(entry.get(required) or "").strip():
                msg = f"{source}: endpoint {name!r} is missing required key {required!r}"
                raise ConfigError(msg)
        api = str(entry["api"]).strip()
        if api not in ADAPTERS:
            # Refused here rather than in `build_model`, which is where it used
            # to happen -- meaning a wire format kingfisher cannot speak loaded
            # without complaint and failed when a turn started, from inside a
            # request. Every other closed set in this file is checked as it is
            # read: an unknown key is refused, a model absent from the table is
            # refused, a default naming neither is refused. `api` was the one
            # that was not.
            #
            # Before the credential check below, deliberately. Checked after it,
            # a typo on an endpoint whose key this machine happens not to hold
            # would be dropped rather than refused -- so the same file would
            # load here and fail on the machine that *does* hold the key, which
            # is precisely the machine-dependence a shared catalogue must not
            # have. A missing key is a fact about a machine; an unbuildable
            # `api` is a fact about the file.
            msg = (
                f"{source}: endpoint {name!r} names api {api!r}, which kingfisher "
                f"cannot build; known: {tuple(sorted(ADAPTERS))}"
            )
            raise ConfigError(msg)
        key = (environ.get(str(entry["key_env"])) or "").strip()
        if not key:
            # Named, not hinted at. "endpoint 'minimax' has no credentials"
            # sends someone to the YAML, where everything looks correct.
            #
            # Keyed by endpoint rather than pre-formatted, because two callers
            # need it now and they word it differently: the warning below lists
            # them, and `resolve` names one inside a sentence about a model.
            dropped[name] = str(entry["key_env"])
            continue
        resolved[name] = Endpoint(
            api=api,
            base_url=str(entry["base_url"]),
            api_key=key,
        )
    return resolved, dropped


def _models(
    document: Mapping[str, Any],
    endpoints: Mapping[str, Endpoint],
    dropped: Mapping[str, str],
    source: Path,
) -> tuple[dict[str, ModelProfile], dict[str, str]]:
    """Every model whose endpoint survived, and why each of the others did not.

    A model whose endpoint was dropped is still dropped from the first mapping:
    it cannot run, and `models` means what can. It is no longer dropped
    *silently*, which was the whole trouble -- once it was gone, nothing could
    tell a name this file never defined from one this machine cannot reach, and
    both `resolve` and `doctor` told people to go and edit correct YAML.

    The second mapping is what makes that answerable, and is deliberately not a
    place to look models up from: it holds the reason, not the profile.
    """
    profiles: dict[str, ModelProfile] = {}
    for name, raw in _mapping(document.get("models"), f"{source}: models").items():
        entry = _mapping(raw, f"{source}: model {name!r}")
        _refuse_unknown(entry, KNOWN_MODEL, f"{source}: model {name!r}")
        endpoint_name = str(entry.get("endpoint") or "").strip()
        if not endpoint_name:
            msg = f"{source}: model {name!r} is missing required key 'endpoint'"
            raise ConfigError(msg)
        extra = _mapping(entry.get("extra"), f"{source}: model {name!r} extra")
        if collision := tuple(sorted(set(extra) & KNOWN_MODEL)):
            # The rule `Adapter.extra` already carries: additive only. A row
            # that could overrule a named param would silently discard a value
            # written three lines above it.
            msg = (
                f"{source}: model {name!r} names {', '.join(collision)} under 'extra', "
                f"which this format already defines; write it directly instead"
            )
            raise ConfigError(msg)
        profiles[name] = ModelProfile(
            model=name,
            endpoint=endpoint_name,
            max_tokens=int(entry.get("max_tokens", 4096)),
            timeout_s=int(entry.get("timeout_s", 120)),
            temperature=entry.get("temperature"),
            top_p=entry.get("top_p"),
            extra=dict(extra),
        )

    # An endpoint that does not exist *at all* is a mistake in the file and is
    # refused; one that exists but was dropped for want of a key is this
    # machine's situation, not the file's, and takes its models quietly.
    declared = set(_mapping(document.get("endpoints"), f"{source}: endpoints"))
    for name, profile in profiles.items():
        if profile.endpoint not in declared:
            msg = (
                f"{source}: model {name!r} names endpoint {profile.endpoint!r}, "
                f"which this file does not define; it defines {tuple(sorted(declared))}"
            )
            raise ConfigError(msg)
    return (
        {n: p for n, p in profiles.items() if p.endpoint in endpoints},
        # Worded to slot into a sentence about a model, because that is where it
        # is read: "model 'gpt-5' runs on endpoint 'openai', whose ...".
        {
            name: f"endpoint {p.endpoint!r}, whose {dropped[p.endpoint]} is not set"
            for name, p in profiles.items()
            if p.endpoint not in endpoints and p.endpoint in dropped
        },
    )


def load(path: Path, environ: Mapping[str, str]) -> Models:
    """Read `path` into what this deployment can run, where, and under which names.

    Required, with no fallback and no shipped default table. `api_style` was
    required and deliberately defaulted to nothing for the same reason: a
    default would silently pick the wrong destination the first time kingfisher
    is pointed somewhere new. A fallback would also make "file absent" look
    exactly like "file found", including when `KINGFISHER_MODELS_FILE` points
    at the wrong path.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        msg = (
            f"no model catalogue at {path}. kingfisher needs one to know where to send "
            f"prompts; set KINGFISHER_MODELS_FILE or write that file. A minimal one:\n\n"
            f"    endpoints:\n"
            f"      minimax:\n"
            f"        api: anthropic\n"
            f"        base_url: https://api.minimaxi.com/anthropic\n"
            f"        key_env: MINIMAX_API_KEY\n\n"
            f"    default: MiniMax-M3\n\n"
            f"    models:\n"
            f"      MiniMax-M3:\n"
            f"        endpoint: minimax\n\n"
            # The minimal one above is enough to start; the annotated example
            # is the one that explains `extra` and why an omitted
            # `temperature` is not a defaulted one. It ships with the framework
            # rather than with an asset pack, so this can promise it even to a
            # deployment that installed no pack.
            #
            # Which of the two sentences depends on whether it is there yet. It
            # said "run this to get one" unconditionally, and that was a dead
            # end: running it hit this same error, because the driver built its
            # config before it seeded. A first run seeds before loading now, so
            # by the time anyone reads this the file is usually already beside
            # them -- and telling someone to run a command that has just run is
            # how a message stops being read.
            #
            # `kingfisher seed` rather than `--seed-assets`: the flag is on a
            # file that is not in the wheel, so it names nothing a pip user has,
            # while the command is on `PATH` for both audiences.
            + (
                f"An annotated {EXAMPLE} is next to it; copy it across.\n"
                if (path.parent / EXAMPLE).is_file()
                else f"`kingfisher seed` writes an annotated {EXAMPLE} next to it.\n"
            )
        )
        raise ConfigError(msg) from exc

    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        msg = f"{path}: not valid YAML: {exc}"
        raise ConfigError(msg) from exc

    document = _mapping(parsed, str(path))
    _refuse_unknown(document, KNOWN_TOP, str(path))

    endpoints, dropped = _endpoints(document, environ, path)
    if dropped:
        # Warned even when nothing names them. A shared catalogue listing an
        # endpoint this machine cannot reach is the normal case, but silence
        # would make a *typo* in `key_env` look identical to it.
        named = ", ".join(f"{name} ({key} is not set)" for name, key in sorted(dropped.items()))
        warnings.warn(
            f"{path}: no credentials for endpoint(s) {named}; "
            f"models on them are unavailable here",
            stacklevel=2,
        )
    models, unreachable = _models(document, endpoints, dropped, path)

    default = str(document.get("default") or "").strip()
    if not default:
        msg = f"{path}: no 'default' model named; one must be, or nothing knows what to run"
        raise ConfigError(msg)
    if default not in models:
        # Separated because the two read differently: a default naming nothing
        # is a broken file, while a default whose endpoint has no key on this
        # machine is a deployment that has not finished being set up.
        known = tuple(sorted(models))
        if default in set(_mapping(document.get("models"), str(path))):
            msg = (
                f"{path}: default model {default!r} runs on an endpoint this machine has "
                f"no credentials for; it can run {known}"
            )
        else:
            msg = f"{path}: default model {default!r} is not defined here; it defines {known}"
        raise ConfigError(msg)
    return Models(
        models=models,
        endpoints=endpoints,
        default=default,
        unreachable=unreachable,
        source=path,
    )
