"""Model construction."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING

from kingfisher.config import Adapter, ConfigError, Landing
from kingfisher.domain.capabilities import ALL, Selection, refuse_ungranted_endpoint

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from kingfisher.config import Config, Endpoint, ModelProfile


#: The wire formats kingfisher ships. `models.yaml` picks from these by name
#: through an endpoint's `api`, together with any a deployment adds through
#: `KINGFISHER_ADAPTERS_FACTORY`.
#:
#: A row names its package as a string and `resolve` imports it by name, so neither
#: provider is an import anywhere in `src/` and neither belongs in `THIRD_PARTY` --
#: which reads imports, and now refuses a grant nothing spends. Adding one back to
#: that table on the strength of a row here fails
#: `test_no_area_is_granted_a_dependency_it_does_not_import`.
ADAPTERS: Mapping[str, Adapter] = {
    # The gateway path. MiniMax and anything else imitating Anthropic's wire
    # format lives here — see models.yaml.example, which recommends this style.
    "anthropic": Adapter(
        "langchain_anthropic:ChatAnthropic",
        Landing(
            model="model",
            base_url="anthropic_api_url",
            api_key="anthropic_api_key",
            max_tokens="max_tokens",
            timeout="default_request_timeout",
        ),
    ),
    # OpenAI proper, on the Responses API. This adapter is *not* a general
    # OpenAI-compatible client: `/v1/responses` is what we target, and virtually no
    # gateway imitating OpenAI implements it. Point a gateway at the anthropic adapter
    # instead. A future Chat-Completions row would be a new entry here, not a flag on
    # this one.
    "openai_responses": Adapter(
        "langchain_openai:ChatOpenAI",
        Landing(
            model="model_name",
            base_url="openai_api_base",
            api_key="openai_api_key",
            max_tokens="max_tokens",
            timeout="request_timeout",
        ),
        MappingProxyType({"use_responses_api": True}),
    ),
}


#: The share of a model's advertised input window a request may fill, from deepagents'
#: `SummarizationMiddleware._input_budget`: it reserves the configured output tokens and
#: five per cent of headroom, then refuses anything over what is left. Mirrored here
#: because a row reserving more than this can send nothing at all, and this is the only
#: place that can say so before a turn starts.
#: `test_the_share_a_request_may_fill_is_the_one_deepagents_applies` drives their own
#: function with it, so this going stale is a red test rather than a wrong refusal.
FILLABLE = 0.95


def build_model(profile: ModelProfile, endpoint: Endpoint) -> BaseChatModel:
    """Build a chat model from `profile`, pointed at `endpoint`."""
    adapter = endpoint.adapter
    model = adapter.resolve()(
        model=profile.model,
        base_url=endpoint.base_url,
        api_key=endpoint.api_key,
        **profile.kwargs(),
        **profile.extra,
        **adapter.extra,
    )
    _refuse_what_did_not_land(model, adapter, profile, endpoint)
    _refuse_an_output_reservation_that_fills_the_window(model, profile)
    _refuse_a_clearing_mark_the_summarizer_reaches_first(model, profile)
    return model


def _advertised_window(model: BaseChatModel) -> int | None:
    """What this model says its input window is, or `None` where it says nothing.

    Read off the built model rather than from a table of our own: the number comes
    from the provider package's profile for this model name, a row's `extra: {profile:
    ...}` replaces that whole dict, and a model the table has never heard of -- which
    is every gateway row naming its own name -- carries no window and is left alone.
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

    `max_tokens` is the *output* reservation, and a window-sized one reads like the
    model's own limit -- which is the mistake: the reservation is subtracted from the
    window before a request is sized, so every turn is refused before it is sent, with
    a message naming no model, no row and no number. Measured through the real command:
    `max_tokens: 190001` on a 200,000-token window answered
    `ContextOverflowError: Context remains above the input budget after compaction`,
    and nothing had said anything at startup.

    Refused rather than warned because no request of any size can be sent, so it is not
    a decision anybody could have meant. The window moves when the provider's table
    does -- it moved from 1,000,000 to 200,000 under this project once -- and a row that
    started yesterday failing to start today is the right outcome when every turn would
    otherwise die.
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

    Read from `compute_summarization_defaults`, which is what sizes the summariser on
    every graph, and never from a number copied out of it: 85% of the window, or 170,000
    tokens with none. It also summarises once a request is over its input budget, the
    sooner of the two where `max_tokens` is above a tenth of the window.

    Imported here rather than at the top, because a catalogue reading `ADAPTERS` should
    not load deepagents to do it.
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
    would never clear anything and say nothing. The highest mark this lets through
    almost never clears, which is the nearest thing to off there is.
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
    model: BaseChatModel, adapter: Adapter, profile: ModelProfile, endpoint: Endpoint
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
    for value, expected in sent.items():
        site = getattr(adapter.lands, value)
        landed = getattr(model, site, None)
        if hasattr(landed, "get_secret_value"):
            landed = landed.get_secret_value()
        if landed != expected:
            # The key is named and never quoted: this message goes to a log.
            found = "something else" if value == "api_key" else repr(landed)
            msg = (
                f"endpoint {profile.endpoint!r} names api {endpoint.api!r}, whose "
                f"{adapter.chat_class} did not keep {value!r} at {site!r} (found "
                f"{found}); refusing to build a model that would send its requests "
                "somewhere nobody chose"
            )
            raise ConfigError(msg)


def model_named(
    written: str, cfg: Config, *, endpoints: Selection = ALL, subject: str
) -> BaseChatModel:
    """The model a written name means, on an endpoint this request may reach.

    Shared with `model_object`, which is this with a definition in front of it rather
    than a name. A second copy of these three steps that forgot the middle one would
    send a run to an endpoint the caller refused and say nothing.
    """
    # A lookup, rather than a `replace` of four `Config` fields built from the copy.
    # A param nobody remembered to add to that copy was silently the deployment's
    # own, so a per-model `max_tokens` was dropped without a word; a profile carries
    # every param and there is nothing here to forget.
    try:
        profile, endpoint = cfg.models.resolve(written)
    except ConfigError as exc:
        # `resolve` knows the model and the catalogue; only the caller knows *who
        # asked*. Without that, the reader is told `gpt-5` cannot be run and left to
        # grep the catalogue for whoever wanted it.
        msg = f"{subject}: {exc}"
        raise ConfigError(msg) from exc
    refuse_ungranted_endpoint(profile.endpoint, granted=endpoints, subject=subject)
    return build_model(profile, endpoint)
