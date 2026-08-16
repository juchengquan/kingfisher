"""Model construction.

Every model in kingfisher is a pre-built instance, never a `"provider:model"`
string — for delegates as much as for the main agent. A string would be
resolved by deepagents through `init_chat_model`, which never sees this
workspace's configuration: the endpoint, the key, the token ceiling and the
timeout would all be silently dropped. A `ModelProfile` is a *record*;
`build_model` is what turns it into an instance, and `delegation.py` routes
subagent models through it too.

A wire format is **data**, not a subclass. The classes differ in exactly two
ways — which one to construct, and any kwargs peculiar to it — while the values
that come from a profile use identical names on all of them. `ChatOpenAI`,
`ChatAnthropic` and `ChatGoogleGenerativeAI` all accept `model`, `base_url`,
`api_key`, `max_tokens` and `timeout` unchanged, because LangChain aliases them
(Gemini's `max_output_tokens` among them). So there is no shared behaviour for a
base class to hold, and a hierarchy would express a one-field difference as a
type.

**This table is closed, and an endpoint table is not.** `ADAPTERS` was
`PROVIDERS`, and it carried the credential variable names too -- which welded
"which wire format" to "which endpoint", 1:1, so a deployment had exactly one
endpoint per wire format and two Anthropic-compatible gateways could not both
exist. Endpoints are now open data in `models.yaml`; what stays here is the part
that needs a Python class behind it.

It stays closed deliberately. `Adapter.resolve` imports the module a row names,
so a row a *deployment* could write would make a config file an arbitrary-import
vector — in a package that sandboxes `execute` precisely because the shell could
otherwise read this deployment's own keys. And the openness would be fake:
`test_models.py` carries a `LANDING_SITES` row per adapter because the classes
disagree about attribute names, so a new wire format needs a kingfisher release
whatever the table is written in.

The classes are imported at module scope rather than inside a builder. The
deferred import was meant to spare a deployment using one style from importing
the other's SDK, or needing it installed at all — and it achieved neither.
deepagents depends on `langchain-anthropic` and `langchain-google-genai`
directly, so both are always installed, and `import kingfisher` has already
loaded the whole provider stack long before anything calls `build_model`.
Measured, not assumed. `pyproject.toml` declares `langchain-anthropic` outright
now, because this module names it and a transitive dependency is not a promise.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from kingfisher.config import ConfigError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from langchain_core.language_models import BaseChatModel

    from kingfisher.config import Endpoint, ModelProfile

_NO_EXTRA: Mapping[str, Any] = MappingProxyType({})


@dataclass(frozen=True)
class Adapter:
    """One wire format: which class speaks it, and what it needs to be told.

    kingfisher targets **gateway-shaped** endpoints — one base URL, one key.
    A wire format without that shape (Bedrock wants a region and a credentials
    profile) is a new field on `Endpoint`, not a new row in this table.

    `extra` is additive only. It carries kwargs the format itself requires, and
    cannot name one of the values that come from the endpoint or the profile —
    Python raises on the duplicate, which is the intended outcome: an adapter
    row must not quietly overrule a value the deployment configured.

    `chat_class` is a `"module:Name"` path rather than the class, resolved when
    a model is actually built. Holding the classes meant importing every
    provider's SDK to describe *one* wire format, and a deployment can only use
    the ones its endpoints name. It buys nothing today, because deepagents
    imports `langchain_openai` itself wherever it is installed; what it buys is
    the option of not installing it, which naming the class made impossible.
    """

    chat_class: str
    extra: Mapping[str, Any] = _NO_EXTRA

    def resolve(self) -> type[BaseChatModel]:
        """Import the chat class this row names."""
        module_name, _, class_name = self.chat_class.partition(":")
        return getattr(import_module(module_name), class_name)


#: The one place a wire format is described. `models.yaml` picks from these by
#: name through an endpoint's `api`; `build_model` constructs through
#: `chat_class`. Adding a row is a kingfisher release, and needs a matching
#: `LANDING_SITES` entry in `test_models.py`.
ADAPTERS: Mapping[str, Adapter] = {
    # The gateway path. MiniMax and anything else imitating Anthropic's wire
    # format lives here — see models.yaml.example, which recommends this style.
    "anthropic": Adapter("langchain_anthropic:ChatAnthropic"),
    # OpenAI proper, on the Responses API. This adapter is *not* a general
    # OpenAI-compatible client: `/v1/responses` is what we target, and
    # virtually no gateway imitating OpenAI implements it. Point a gateway at
    # the anthropic adapter instead. A future Chat-Completions row would be a
    # new entry here, not a flag on this one.
    "openai": Adapter(
        "langchain_openai:ChatOpenAI",
        MappingProxyType({"use_responses_api": True}),
    ),
}


def build_model(profile: ModelProfile, endpoint: Endpoint) -> BaseChatModel:
    """Build a chat model from `profile`, pointed at `endpoint`.

    Two arguments rather than a `Config`, and that is the whole of the fix this
    signature exists for. It used to read five fields off a `Config`, which
    meant a delegate running elsewhere was built by *copying* a `Config` with
    four of them swapped — so a param nobody remembered to add to that copy was
    silently the deployment's own. `max_tokens` was one edit away from being
    exactly that. A profile carries every param, so there is nothing to forget.

    No role parameter. A delegate that runs somewhere else says so in its own
    definition, and `delegation.as_subagent` resolves it through
    `Config.resolve_model` — so "which model, where" is one question with one
    answer, asked of whatever names the model.
    """
    try:
        adapter = ADAPTERS[endpoint.api]
    except KeyError:
        msg = (
            f"endpoint {endpoint.name!r} names api {endpoint.api!r}, which kingfisher "
            f"cannot build; known: {tuple(ADAPTERS)}"
        )
        raise ConfigError(msg) from None

    return adapter.resolve()(
        model=profile.model,
        base_url=endpoint.base_url,
        api_key=endpoint.api_key,
        **profile.kwargs(),
        **profile.extra,
        **adapter.extra,
    )
