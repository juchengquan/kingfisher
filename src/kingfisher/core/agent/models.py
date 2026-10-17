"""Model construction."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING

from kingfisher.core.access.capabilities import ALL, Selection, refuse_ungranted_endpoint
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


def summarizer_trigger(model: BaseChatModel) -> int:
    """How large a conversation on `model` gets, in deepagents' approximate tokens,
    before its summariser rewrites it.

    Read from `compute_summarization_defaults`, never a number copied out of it, and
    capped where the request's input budget is reached first.

    Imported here, so a catalogue reading `WIRE_FORMATS` does not load deepagents.
    """
    from deepagents.middleware.summarization import (  # noqa: PLC0415
        compute_summarization_defaults,
    )

    kind, value = compute_summarization_defaults(model)["trigger"]
    window = _advertised_window(model)
    if kind != "fraction":
        trigger = int(value)
    elif window is None:
        # A profile deepagents read as a window and `_advertised_window` refuses as
        # one -- `max_input_tokens: 0` -- for which langchain triggers at one token.
        return 1
    else:
        trigger = max(1, int(window * value))
    if window is not None:
        # Over the budget, not at it, so one past it.
        trigger = min(trigger, int(window * FILLABLE) - _reserved_output(model) + 1)
    return trigger


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
    written: str, cfg: Config, *, endpoints: Selection = ALL, subject: str
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
