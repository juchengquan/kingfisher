"""A reply's thinking goes back only to the endpoint and model that produced it."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from langchain.agents.middleware import ModelRequest
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI

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
