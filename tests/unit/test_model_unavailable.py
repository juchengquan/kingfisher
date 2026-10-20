"""A model the provider cannot serve now: the turn stops, and a subagent's is handed back."""

from __future__ import annotations

import asyncio
from typing import Any

import anthropic
import httpx
import pytest
from langchain_core.messages import AIMessage, ToolMessage

from kingfisher import ConfigError, Kingfisher, Request
from kingfisher.core.agent.middlewares.guarded_calls import GuardedCallsMiddleware
from kingfisher.core.agent.middlewares.model_reply import ModelReplyMiddleware
from kingfisher.core.session.runtime import ModelUnavailableError, unavailable
from kingfisher.plugins import LocalSessions
from tests.conftest import EVERY_TOOL, an_agent, start, subagents_dir
from tests.unit.scripted import Scripted


class RefusedError(Exception):
    """Shaped like a provider SDK's status error: a code, and the body it came with."""

    def __init__(self, status: int, message: str = "rate limited") -> None:
        super().__init__(f"Error code: {status}")
        self.status_code = status
        self.body = {"type": "error", "error": {"type": "rate_limit_error", "message": message}}


class APITimeoutError(Exception):
    """Named as both SDKs name theirs, which is all `unavailable` reads of it."""


def streamed(kind: str, message: str = "Overloaded") -> anthropic.APIStatusError:
    """What the anthropic SDK raises for an `error` event inside a stream that began as a
    200: the SDK's own class, so a change to how it sets `type` lands here."""
    body = {"type": "error", "error": {"type": kind, "message": message}}
    response = httpx.Response(200, request=httpx.Request("POST", "https://api.invalid"))
    return anthropic.APIStatusError(str(body), response=response, body=body)


class StreamedError(Exception):
    """A 200 stream's error carrying its type only in the body, as an SDK may leave it."""

    def __init__(self, kind: Any) -> None:
        super().__init__("in the stream")
        self.status_code = 200
        self.body = {"type": "error", "error": {"type": kind, "message": "busy"}}


def _chained() -> Exception:
    """An SDK's error under langchain's re-raise of it, as `raise ... from` leaves them."""
    wrapped = RuntimeError("langchain's own")
    wrapped.__cause__ = RefusedError(429, "quota")
    return wrapped


@pytest.mark.parametrize(
    ("exc", "why"),
    [
        (RefusedError(429, "quota"), "429: quota"),
        (RefusedError(503), "503: rate limited"),
        (RefusedError(529), "529: rate limited"),
        (APITimeoutError(), "the request timed out"),
        (httpx.ReadTimeout("slow"), "the request timed out"),
        (TimeoutError(), "the request timed out"),
        (httpx.ConnectError("refused"), "the connection to the provider failed"),
        (ConnectionResetError(), "the connection to the provider failed"),
        (_chained(), "429: quota"),
        (ModelUnavailableError("said already"), "said already"),
        (streamed("overloaded_error"), "overloaded_error: Overloaded"),
        (streamed("api_error", "Internal server error"), "api_error: Internal server error"),
        (streamed("rate_limit_error", "quota"), "rate_limit_error: quota"),
        (StreamedError("overloaded_error"), "overloaded_error: busy"),
    ],
    ids=["429", "503", "529", "sdk-timeout", "httpx-timeout", "timeout", "httpx-connect",
         "connection-reset", "chained", "own", "streamed-overloaded", "streamed-api",
         "streamed-rate-limit", "streamed-body-only"],
)
def test_a_failure_that_may_pass_is_named_with_the_providers_words(exc, why):
    """A 429 or 5xx, a timeout or a dropped connection, from either SDK or httpx under
    them, and through the cause langchain re-raises it from; and Anthropic's error sent
    inside a 200 stream, which read as a 200 and killed the turn with a traceback."""
    assert unavailable(exc) == why


@pytest.mark.parametrize(
    "exc",
    [RefusedError(400), RefusedError(401), ValueError("a bug"),
     streamed("invalid_request_error"), StreamedError(["overloaded_error"])],
    ids=["400", "401", "bug", "streamed-invalid", "streamed-unhashable"],
)
def test_a_failure_that_will_not_pass_is_left_alone(exc):
    """The control: a 401 is a configuration error, a 400 fails the same way again, a
    bug keeps its traceback, and so does a stream's error of any other type."""
    assert unavailable(exc) is None


# -- the guard on its own ------------------------------------------------------------


class Call:
    def __init__(self, name: str, **args: Any) -> None:
        self.tool_call = {"name": name, "args": args, "id": "c1"}
        self.state = {"messages": []}


def _raising(exc: Exception):
    def handler(_: Any) -> Any:
        raise exc

    return handler


def test_a_refused_model_call_is_raised_as_the_stop_and_another_as_itself():
    """Raised as kingfisher's own, so the turn can end on it without catching a tool's."""
    guard = ModelReplyMiddleware(subagent=False, outputs="/outputs")

    with pytest.raises(ModelUnavailableError, match="429: rate limited"):
        guard.wrap_model_call(None, _raising(RefusedError(429)))
    with pytest.raises(RefusedError):
        guard.wrap_model_call(None, _raising(RefusedError(400)))


def test_only_a_task_call_hands_a_refusal_back():
    """A tool's own timeout is the tool's failure, not a model that cannot answer; and a
    compiled subagent's raw provider error is handed back as a declared one's is."""
    guard = GuardedCallsMiddleware()

    told = guard.wrap_tool_call(Call("task", subagent_type="helper"), _raising(RefusedError(429)))
    assert isinstance(told, ToolMessage)
    assert told.status == "error"
    assert "'helper' could not reach its model (429: rate limited)" in str(told.content)

    with pytest.raises(TimeoutError):
        guard.wrap_tool_call(Call("fetch"), _raising(TimeoutError()))
    with pytest.raises(RefusedError):
        guard.wrap_tool_call(Call("task", subagent_type="helper"), _raising(RefusedError(400)))


# -- through real turns ----------------------------------------------------------------


@pytest.fixture
def refusing(scripted, monkeypatch):
    """`scripted`, where an exception in the script is raised by the model call it reaches."""
    original = Scripted._generate

    def generate(self, messages, stop=None, run_manager=None, **kwargs):
        queued: Any = Scripted.script[0] if Scripted.script else None
        if isinstance(queued, Exception):
            Scripted.script.popleft()
            raise queued
        return original(self, messages, stop, run_manager, **kwargs)

    monkeypatch.setattr(Scripted, "_generate", generate)
    an_agent(scripted, **EVERY_TOOL)
    start(scripted, "s")
    return scripted


def _queue(*script: Any) -> None:
    """Replies and refusals, in the order the model calls reach them."""
    Scripted.script.extend(script)


def _turn(cfg: Any, *script: Any, way: str = "sync") -> Any:
    _queue(*script)
    service = Kingfisher(cfg, sessions=LocalSessions())
    request = Request("go", agent="only", session_id="s")
    return asyncio.run(service.arun(request)) if way == "async" else service.run(request)


@pytest.mark.parametrize("way", ["sync", "async"])
def test_a_refused_model_call_stops_the_turn_rather_than_raising(refusing, way):
    """A 429 escaped `run` as the SDK's exception, and the command printed two tracebacks
    and exited 1, the code it keeps for a turn stopped at a bound."""
    result = _turn(refusing, RefusedError(429, "quota"), way=way)

    assert result.stop_reason == "model_unavailable"
    assert not result.completed


@pytest.mark.parametrize("way", ["sync", "async"])
def test_a_refusal_of_the_asking_again_after_a_cut_answer_is_the_stop_too(refusing, way):
    """The refusal read only on the first call a reply takes, so a provider refusing the
    second asking raised its SDK's exception out of the turn."""
    from tests.unit.test_cut_replies import reply

    result = _turn(refusing, reply(text="The findings are: first, the"), RefusedError(429), way=way)

    assert result.stop_reason == "model_unavailable"


def test_the_stop_says_what_the_provider_said(refusing):
    """The status and the provider's own words, where the caller watches the turn."""
    _queue(RefusedError(429, "Token Plan limit reached"))
    events = list(
        Kingfisher(refusing, sessions=LocalSessions()).stream(
            Request("go", agent="only", session_id="s")
        )
    )

    (said,) = [one.text for one in events if one.kind == "cut_short"]
    assert "429: Token Plan limit reached" in said
    assert "run it again on this session" in said


@pytest.mark.parametrize("way", ["sync", "async"])
def test_an_overload_inside_a_stream_stops_the_turn_rather_than_raising(refusing, way):
    """Anthropic's `overloaded_error` sent after a 200 escaped `run` as the SDK's
    exception, because its status read as a success."""
    result = _turn(refusing, streamed("overloaded_error"), way=way)

    assert result.stop_reason == "model_unavailable"
    assert not result.completed


def test_a_failure_that_will_not_pass_still_raises(refusing):
    """The control: only the failures that may pass become a stop; a 400 is the
    deployment's to fix, and says so."""
    with pytest.raises(ConfigError, match=r"\(400\)"):
        _turn(refusing, RefusedError(400))


@pytest.mark.parametrize("way", ["sync", "async"])
def test_a_subagents_refused_call_is_handed_back_and_the_turn_goes_on(refusing, way):
    """A subagent's refused call took the whole turn down with it, and the parent never
    heard; it is the parent's to go on without that part."""
    subagents_dir(refusing).mkdir(parents=True, exist_ok=True)
    (subagents_dir(refusing) / "helper.yaml").write_text(
        "name: helper\ndescription: Helps.\n"
        'builtin_tools: ["*"]\ntools: ["*"]\n'
        "system_prompt: |\n  You help.\n",
        encoding="utf-8",
    )
    an_agent(refusing, subagents="[helper]", **EVERY_TOOL)
    asked = {"description": "go", "subagent_type": "helper"}
    to_helper = AIMessage(content="", tool_calls=[{"name": "task", "args": asked, "id": "t1"}])

    result = _turn(refusing, to_helper, RefusedError(429), AIMessage("carried on"), way=way)

    told = [m for m in Scripted.heard[-1] if isinstance(m, ToolMessage)]
    assert "'helper' could not reach its model (429: rate limited)" in str(told[-1].content)
    assert result.stop_reason == "end_turn"
    assert result.answer == "carried on"
