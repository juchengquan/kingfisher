"""A model the provider cannot serve now: the turn stops, and a subagent's is handed back."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from langchain_core.messages import AIMessage, ToolMessage

from kingfisher import Kingfisher, Request
from kingfisher.core.agent.middlewares.model_reply import ModelReplyMiddleware
from kingfisher.core.session.runtime import ModelUnavailableError, unavailable
from kingfisher.plugins import LocalSessions
from tests.conftest import an_agent, start, subagents_dir
from tests.unit.scripted import Scripted


class RefusedError(Exception):
    """Shaped like a provider SDK's status error: a code, and the body it came with."""

    def __init__(self, status: int, message: str = "rate limited") -> None:
        super().__init__(f"Error code: {status}")
        self.status_code = status
        self.body = {"type": "error", "error": {"type": "rate_limit_error", "message": message}}


class APITimeoutError(Exception):
    """Named as both SDKs name theirs, which is all `unavailable` reads of it."""


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
    ],
    ids=["429", "503", "529", "sdk-timeout", "httpx-timeout", "timeout", "httpx-connect",
         "connection-reset", "chained", "own"],
)
def test_a_failure_that_may_pass_is_named_with_the_providers_words(exc, why):
    """A 429 or 5xx, a timeout or a dropped connection, from either SDK or httpx under
    them, and through the cause langchain re-raises it from."""
    assert unavailable(exc) == why


@pytest.mark.parametrize(
    "exc", [RefusedError(400), RefusedError(401), ValueError("a bug")], ids=["400", "401", "bug"]
)
def test_a_failure_that_will_not_pass_is_left_alone(exc):
    """The control: a 401 is a configuration error, a 400 fails the same way again, and a
    bug keeps its traceback."""
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
    guard = ModelReplyMiddleware(subagent=False, outputs="/outputs")

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
    an_agent(scripted)
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


def test_a_failure_that_will_not_pass_still_raises(refusing):
    """The control: only the failures that may pass become a stop."""
    with pytest.raises(RefusedError):
        _turn(refusing, RefusedError(400))


@pytest.mark.parametrize("way", ["sync", "async"])
def test_a_subagents_refused_call_is_handed_back_and_the_turn_goes_on(refusing, way):
    """A subagent's refused call took the whole turn down with it, and the parent never
    heard; it is the parent's to go on without that part."""
    subagents_dir(refusing).mkdir(parents=True, exist_ok=True)
    (subagents_dir(refusing) / "helper.yaml").write_text(
        "name: helper\ndescription: Helps.\nsystem_prompt: |\n  You help.\n", encoding="utf-8"
    )
    an_agent(refusing, subagents="[helper]")
    asked = {"description": "go", "subagent_type": "helper"}
    to_helper = AIMessage(content="", tool_calls=[{"name": "task", "args": asked, "id": "t1"}])

    result = _turn(refusing, to_helper, RefusedError(429), AIMessage("carried on"), way=way)

    told = [m for m in Scripted.heard[-1] if isinstance(m, ToolMessage)]
    assert "'helper' could not reach its model (429: rate limited)" in str(told[-1].content)
    assert result.stop_reason == "end_turn"
    assert result.answer == "carried on"
