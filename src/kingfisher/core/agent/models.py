"""Model construction."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from kingfisher.core.access import Selection, refuse_ungranted_endpoint
from kingfisher.core.config import ConfigError, Landing, WireFormat

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from kingfisher.core.config import Config, Endpoint, ModelProfile


#: The wire formats kingfisher ships, picked by an endpoint's `api` along with any a
#: deployment adds through `KINGFISHER_WIRE_FORMATS`. A row names its package as a
#: string, so neither provider is an import and neither belongs in `THIRD_PARTY`.
WIRE_FORMATS: Mapping[str, WireFormat] = {
    # The gateway path: anything imitating Anthropic's wire format, MiniMax included.
    "anthropic": WireFormat(
        "langchain_anthropic:ChatAnthropic",
        Landing(
            model="model",
            base_url="anthropic_api_url",
            api_key="anthropic_api_key",
            max_tokens="max_tokens",
            timeout="default_request_timeout",
            max_retries="max_retries",
        ),
    ),
    # OpenAI proper, on the Responses API: not a general OpenAI-compatible client, since
    # almost no gateway implements `/v1/responses`. Chat Completions would be a new row.
    "openai_responses": WireFormat(
        "langchain_openai:ChatOpenAI",
        Landing(
            model="model_name",
            base_url="openai_api_base",
            api_key="openai_api_key",
            max_tokens="max_tokens",
            timeout="request_timeout",
            max_retries="max_retries",
        ),
        MappingProxyType({"use_responses_api": True}),
    ),
}


#: The share of a model's advertised input window a request may fill, mirroring
#: deepagents' `SummarizationMiddleware._input_budget`, so a row that leaves nothing is
#: refused before a turn starts. A test drives their function with this, so it going
#: stale is red rather than a wrong refusal.
FILLABLE = 0.95


#: Every keyword `build_model` passes the chat class itself, which an `extra` naming one
#: would pass a second time: Python refuses the call, from inside every build.
BUILT_WITH: frozenset[str] = frozenset(
    {"model", "base_url", "api_key", "max_tokens", "timeout", "max_retries", "temperature",
     "top_p"}
)


def spelled_otherwise(wire_format: WireFormat) -> Mapping[str, str]:
    """The chat class's other names for what `build_model` passes it, mapped to the
    keyword each stands for: its field names and aliases for those settings.

    Under `extra` such a name is ignored for the keyword, or overrules it, and either way
    the model does not run as the entry says. Read off the class's pydantic fields, so a
    provider renaming one moves this with it.
    """
    try:
        chat_class = wire_format.resolve()
    except (ImportError, AttributeError):
        return {}  # refused when it is built, by the error that says why
    fields = getattr(chat_class, "model_fields", None)
    if not isinstance(fields, Mapping):
        return {}
    spelled: dict[str, str] = {}
    for name, field in fields.items():
        # Not `alias`: pydantic copies it here, and a class may set this alone.
        alias = getattr(field, "validation_alias", None)
        spellings = {name, alias} if isinstance(alias, str) else {name}
        keyword = min(spellings & BUILT_WITH, default=None)
        if keyword is not None:
            spelled.update(dict.fromkeys(spellings - BUILT_WITH, keyword))
    return MappingProxyType(spelled)


def build_model(profile: ModelProfile, endpoint: Endpoint) -> BaseChatModel:
    """Build a chat model from `profile`, pointed at `endpoint`."""
    wire_format = endpoint.wire_format
    if profile.max_retries is not None and wire_format.lands.max_retries is None:
        msg = (
            f"model {profile.model!r} sets max_retries, and api {endpoint.api!r} names "
            f"{wire_format.chat_class}, whose Landing has nowhere to keep it; leave it out "
            "for this model, or give the format's Landing a max_retries"
        )
        raise ConfigError(msg)
    model = wire_format.resolve()(
        model=profile.model,
        base_url=endpoint.base_url,
        api_key=endpoint.api_key,
        **profile.kwargs(),
        **profile.extra,
        **wire_format.extra,
    )
    _refuse_what_did_not_land(model, wire_format, profile, endpoint)
    _refuse_an_output_reservation_that_fills_the_window(model, profile)
    _refuse_a_clearing_mark_the_summarizer_reaches_first(model, profile)
    return model


def _advertised_window(model: BaseChatModel) -> int | None:
    """What this model says its input window is, or `None` where it says nothing.

    Read off the built model, not a table of our own: a name the provider's profiles
    do not know, as on most gateway rows, carries no window and is left alone.
    """
    advertised = getattr(model, "profile", None)
    if not isinstance(advertised, Mapping):
        return None
    window = advertised.get("max_input_tokens")
    if isinstance(window, bool) or not isinstance(window, int) or window <= 0:
        return None
    return window


def _refuse_an_output_reservation_that_fills_the_window(
    model: BaseChatModel, profile: ModelProfile
) -> None:
    """Refuse a row whose `max_tokens` leaves no room for the conversation.

    `max_tokens` is the output reservation, taken off the window before a request is
    sized, so a window-sized one refuses every turn with a message naming no model or
    row. Refused rather than warned, since no request could be sent; the window moves
    with the provider's table, so a row starting to fail after an upgrade is right.
    """
    window = _advertised_window(model)
    if window is None or int(window * FILLABLE) - profile.max_tokens > 0:
        return
    fills = int(window * FILLABLE)
    msg = (
        f"model {profile.model!r} reserves max_tokens: {profile.max_tokens} of an "
        f"advertised {window}-token input window, leaving nothing for the conversation: "
        f"at most {fills} of that window is filled, and the output reservation comes off "
        f"it first, so every turn would be refused before it was sent. Lower max_tokens "
        f"in models.yaml -- it is how much the model may *write*, not how much it may "
        f"read, and the window is this model's own, from the table its class ships with"
    )
    raise ConfigError(msg)


def _reserved_output(model: BaseChatModel) -> int:
    """The output tokens deepagents' `_input_budget` takes off the window for `model`."""
    reserved = (
        getattr(model, key, None)
        for key in ("max_tokens", "max_completion_tokens", "max_output_tokens")
    )
    return max(
        (one for one in reserved if isinstance(one, int) and not isinstance(one, bool)),
        default=0,
    )


#: The name deepagents' summariser answers to. deepagents merges middleware by name, so a
#: deployment's answering to it is the summariser that runs, in its place.
SUMMARIZER = "SummarizationMiddleware"


def summarizer_trigger(model: BaseChatModel, stack: Sequence[Any] = ()) -> int:
    """How large a conversation on `model` gets, in deepagents' approximate tokens,
    before the summariser of a graph handed `stack` rewrites it.

    The last of `stack` answering to `SUMMARIZER` is that summariser, as deepagents'
    merge keeps the last; with none, or one `supplied_trigger` cannot read, it is
    deepagents' own, read from `compute_summarization_defaults`, never a number copied
    out of it. Capped where the request's input budget is reached first.
    """
    supplied = [one for one in stack if getattr(one, "name", None) == SUMMARIZER]
    trigger = supplied_trigger(supplied[-1]) if supplied else None
    if trigger is None:
        trigger = _deepagents_own(model)
    window = _advertised_window(model)
    if window is not None:
        # Over the budget, not at it, so one past it.
        trigger = min(trigger, int(window * FILLABLE) - _reserved_output(model) + 1)
    return trigger


def _deepagents_own(model: BaseChatModel) -> int:
    """Where deepagents' own summariser on `model` fires by size: a fraction is picked only
    where the profile holds a window."""
    # Here, so a catalogue reading `WIRE_FORMATS` does not load deepagents.
    from deepagents.middleware.summarization import (  # noqa: PLC0415
        compute_summarization_defaults,
    )

    kind, value = compute_summarization_defaults(model)["trigger"]
    window = _summarised_window(model)
    if kind == "fraction" and window is not None:
        return max(1, int(window * value))
    return int(value)


def supplied_trigger(summarizer: Any) -> int | None:
    """The size at which `summarizer` first summarises, or `None` where that cannot be read.

    Read off deepagents' summariser or langchain's, whose clauses are OR'd and AND'd
    within: a clause counting messages alone never fires by size and is skipped, so a
    trigger of only those has none. Their own clauses and window, not a copy of how
    either parses a trigger.
    """
    helper = _langchains(summarizer)
    if helper is None:
        return None
    return _earliest(helper._trigger_clauses, _summarised_window(helper.model))


def _langchains(summarizer: Any) -> Any:
    """The langchain summariser that holds `summarizer`'s trigger and counter, or `None`
    where it is neither langchain's nor deepagents'."""
    # Here, as in `_deepagents_own`.
    from deepagents.middleware.summarization import (  # noqa: PLC0415
        SummarizationMiddleware as DeepAgentsSummarization,
    )
    from langchain.agents.middleware import (  # noqa: PLC0415
        SummarizationMiddleware as LangchainSummarization,
    )

    # deepagents' is no langchain one: it holds one, which keeps both.
    helper = (
        summarizer._lc_helper if isinstance(summarizer, DeepAgentsSummarization) else summarizer
    )
    return helper if isinstance(helper, LangchainSummarization) else None


@dataclass(frozen=True)
class SummarizerCount:
    """How the summariser a graph runs counts tokens, so what is measured against its
    trigger is measured in its unit: a deployment calibrating the counter moves both."""

    #: The count it decides to summarise on. langchain's default scales it by the
    #: provider's last reported usage.
    whole: Callable[..., int]
    #: One message as it sizes its cut, never scaled: a reply counted alone scales to
    #: the whole conversation its usage reported.
    each: Callable[..., int]
    #: Whether both take the tools' schemas, which deepagents counts where they do.
    tools: bool

    def request(self, messages: Sequence[Any], tools: Sequence[Any]) -> int:
        return self.whole(messages, tools=tools) if self.tools else self.whole(messages)

    def unscaled(self, messages: Sequence[Any], tools: Sequence[Any]) -> int:
        return self.each(messages, tools=tools) if self.tools else self.each(messages)


def summarizer_count(model: BaseChatModel, stack: Sequence[Any] = ()) -> SummarizerCount:
    """How the summariser of a graph on `model` handed `stack` counts: the one
    `summarizer_trigger` reads, and deepagents' own where it reads none."""
    # Here, as in `_deepagents_own`.
    from deepagents.middleware.summarization import (  # noqa: PLC0415
        _token_counter_accepts_tools,
    )
    from langchain.agents.middleware.summarization import (  # noqa: PLC0415
        _get_approximate_token_counter,
    )

    supplied = [one for one in stack if getattr(one, "name", None) == SUMMARIZER]
    helper = _langchains(supplied[-1]) if supplied else None
    if helper is not None:
        whole, each = helper.token_counter, helper._partial_token_counter
    else:
        # What langchain puts in place of the `count_tokens_approximately` deepagents
        # hands it: not 4 characters a token on every model.
        whole = _get_approximate_token_counter(model)
        # Typed as taking the messages alone, as langchain's own copy of this line is.
        each = partial(whole, use_usage_metadata_scaling=False)  # ty: ignore[unknown-argument]
    # One whose signature cannot be read is asked without them, where deepagents tries both.
    return SummarizerCount(whole, each, tools=_token_counter_accepts_tools(whole) is True)


def _earliest(clauses: Sequence[Mapping[str, float]], window: int | None) -> int | None:
    """The smallest size at which any of `clauses` fires, each needing all of its parts.

    A fraction is of `window` and at least one token, as langchain counts it; with no
    window its clause never fires.
    """
    sizes: list[int] = []
    for clause in clauses:
        parts: list[int] = []
        for kind, value in clause.items():
            if kind == "tokens":
                parts.append(int(value))
            elif kind == "fraction":
                if window is None:
                    break
                parts.append(max(1, int(window * value)))
        else:
            if parts:
                sizes.append(max(parts))
    return min(sizes, default=None)


def _summarised_window(model: Any) -> int | None:
    """The window a summariser takes a fraction of: langchain's reading of the profile, which
    unlike `_advertised_window` takes any integer, so `max_input_tokens: 0` fires at one token."""
    profile = getattr(model, "profile", None)
    window = profile.get("max_input_tokens") if isinstance(profile, Mapping) else None
    return window if isinstance(window, int) else None


def _refuse_a_clearing_mark_the_summarizer_reaches_first(
    model: BaseChatModel, profile: ModelProfile
) -> None:
    """Refuse a `clear_results_at` at or past where this model's conversation is summarised.

    The summariser counts each call before clearing does, so a mark it reaches first
    would never clear anything and say nothing.
    """
    mark = profile.clear_results_at
    if mark is None or mark < (trigger := summarizer_trigger(model)):
        return
    window = _advertised_window(model)
    where = (
        f"an advertised {window}-token input window"
        if window is not None
        else "no advertised input window"
    )
    msg = (
        f"model {profile.model!r} sets clear_results_at: {mark}, and deepagents summarises "
        f"its conversation at {trigger} tokens ({where}), before clearing would start. "
        f"Lower it below {trigger} in models.yaml, or leave it out for the default"
    )
    raise ConfigError(msg)


def _refuse_what_did_not_land(
    model: BaseChatModel, wire_format: WireFormat, profile: ModelProfile, endpoint: Endpoint
) -> None:
    """Refuse a model that did not keep a value it was built with.

    A chat class that does not know a keyword moves it to `model_kwargs` with a warning
    and builds anyway. For `base_url` that leaves the URL unset, and the request -- with
    this endpoint's key -- goes to the vendor's default host instead of the gateway.
    """
    sent = {
        "model": profile.model,
        "base_url": endpoint.base_url,
        "api_key": endpoint.api_key,
        "max_tokens": profile.max_tokens,
        "timeout": profile.timeout_s,
    }
    if profile.max_retries is not None:
        sent["max_retries"] = profile.max_retries
    for value, expected in sent.items():
        site = getattr(wire_format.lands, value)
        landed = getattr(model, site, None)
        if hasattr(landed, "get_secret_value"):
            landed = landed.get_secret_value()
        if landed != expected:
            # The key is named and never quoted: this message goes to a log.
            found = "something else" if value == "api_key" else repr(landed)
            msg = (
                f"endpoint {profile.endpoint!r} names api {endpoint.api!r}, whose "
                f"{wire_format.chat_class} did not keep {value!r} at {site!r} (found "
                f"{found}); refusing to build a model that would send its requests "
                "somewhere nobody chose"
            )
            raise ConfigError(msg)


def model_named(
    written: str, cfg: Config, *, endpoints: Selection, subject: str
) -> BaseChatModel:
    """The model a written name means, on an endpoint this request may reach.

    Shared with `model_object`: a copy that skipped the endpoint check would send a run
    to an endpoint the caller refused, silently.
    """
    try:
        profile, endpoint = cfg.models.resolve(written)
    except ConfigError as exc:
        # Only the caller knows who asked, which the reader needs.
        msg = f"{subject}: {exc}"
        raise ConfigError(msg) from exc
    refuse_ungranted_endpoint(profile.endpoint, granted=endpoints, subject=subject)
    return build_model(profile, endpoint)


def deployment_default(cfg: Config, *, endpoints: Selection) -> BaseChatModel:
    """The deployment's default model, on an endpoint this request may reach.

    Checked as a named one is, or a definition naming no model would reach any endpoint.
    """
    named = cfg.models.default
    return model_named(named, cfg, endpoints=endpoints, subject=f"the default model {named!r}")
