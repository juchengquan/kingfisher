"""What a model provider's error says: a refusal that may pass, which stops the turn, or
one that will not, which is the deployment's configuration error."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from kingfisher.core.config import ConfigError

from .runtime import PREVIEW, ModelUnavailableError

if TYPE_CHECKING:
    from kingfisher.core.config import Config, Endpoint

# Matched on the exception's `status_code`, not its class: `anthropic` and `openai` each
# raise their own `AuthenticationError`, and the command may import neither.
#: A provider declining a request it may serve later: a rate limit or quota, and the
#: 5xx range, where Anthropic's 529 "overloaded" sits. A 401 is a configuration error,
#: and the rest of the 4xx range is a request that will fail the same way again.
TOO_MANY_REQUESTS = 429
SERVER_ERROR = 500
UNAUTHORIZED = 401
CLIENT_ERROR = 400

#: Anthropic's names for those same refusals, all that an error sent inside a stream
#: carries: the stream began as a 200, and the SDK raises it with that status.
UNSERVED_TYPES = frozenset({"overloaded_error", "api_error", "rate_limit_error"})
#: And the one for a 400, which a stream's error is just as able to carry.
REFUSED_TYPE = "invalid_request_error"

#: Matched by name through the class's ancestry, so neither SDK -- nor httpx, which both
#: send through -- is imported here.
TIMED_OUT = frozenset({"APITimeoutError", "TimeoutException", "ModelTimeoutError"})
UNREACHED = frozenset(
    {"APIConnectionError", "NetworkError", "RemoteProtocolError", "ModelConnectionError"}
)


def unavailable(exc: BaseException) -> str | None:
    """Why the provider could not answer, where trying again later can work, else `None`.

    The causes are read too: langchain re-raises an SDK's error as one of its own.
    """
    seen: set[int] = set()
    one: BaseException | None = exc
    while one is not None and id(one) not in seen:
        seen.add(id(one))
        if isinstance(one, ModelUnavailableError):
            return one.why
        status = getattr(one, "status_code", None)
        if isinstance(status, int) and (status == TOO_MANY_REQUESTS or status >= SERVER_ERROR):
            return f"{status}: {said(one)}"
        kind = error_type(one)
        if isinstance(status, int) and status < CLIENT_ERROR and kind in UNSERVED_TYPES:
            return f"{kind}: {said(one)}"
        names = {cls.__name__ for cls in type(one).__mro__}
        # Before the connection check: both SDKs' timeout is a kind of connection error.
        if names & TIMED_OUT or isinstance(one, TimeoutError):
            return "the request timed out"
        if names & UNREACHED or isinstance(one, ConnectionError):
            return "the connection to the provider failed"
        one = one.__cause__ or one.__context__
    return None


def error_type(exc: BaseException) -> str | None:
    """The provider's name for the error, on the exception or in the body it came with."""
    kind = getattr(exc, "type", None)
    if not isinstance(kind, str):
        body = getattr(exc, "body", None)
        inner = body.get("error") if isinstance(body, dict) else None
        kind = inner.get("type") if isinstance(inner, dict) else None
    # A body is whatever JSON the stream sent, and a list in it is unhashable.
    return kind if isinstance(kind, str) else None


def said(exc: BaseException) -> str:
    """The provider's own words, from the body where it sent one."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        inner = body.get("error", body)
        if isinstance(inner, dict) and isinstance(inner.get("message"), str):
            return inner["message"][:PREVIEW]
    return str(getattr(exc, "message", None) or exc)[:PREVIEW]


def refused_credentials(exc: BaseException, cfg: Config) -> ConfigError | None:
    """A provider's 401 as the configuration error it is, or `None` for anything else.

    The endpoint is matched by the failed request's URL, not taken as the default: a
    subagent may run elsewhere. The shell is named because `load_dotenv` runs with
    `override=False`, so an exported key beats the one in `.env`.
    """
    if getattr(exc, "status_code", None) != UNAUTHORIZED:
        return None
    named = _endpoint_of(exc, cfg)
    where = f"endpoint {named[0][0]!r}" if named else "the model endpoint"
    if named and (entry := named[0][1].key_entry):
        return ConfigError(
            f"{where} rejected the key from entry {entry!r} in {cfg.models.keys_file} "
            f"(401); that entry is the one to check"
        )
    supplied = f" from {named[0][1].key_env}" if named and named[0][1].key_env else ""
    return ConfigError(
        f"{where} rejected the key{supplied} (401). A value exported in the shell "
        f"overrides the one in .env, so check the environment before the file"
    )


def refused_request(exc: BaseException, cfg: Config) -> ConfigError | None:
    """A provider's 4xx, or the same refusal sent inside a stream, as the configuration
    error it is, or `None` for anything else.

    Not a 429, which may pass and ends a turn as a stop before this is reached. The rest
    of the range -- a model name the endpoint does not serve, a request it will not
    take -- fails the same way on every retry, so the fix is in the deployment.
    """
    if (refused := refused_credentials(exc, cfg)) is not None:
        return refused
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int) or status == TOO_MANY_REQUESTS:
        return None
    # Named by its type: a stream's error carries the 200 the stream began with.
    if status < CLIENT_ERROR and error_type(exc) == REFUSED_TYPE:
        code: int | str = REFUSED_TYPE
    elif CLIENT_ERROR <= status < SERVER_ERROR:
        code = status
    else:
        return None
    named = _endpoint_of(exc, cfg)
    where = f"endpoint {named[0][0]!r} ({named[0][1].base_url})" if named else "the endpoint"
    model = _model_of(exc)
    which = f"model {model!r}" if model else "the model"
    return ConfigError(f"{where} refused a call to {which} ({code}): {said(exc)}")


def _endpoint_of(exc: BaseException, cfg: Config) -> list[tuple[str, Endpoint]]:
    """The configured endpoints the failed request was sent to, by its URL."""
    url = str(getattr(getattr(getattr(exc, "response", None), "request", None), "url", ""))
    return [
        (name, endpoint)
        for name, endpoint in cfg.models.endpoints.items()
        if url.startswith(endpoint.base_url)
    ]


def _model_of(exc: BaseException) -> str | None:
    """The model the failed request named, read from the body it sent."""
    request = getattr(getattr(exc, "response", None), "request", None)
    try:
        body = json.loads(getattr(request, "content", b"") or b"{}")
    # httpx raises `RequestNotRead`, a `RuntimeError`, for a body it streamed.
    except (ValueError, TypeError, RuntimeError):
        return None
    model = body.get("model") if isinstance(body, dict) else None
    return model if isinstance(model, str) else None
