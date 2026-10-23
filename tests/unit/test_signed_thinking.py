"""A reply's thinking goes back only to the endpoint and model that produced it."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from anthropic.types import Message
from langchain.agents.middleware import ModelRequest
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models._utils import _update_message_content_to_blocks
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langchain_openai.chat_models.base import _construct_lc_result_from_responses_api
from openai.types.responses import Response

from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.agent.middlewares.caching import PromptCachingMiddleware
from kingfisher.core.agent.middlewares.clearing import ResultClearingMiddleware
from kingfisher.core.agent.middlewares.signed_thinking import (
    SERVED_BY,
    SignedThinkingMiddleware,
    kept_for,
    model_id,
)
from kingfisher.core.agent.middlewares.step_budget import StepBudgetMiddleware
from kingfisher.core.session.ledger import Ledger
from kingfisher.plugins import backend_at
from tests.conftest import EVERY_TOOL, FAKE_ENDPOINT, OTHER_ENDPOINT, an_agent, subagents_dir
from tests.unit.test_clearing import (
    COMPILED,
    WORKER,
    KeptLedger,
    _helpers,
    _spec,
    _subagent,
    bulky,  # noqa: F401 -- the fixture the every-graph walk is built on
)
from tests.unit.test_session_instructions import Sent

#: The test catalogue's default and where it is served.
HERE = {"endpoint": "fake", "address": FAKE_ENDPOINT.base_url, "model": "fake-model"}

#: How a reply records who served it, for each way a signer can differ from `HERE`.
ELSEWHERE = {"endpoint": "elsewhere", "address": OTHER_ENDPOINT.base_url, "model": "fake-model"}
OTHER_MODEL = {**HERE, "model": "cheap-model"}
MOVED = {**HERE, "address": OTHER_ENDPOINT.base_url}


def _reply(**metadata: Any) -> AIMessage:
    """An earlier answer that thought first, signed by whoever produced it."""
    return AIMessage(
        content=[
            {"type": "thinking", "thinking": "plan A", "signature": "SIGNED"},
            {"type": "text", "text": "first answer"},
        ],
        response_metadata={"model_provider": "anthropic", **metadata},
    )


def _only_thought(**metadata: Any) -> AIMessage:
    """An earlier answer that was thinking and nothing else, as a turn cut short in its
    thinking leaves one."""
    return AIMessage(
        content=[{"type": "thinking", "thinking": "plan A", "signature": "SIGNED"}],
        response_metadata={"model_provider": "anthropic", **metadata},
    )


def _kept_as_v0(**metadata: Any) -> AIMessage:
    """An earlier Responses answer that reasoned first, as `ChatOpenAI` keeps one under
    `output_version="v0"`: built from the API's own response rather than by hand, so the
    reasoning is wherever langchain puts it."""
    response = Response.model_validate({
        "id": "resp_1", "created_at": 0, "model": "fake-model", "object": "response",
        "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
        "output": [
            {"id": "rs_SIGNED", "type": "reasoning", "summary": [], "encrypted_content": "SIGNED"},
            {"id": "msg_1", "type": "message", "role": "assistant", "status": "completed",
             "content": [{"type": "output_text", "text": "first answer", "annotations": []}]},
        ],
    })
    result = _construct_lc_result_from_responses_api(response, output_version="v0")
    reply = result.generations[0].message
    assert isinstance(reply, AIMessage)
    return reply.model_copy(
        update={"response_metadata": {**reply.response_metadata, **metadata}}
    )


def _kept_as_v1(**metadata: Any) -> AIMessage:
    """An earlier answer from an endpoint on Anthropic's format that thought and had some
    of it redacted, as langchain keeps one under `output_version="v1"`: built from the
    API's own message, as a model call converts it, so each block is wherever langchain
    puts it."""
    model = ChatAnthropic(model="fake-model", api_key="x", output_version="v1")
    message = Message.model_validate({
        "id": "msg_1", "type": "message", "role": "assistant", "model": "fake-model",
        "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
        "content": [
            {"type": "thinking", "thinking": "plan A", "signature": "SIGNED"},
            {"type": "redacted_thinking", "data": "REDACTED"},
            {"type": "text", "text": "first answer"},
        ],
    })
    reply = model._format_output(message).generations[0].message
    reply = _update_message_content_to_blocks(reply, "v1")
    assert isinstance(reply, AIMessage)
    return reply.model_copy(
        update={"response_metadata": {**reply.response_metadata, **metadata}}
    )


def _turn(cfg, session_dir, way: str, earlier: AIMessage) -> tuple[dict[str, Any], dict]:
    """What a turn on the catalogue's default sends with `earlier` behind it, and the state
    it leaves."""
    model = Sent(model="fake-model", api_key="x", script=[AIMessage("done")])
    built = build_agent(cfg, backend=backend_at(cfg, session_dir), model=model)
    asked = {"messages": [HumanMessage("first"), earlier, HumanMessage("second")]}
    if way == "async":
        state = asyncio.run(built.graph.ainvoke(asked, context=built.context))
    else:
        state = built.graph.invoke(asked, context=built.context)
    return model.sent[0], state


def _sent_back(payload: dict[str, Any]) -> list[str]:
    """The block types of the earlier answer, as the request sends it."""
    (earlier,) = [m for m in payload["messages"] if m["role"] == "assistant"]
    content = earlier["content"]
    return ["text"] if isinstance(content, str) else [block["type"] for block in content]


@pytest.mark.parametrize(
    "earlier",
    [
        _reply(model_name="MiniMax-M3"),
        _reply(**{SERVED_BY: ELSEWHERE}),
        _reply(**{SERVED_BY: OTHER_MODEL}),
        _reply(**{SERVED_BY: MOVED}),
    ],
    ids=["unrecorded-other-model", "other-endpoint", "same-endpoint-other-model", "moved"],
)
def test_thinking_another_signer_made_is_not_sent(cfg, session_dir, way, earlier):
    """A MiniMax reply's thinking reached Claude signed by MiniMax, which Claude cannot
    check: langchain tells vendors apart by package, and both come through `ChatAnthropic`.
    So does one from another endpoint, another model on the same one, or a gateway moved
    under the same name."""
    payload, state = _turn(cfg, session_dir, way, earlier)

    assert _sent_back(payload) == ["text"]
    assert "SIGNED" not in json.dumps(payload)
    # The conversation keeps it, for the model that can read it.
    assert [block["type"] for block in state["messages"][1].content] == ["thinking", "text"]


@pytest.mark.parametrize(
    "earlier",
    [_reply(**{SERVED_BY: HERE}), _reply(model_name="fake-model")],
    ids=["recorded", "unrecorded-same-model"],
)
def test_the_signers_own_thinking_goes_back_unchanged(cfg, session_dir, way, earlier):
    """The control: thinking the same endpoint and model produced is the reasoning the
    model keeps between calls, and a reply from before replies were recorded is judged by
    the model the server named."""
    payload, _ = _turn(cfg, session_dir, way, earlier)

    assert _sent_back(payload) == ["thinking", "text"]
    assert "SIGNED" in json.dumps(payload)


def test_each_reply_records_who_served_it_and_no_request_says_so(cfg, session_dir, way):
    """A reply that did not say who answered it could only be judged by the name a server
    reports, which a gateway moved under the same name keeps."""
    payload, state = _turn(cfg, session_dir, way, _reply(**{SERVED_BY: HERE}))

    assert state["messages"][-1].response_metadata[SERVED_BY] == HERE
    assert SERVED_BY not in json.dumps(payload)


@pytest.mark.parametrize(
    "model",
    [
        ChatAnthropic(model="claude-sonnet-4-5", api_key="x"),
        ChatOpenAI(model="gpt-5", api_key="x", use_responses_api=True),
    ],
    ids=["anthropic", "openai_responses"],
)
def test_neither_chat_class_sends_the_record(model):
    """The record is kingfisher's: a chat class that put it on the wire would hand the
    endpoint's address to whichever endpoint came next."""
    earlier = AIMessage(content="first answer", response_metadata={SERVED_BY: HERE})

    payload = model._get_request_payload([HumanMessage("first"), earlier, HumanMessage("2")])

    assert SERVED_BY not in json.dumps(payload, default=str)


@pytest.mark.parametrize(
    ("model", "sent_as"),
    [
        (ChatAnthropic(model="claude-sonnet-4-5", api_key="x"), "claude-sonnet-4-5"),
        (ChatOpenAI(model="gpt-5", api_key="x", use_responses_api=True), "gpt-5"),
    ],
    ids=["anthropic", "openai_responses"],
)
def test_each_wire_formats_model_is_known_by_the_id_it_sends(model, sent_as):
    """The two chat classes keep the id under different names; one read under the wrong
    name is no model at all, and every reply it made would lose its thinking."""
    assert model_id(model) == sent_as


@pytest.mark.parametrize(
    ("served", "sent"), [(ELSEWHERE, False), (HERE, True)], ids=["another-signer", "own"]
)
def test_a_v0_replys_reasoning_goes_back_only_to_its_signer(served, sent):
    """A Responses reply kept as `v0` holds its reasoning in `additional_kwargs`, which
    `ChatOpenAI` sends back as an input item, so a strip that read only the content sent
    another endpoint's reasoning on. The signer's own going back is the control."""
    model = ChatOpenAI(model="fake-model", api_key="x", use_responses_api=True)
    earlier = _kept_as_v0(**{SERVED_BY: served})
    assert "reasoning" in earlier.additional_kwargs, "and not in the content: the shape"
    request = ModelRequest(
        model=model, messages=[HumanMessage("first"), earlier, HumanMessage("second")]
    )

    payload = json.dumps(model._get_request_payload(kept_for(request, HERE).messages))

    assert ("rs_SIGNED" in payload) is sent
    assert "first answer" in payload
    assert earlier.additional_kwargs["reasoning"]["id"] == "rs_SIGNED", "the conversation keeps it"


@pytest.mark.parametrize(
    "model",
    [ChatAnthropic(model="fake-model", api_key="x"), ChatOpenAI(model="fake-model", api_key="x")],
    ids=["anthropic", "openai_chat"],
)
@pytest.mark.parametrize(
    ("served", "sent"), [(ELSEWHERE, False), (HERE, True)], ids=["another-signer", "own"]
)
def test_a_v1_replys_redacted_thinking_goes_back_only_to_its_signer(model, served, sent):
    """Under `v1` redacted thinking is wrapped as a `non_standard` block, which a strip
    reading only the outer type left in, and both chat classes send the wrapped block on as
    it is: MiniMax's reached Claude. The signer's own going back is the control."""
    earlier = _kept_as_v1(**{SERVED_BY: served})
    wrapped = earlier.content[1]
    assert isinstance(wrapped, dict) and wrapped["type"] == "non_standard", "the shape"
    request = ModelRequest(
        model=model, messages=[HumanMessage("first"), earlier, HumanMessage("second")]
    )

    payload = json.dumps(model._get_request_payload(kept_for(request, HERE).messages))

    assert ("REDACTED" in payload) is sent
    if not sent:
        assert "SIGNED" not in payload
    assert "first answer" in payload
    assert wrapped["value"]["data"] == "REDACTED", "the conversation keeps it"


def test_a_reply_that_was_only_another_signers_thinking_is_left_out(cfg, session_dir, way):
    """Stripped to nothing, the reply went to `ChatAnthropic` empty, which skips it only
    after joining its neighbours, so two user turns went out in a row. Left out first, the
    two are one; the conversation keeps the reply."""
    payload, state = _turn(cfg, session_dir, way, _only_thought(**{SERVED_BY: ELSEWHERE}))

    assert [message["role"] for message in payload["messages"]] == ["user"]
    assert "SIGNED" not in json.dumps(payload)
    assert [block["type"] for block in state["messages"][1].content] == ["thinking"]


@pytest.mark.parametrize(
    "beside",
    [{"type": "text", "text": ""}, {"type": "text", "text": " \n"}, ""],
    ids=["empty-text", "blank-text", "empty-string"],
)
def test_a_reply_stripped_to_blank_text_is_left_out_too(cfg, session_dir, way, beside):
    """A reply kept for the blank text beside its thinking went to `ChatAnthropic`, which
    drops blank text and then skips the reply, so two user turns went out in a row as if
    nothing had been stripped."""
    earlier = _only_thought(**{SERVED_BY: ELSEWHERE})
    earlier = earlier.model_copy(update={"content": [*earlier.content, beside]})

    payload, state = _turn(cfg, session_dir, way, earlier)

    assert [message["role"] for message in payload["messages"]] == ["user"]
    assert "SIGNED" not in json.dumps(payload)
    assert state["messages"][1].content[-1] == beside, "the conversation keeps the reply"


def test_a_reply_stripped_to_nothing_is_no_prefill():
    """Last in the request, an emptied reply is a prefill, which the current Claude models
    refuse with a 400."""
    model = ChatAnthropic(model="fake-model", api_key="x")
    request = ModelRequest(
        model=model, messages=[HumanMessage("first"), _only_thought(**{SERVED_BY: ELSEWHERE})]
    )

    payload = model._get_request_payload(kept_for(request, HERE).messages)

    assert [message["role"] for message in payload["messages"]] == ["user"]


def test_a_reply_stripped_to_its_tool_calls_still_goes():
    """A reply that called a tool is not empty without its thinking: left out, the call's
    result would answer nothing. Nor is a result that said nothing left out beside it."""
    model = ChatAnthropic(model="fake-model", api_key="x")
    called = _only_thought(**{SERVED_BY: ELSEWHERE}).model_copy(
        update={"tool_calls": [{"name": "ls", "args": {}, "id": "toolu_1", "type": "tool_call"}]}
    )
    request = ModelRequest(
        model=model,
        messages=[HumanMessage("first"), called, ToolMessage("", tool_call_id="toolu_1")],
    )

    payload = model._get_request_payload(kept_for(request, HERE).messages)

    assert [message["role"] for message in payload["messages"]] == ["user", "assistant", "user"]
    assert [block["type"] for block in payload["messages"][1]["content"]] == ["tool_use"]
    assert payload["messages"][2]["content"][0]["type"] == "tool_result"
    assert "SIGNED" not in json.dumps(payload)


def test_a_reply_naming_no_model_is_not_the_signer_of_a_model_naming_none():
    """An unrecorded reply the server named no model for, sent to a model built outside
    the catalogue that reports no id either, matched it as `None == None`, and its thinking
    went on to a model nothing showed had signed it."""
    nowhere = {"endpoint": None, "address": None, "model": None}
    request = ModelRequest(
        model=ChatAnthropic(model="fake-model", api_key="x"),
        messages=[HumanMessage("first"), _reply(), HumanMessage("second")],
    )

    kept = kept_for(request, nowhere)

    assert [block["type"] for block in kept.messages[1].content] == ["text"]


def test_a_request_with_nothing_to_take_out_is_sent_as_it_came():
    """Rebuilt for nothing, the request would differ from what every middleware inside it
    expects to have been handed."""
    request = ModelRequest(
        model=ChatAnthropic(model="fake-model", api_key="x"),
        messages=[HumanMessage("first"), AIMessage("plain"), _reply(**{SERVED_BY: HERE})],
    )

    assert kept_for(request, HERE) is request


def test_every_graph_kingfisher_builds_for_a_turn_carries_it(bulky):  # noqa: F811
    """A graph without it would send another signer's thinking on: the agent, a declared
    subagent, a helper it consults and `general-purpose` alike. After clearing, which
    measures what the summariser counted; outside the step budget, so the reply recorded
    is the one kept; outside the caching slot, which marks what it sends."""
    _subagent(bulky, "worker", WORKER + "subagents: [inner]\n")
    _subagent(bulky, "inner", WORKER)
    (subagents_dir(bulky) / "built.py").write_text(COMPILED, encoding="utf-8")
    an_agent(bulky, subagents="[worker, inner, built]", **EVERY_TOOL)
    backend = backend_at(bulky, bulky.workspace / "sessions" / "s")
    kept = build_agent(
        bulky, backend=backend, ledger=Ledger("t", KeptLedger()), agent=_spec(bulky, "only")
    )

    graphs = {spec["name"]: spec for spec in kept.subagents or ()}
    stacks = {
        "the agent": kept.middleware,
        **{name: graphs[name]["middleware"] for name in ("worker", "inner", "general-purpose")},
        "worker's helper": _helpers(graphs["worker"])["inner"]["middleware"],
    }
    for name, middleware in stacks.items():
        at = {type(one): i for i, one in enumerate(middleware)}
        assert [type(one) for one in middleware].count(SignedThinkingMiddleware) == 1, name
        assert at[ResultClearingMiddleware] < at[SignedThinkingMiddleware], name
        assert at[SignedThinkingMiddleware] < at[StepBudgetMiddleware], name
        if PromptCachingMiddleware in at:
            assert at[SignedThinkingMiddleware] < at[PromptCachingMiddleware], name
    assert "middleware" not in graphs["built"], "a compiled subagent is handed none"
