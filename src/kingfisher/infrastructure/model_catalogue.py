"""Reading `models.yaml`: which endpoints exist, and which models run on them."""

from __future__ import annotations

import warnings
from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

import yaml

from kingfisher.config import (
    ConfigError,
    Endpoint,
    MissingCredentialsWarning,
    ModelProfile,
    Models,
    WireFormat,
)
from kingfisher.core.agent.models import WIRE_FORMATS
from kingfisher.core.definitions import fields
from kingfisher.infrastructure.wiring import store_named
from kingfisher.infrastructure.workspace import EXAMPLE

if TYPE_CHECKING:
    from pathlib import Path

#: Top-level keys. `default` names a model; the other two are the tables.
KNOWN_TOP: frozenset[str] = frozenset({"endpoints", "models", "default"})

#: What an endpoint entry may say. `api` picks a wire format from `wire_formats`
#: below; `base_url` is literal because it is topology, not a
#: secret, and because two gateways speaking one wire format cannot both be
#: described by a single conventional variable name -- which is the whole reason
#: this table exists apart from the wire-format table.
KNOWN_ENDPOINT: frozenset[str] = frozenset({"api", "base_url", "key", "key_env"})

#: What names the file an endpoint's `key:` is an entry in. No default: everything but
#: the workspace defaults inside it or is off, and a keys file inside the workspace is
#: one the macOS fence lets the agent's shell read.
KEYS_SETTING = "KINGFISHER_MODEL_KEYS_FILE"

#: What a model entry may say, beside `extra`. Every one is optional except
#: `endpoint`; see `ModelProfile` for why omitted means "not passed at all"
#: rather than "passed as a default we chose".
KNOWN_MODEL: frozenset[str] = frozenset(
    {"endpoint", "max_tokens", "timeout_s", "temperature", "top_p", "clear_results_at", "extra"}
)


#: Keys this format used to define, named individually rather than folded into "unknown
#: key" for the reason `NOT_COMPILED` gives one layer out: the generic message reads as
#: a typo and sends its reader looking for the right spelling, when what they need is to
#: know the key is gone and what replaces it.
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
    """Refuse every key this format does not define, and guess at the typos."""
    for gone, reason in REMOVED.items():
        if gone in document:
            msg = f"{where}: {gone!r} {reason}"
            raise ConfigError(msg)
    complaint = fields.unrecognised(document, known=known, noun="key")
    if complaint is not None:
        msg = f"{where}: {complaint}"
        raise ConfigError(msg)


SETTING = "KINGFISHER_WIRE_FORMATS"


def wire_formats(spec: str | None) -> Mapping[str, WireFormat]:
    """The wire formats this deployment can build: kingfisher's, and its own."""
    if spec is None:
        return WIRE_FORMATS
    theirs = store_named(spec, setting=SETTING, port=Mapping)
    for name, row in theirs.items():
        if not isinstance(row, WireFormat):
            msg = (
                f"{SETTING} names {spec!r}, whose {name!r} is a "
                f"{type(row).__name__} -- every value has to be a kingfisher WireFormat"
            )
            raise ConfigError(msg)
    # Refused rather than letting the deployment's row win: every endpoint already
    # written as `api: anthropic` would move to a class its author never chose, and
    # nothing in `models.yaml` would show it.
    taken = sorted(set(theirs) & set(WIRE_FORMATS))
    if taken:
        msg = (
            f"{SETTING} names {spec!r}, which redefines {', '.join(map(repr, taken))} -- "
            "a wire format kingfisher ships. Give yours a name of its own and point "
            "the endpoints that want it there"
        )
        raise ConfigError(msg)
    return MappingProxyType({**WIRE_FORMATS, **theirs})


def _mapping(value: Any, where: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        msg = f"{where}: expected a mapping, got {type(value).__name__}"
        raise ConfigError(msg)
    return value


def _without_the_line(exc: yaml.YAMLError) -> str:
    """Why the parse failed, with the place instead of the text.

    `str()` on one of PyYAML's marked errors reproduces the source line it stopped on,
    with a caret under the column. For a keys file that line *is* a key, and a refusal
    is printed -- so the one message saying the file is wrong undid the whole reason the
    key was kept in a file of its own. A line and a column are what a person needs in
    order to go and look, and they carry nothing.

    Both parse sites use it. `models.yaml` refuses an `api_key:` field, but only once it
    parses: malformed, it quoted whatever line it stopped on exactly as this one did.
    """
    problem = getattr(exc, "problem", None)
    if not isinstance(problem, str):
        # Not every `YAMLError` is a marked one, and the others put their detail in
        # `str()` too. Nothing is said rather than guessing which parts of it are safe.
        return "it could not be parsed"
    mark = getattr(exc, "problem_mark", None)
    if mark is None:
        return problem
    return f"{problem} (line {mark.line + 1}, column {mark.column + 1})"


def _keys(path: Path | None) -> Mapping[str, str] | None:
    """The keys file's entries, or `None` where no keys file is named.

    Refused rather than read around when it is named and wrong. A path somebody wrote
    is a mistake to say at once, where an endpoint missing its key is one machine's
    ordinary state -- the line a missing `models.yaml` already draws.
    """
    if path is None:
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        msg = f"{KEYS_SETTING} names {path}, which does not exist"
        raise ConfigError(msg) from exc
    except OSError as exc:
        msg = f"{KEYS_SETTING} names {path}, which cannot be read: {exc}"
        raise ConfigError(msg) from exc
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        msg = f"{path}: not valid YAML: {_without_the_line(exc)}"
        raise ConfigError(msg) from exc
    if parsed is None:
        return {}
    if not isinstance(parsed, Mapping):
        msg = f"{path}: not a mapping of entry names to keys"
        raise ConfigError(msg)
    # A key YAML reads as a number would be sent as one, or quietly stringified into
    # something that was never the key.
    if wrong := sorted(str(name) for name, key in parsed.items() if not isinstance(key, str)):
        named = ", ".join(repr(name) for name in wrong)
        msg = f"{path}: entry {named} is not text; quote a key YAML would read as a number"
        raise ConfigError(msg)
    return {str(name): key for name, key in parsed.items()}


def _endpoints(  # noqa: PLR0913 -- one argument per source a key can come from
    document: Mapping[str, Any],
    environ: Mapping[str, str],
    source: Path,
    buildable: Mapping[str, WireFormat],
    *,
    keys: Mapping[str, str] | None,
    keys_file: Path | None,
) -> tuple[dict[str, Endpoint], dict[str, str]]:
    """Every endpoint whose key is actually present, and the names dropped."""
    resolved: dict[str, Endpoint] = {}
    dropped: dict[str, str] = {}
    for name, raw in _mapping(document.get("endpoints"), f"{source}: endpoints").items():
        entry = _mapping(raw, f"{source}: endpoint {name!r}")
        _refuse_unknown(entry, KNOWN_ENDPOINT, f"{source}: endpoint {name!r}")
        for required in ("api", "base_url"):
            if not str(entry.get(required) or "").strip():
                msg = f"{source}: endpoint {name!r} is missing required key {required!r}"
                raise ConfigError(msg)
        named_entry = str(entry.get("key") or "").strip()
        named_variable = str(entry.get("key_env") or "").strip()
        if named_entry and named_variable:
            msg = (
                f"{source}: endpoint {name!r} names both 'key' and 'key_env'; its key "
                f"comes from one or the other"
            )
            raise ConfigError(msg)
        if not (named_entry or named_variable):
            msg = (
                f"{source}: endpoint {name!r} names neither 'key' nor 'key_env', so "
                f"nothing says where its key is"
            )
            raise ConfigError(msg)
        api = str(entry["api"]).strip()
        if api not in buildable:
            # Refused here rather than in `build_model`, which is where it used to
            # happen -- meaning a wire format kingfisher cannot speak loaded without
            # complaint and failed when a turn started, from inside a request. Every
            # other closed set in this file is checked as it is read: an unknown key is
            # refused, a model absent from the table is refused, a default naming
            # neither is refused. `api` was the one that was not.
            msg = (
                f"{source}: endpoint {name!r} names api {api!r}, which kingfisher "
                f"cannot build; known: {tuple(sorted(buildable))}"
            )
            raise ConfigError(msg)
        if named_variable:
            key = (environ.get(named_variable) or "").strip()
            missing = named_variable
        elif keys is None:
            key, missing = "", KEYS_SETTING
        else:
            key = (keys.get(named_entry) or "").strip()
            missing = f"entry {named_entry!r} in {keys_file}"
        if not key:
            # Named, not hinted at. "endpoint 'minimax' has no credentials"
            # sends someone to the YAML, where everything looks correct.
            #
            # Keyed by endpoint rather than pre-formatted, because two callers
            # need it now and they word it differently: the warning below lists
            # them, and `resolve` names one inside a sentence about a model.
            dropped[name] = missing
            continue
        resolved[name] = Endpoint(
            api=api,
            base_url=str(entry["base_url"]),
            api_key=key,
            key_env=named_variable,
            key_entry=named_entry,
            wire_format=buildable[api],
        )
    return resolved, dropped


def _mark(written: Any, subject: str) -> int | None:
    """A `clear_results_at` as tokens, refusing what cannot be a count of them.

    Whether it clears before the summariser does is a question about the built model,
    whose window is its own, so `build_model` asks it.
    """
    if written is None:
        return None
    if isinstance(written, bool) or not isinstance(written, int) or written < 1:
        msg = f"{subject}: clear_results_at is {written!r}, and must be a number of tokens"
        raise ConfigError(msg)
    return written


def _models(
    document: Mapping[str, Any],
    endpoints: Mapping[str, Endpoint],
    dropped: Mapping[str, str],
    source: Path,
) -> tuple[dict[str, ModelProfile], dict[str, str]]:
    """Every model whose endpoint survived, and why each of the others did not."""
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
            # The rule `WireFormat.extra` already carries: additive only. A row
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
            clear_results_at=_mark(entry.get("clear_results_at"), f"{source}: model {name!r}"),
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


def load(
    path: Path,
    environ: Mapping[str, str],
    buildable: Mapping[str, WireFormat] = WIRE_FORMATS,
    *,
    keys_file: Path | None = None,
) -> Models:
    """Read `path` into what this deployment can run, where, and under which names.

    `keys_file` is what `KEYS_SETTING` named, read before the catalogue so a keys file
    named wrong refuses even where no endpoint has yet asked it for anything.
    """
    keys = _keys(keys_file)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        msg = (
            f"no model catalogue at {path}. kingfisher needs one to know where to send "
            f"prompts, and reads it from KINGFISHER_DEFINITIONS_DIR; write that file "
            f"there. A minimal one:\n\n"
            f"    endpoints:\n"
            f"      minimax:\n"
            f"        api: anthropic\n"
            f"        base_url: https://api.minimaxi.com/anthropic\n"
            f"        key_env: MINIMAX_API_KEY\n\n"
            f"    default: MiniMax-M3\n\n"
            f"    models:\n"
            f"      MiniMax-M3:\n"
            f"        endpoint: minimax\n\n"
            # The minimal one above is enough to start; the annotated example is the one
            # that explains `extra` and why an omitted `temperature` is not a defaulted
            # one. It ships with the framework rather than with an asset pack, so this
            # can promise it even to a deployment that installed no pack.
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
        msg = f"{path}: not valid YAML: {_without_the_line(exc)}"
        raise ConfigError(msg) from exc

    document = _mapping(parsed, str(path))
    _refuse_unknown(document, KNOWN_TOP, str(path))

    endpoints, dropped = _endpoints(
        document, environ, path, buildable, keys=keys, keys_file=keys_file
    )
    if dropped:
        # Warned even when nothing names them. A shared catalogue listing an
        # endpoint this machine cannot reach is the normal case, but silence
        # would make a *typo* in `key_env` look identical to it.
        named = ", ".join(f"{name} ({key} is not set)" for name, key in sorted(dropped.items()))
        warnings.warn(
            f"{path}: no credentials for endpoint(s) {named}; "
            f"models on them are unavailable here",
            MissingCredentialsWarning,
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
        dropped=dropped,
        source=path,
        keys_file=keys_file,
    )
