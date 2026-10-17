"""A reply the model stopped at its output cap: the call it was cut inside goes unrun, and
an agent cut off three times in a row stops.

Measured on MiniMax-M3: a `write_file` cut at the cap reached the tool as a valid call with
its `content` stopping mid-word, and saved. The rule -- which reply is cut, which call in it
-- is driven on replies built here, one per wire format. What it does is driven through
real turns, because the refusal, the stop and the report each happen somewhere else.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from kingfisher import Kingfisher, Request
from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.agent.middlewares.cut_replies import CutReplies, cut_call
from kingfisher.core.definitions.agents.catalogue import LocalAgentRepository
from kingfisher.core.layout import HARNESS, TRANSCRIPT_FILE
from kingfisher.core.session.runtime import cut_off, output_limit
from kingfisher.core.session.transcript import from_json
from kingfisher.plugins import LocalSessions
from kingfisher.plugins.sessions.backend import backend_at
from tests.conftest import RecordedEvents, an_agent, start, subagents_dir
from tests.unit.scripted import Scripted

#: The output reservation each cut reply here spent, which is what it reached.
CAP = 32_768

#: How each wire format says a reply stopped at the cap.
STOPPED = {
    "anthropic": {"stop_reason": "max_tokens"},
    "responses": {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}},
}

REFUSED = "Not run: your reply reached the 32,768-token output limit while this call"


def write(at: int, path: str = "", content: str = "half a fi") -> dict[str, Any]:
    return {
        "name": "write_file",
        "args": {"file_path": path or f"/outputs/f{at}.md", "content": content},
        "id": f"c{at}",
    }


def reply(
    *calls: dict[str, Any], text: str = "", wire: str = "anthropic", cut: bool = True
) -> AIMessage:
    """A reply as its wire format hands it over: the Anthropic format puts each call in
    the content as a block, the Responses API keeps them out of it."""
    blocks: list[Any] = [{"type": "text", "text": text}] if text else []
    if wire == "anthropic":
        blocks += [
            {"type": "tool_use", "id": c["id"], "name": c["name"], "input": {}} for c in calls
        ]
    meta = STOPPED[wire] if cut else {"stop_reason": "end_turn"}
    spent = CAP if cut else 40
    return AIMessage(
        content=blocks or text,
        tool_calls=list(calls),
        response_metadata=meta,
        usage_metadata={"input_tokens": 10, "output_tokens": spent, "total_tokens": spent + 10},
    )


# -- the rule ------------------------------------------------------------------------


@pytest.mark.parametrize("wire", sorted(STOPPED))
def test_each_wire_format_says_cut_off_its_own_way(wire):
    """A check reading one format's words would leave the other's cut files saving."""
    assert cut_off(reply(write(1), wire=wire))
    assert not cut_off(reply(write(1), wire=wire, cut=False))


def test_a_reply_incomplete_for_another_reason_was_not_cut_at_the_cap():
    """The Responses API marks a reply incomplete for a content filter as well."""
    filtered = {"status": "incomplete", "incomplete_details": {"reason": "content_filter"}}
    assert not cut_off(AIMessage(content="", response_metadata=filtered))
    assert not cut_off(AIMessage(content="", response_metadata={"status": "completed"}))


@pytest.mark.parametrize("wire", sorted(STOPPED))
def test_only_the_last_call_of_a_cut_reply_is_the_cut_one(wire):
    """The cap stops a reply at its end, so every call before the last is whole."""
    assert cut_call(reply(write(1), write(2), wire=wire)) == "c2"
    assert cut_call(reply(write(1), write(2), wire=wire, cut=False)) is None
    assert cut_call(reply(text="half an answ", wire=wire)) is None


def test_a_reply_cut_writing_text_after_its_calls_cut_none_of_them():
    """Where the content says something came after the last call, the cut was there."""
    after = reply(write(1))
    after.content = [*after.content, {"type": "text", "text": "Next I will"}]

    assert cut_call(after) is None


def test_the_limit_is_named_from_what_the_reply_spent():
    """At the cap, a reply's output count is the cap, whichever format it came in."""
    assert output_limit(reply(write(1))) == "the 32,768-token output limit"
    assert output_limit(AIMessage(content="", response_metadata=STOPPED["anthropic"])) == (
        "the output limit"
    )


# -- through real turns --------------------------------------------------------------


@pytest.fixture
def writing(scripted):
    an_agent(scripted)
    start(scripted, "s")
    return scripted


def _turn(cfg: Any, *script: AIMessage, way: str = "sync", events: Any = None) -> Any:
    """One turn, through `run` or `arun`: the two loops call different hooks."""
    Scripted.script.extend(script)
    service = Kingfisher(cfg, sessions=LocalSessions(), run_events=events)
    request = Request("go", agent="only", session_id="s")
    return asyncio.run(service.arun(request)) if way == "async" else service.run(request)


def _written(cfg: Any) -> set[str]:
    folder = cfg.workspace / "sessions" / "s" / "outputs"
    return {one.name for one in folder.iterdir()} if folder.is_dir() else set()


def _said(cfg: Any) -> dict[str, str]:
    """Each tool result the turn's transcript keeps, by call id."""
    session = cfg.workspace / "sessions" / "s"
    kept = from_json((session / HARNESS / TRANSCRIPT_FILE).read_text(encoding="utf-8"))
    return {one.call_id: one.content for one in kept if one.role == "tool"}


@pytest.mark.parametrize("wire", sorted(STOPPED))
def test_a_cut_write_is_not_run_and_the_agent_is_told_why(writing, way, wire):
    """The bug: the shortened file saved and the tool said it had."""
    result = _turn(writing, reply(write(1), wire=wire), AIMessage("done"), way=way)

    assert _written(writing) == set()
    assert _said(writing)["c1"].startswith(REFUSED)
    assert result.answer == "done"
    assert result.stop_reason == "end_turn", "one cut reply the agent got past ends nothing"


def test_the_whole_calls_before_the_cut_one_run(writing):
    """Refusing them would have the agent redo work that was fine."""
    _turn(writing, reply(write(1), write(2)), AIMessage("done"))

    assert _written(writing) == {"f1.md"}
    assert not _said(writing)["c1"].startswith("Not run")
    assert _said(writing)["c2"].startswith(REFUSED)


def test_three_cut_replies_in_a_row_stop_the_turn_and_run_nothing_of_the_third(writing, way):
    """Each cut reply spends the whole reservation; one session retried a whole-file write
    a dozen times before writing in pieces."""
    result = _turn(
        writing,
        reply(write(1)),
        reply(write(2)),
        reply(write(3), write(4), text="Writing it now."),
        way=way,
    )

    assert result.stop_reason == "max_tokens"
    assert not result.completed
    assert result.answer == "Writing it now.", "the agent's own answer carries no subagent's line"
    assert _written(writing) == set(), "the third reply's whole call ran too"
    assert set(_said(writing)) == {"c1", "c2"}, "the third reply's calls stayed in the transcript"


def test_a_reply_that_was_not_cut_starts_the_count_again(writing):
    """In a row, so an agent writing a long report in parts can hit the cap now and then."""
    result = _turn(
        writing,
        reply(write(1)),
        reply(write(2)),
        reply(write(3), cut=False),
        reply(write(4)),
        reply(write(5)),
        AIMessage("done"),
    )

    assert result.stop_reason == "end_turn"
    assert _written(writing) == {"f3.md"}


def test_a_turn_that_ends_on_a_cut_answer_says_it_stopped_there(writing):
    """A final answer cut mid-sentence was reported as complete, and the command said so."""
    result = _turn(writing, reply(text="The three findings are: first, the"))

    assert result.stop_reason == "max_tokens"
    assert result.answer == "The three findings are: first, the"


def test_a_subagent_that_gives_up_hands_back_its_cut_answer_marked_and_the_turn_goes_on(
    writing,
):
    """Its text is all its caller is handed, so a cut one read as finished there; and
    ending the turn for it would throw away what the rest of the turn did."""
    subagents_dir(writing).mkdir(parents=True, exist_ok=True)
    (subagents_dir(writing) / "helper.yaml").write_text(
        "name: helper\ndescription: Helps.\nsystem_prompt: |\n  You help.\n", encoding="utf-8"
    )
    an_agent(writing, subagents="[helper]")
    asked = {"description": "go", "subagent_type": "helper"}
    to_helper = AIMessage(content="", tool_calls=[{"name": "task", "args": asked, "id": "t1"}])

    result = _turn(
        writing,
        to_helper,
        reply(write(1)),
        reply(write(2)),
        reply(write(3), text="The report so far"),
        AIMessage("done"),
    )

    assert _said(writing)["t1"].startswith(
        "[Incomplete: this subagent's replies reached the 32,768-token output limit 3 times "
        "in a row, and the answer below is cut off.]\n\nThe report so far"
    )
    assert result.stop_reason == "end_turn"
    assert result.answer == "done"


def test_each_cut_is_recorded_on_its_model_call(writing):
    """Got past, a cut leaves no other trace: the turn ends as any other does."""
    sink = RecordedEvents()

    _turn(writing, reply(write(1)), reply(text="done", cut=False), events=sink)

    assert [one["cut_off"] for one in sink.named("model_call")] == [True, False]


def test_the_stream_says_which_reply_was_cut_and_why_the_turn_stopped(writing):
    """Someone watching a turn sees each cut as it happens, and the stop at the end."""
    Scripted.script.extend([reply(write(1)), reply(write(2)), reply(write(3))])
    service = Kingfisher(writing, sessions=LocalSessions())

    events = list(service.stream(Request("go", agent="only", session_id="s")))

    calls = [one for one in events if one.kind == "model_call"]
    assert [one.cut_off for one in calls] == [True, True, True]
    assert str(calls[0]).endswith("(cut off at the output limit)")
    (stopped,) = [one for one in events if one.kind == "cut_short"]
    assert stopped.text == (
        "turn stopped: the agent's reply reached the 32,768-token output limit "
        "(max_tokens in models.yaml)"
    )
    assert events[-1].result is not None
    assert events[-1].result.stop_reason == "max_tokens"


def _spec(cfg: Any, name: str) -> Any:
    return LocalAgentRepository(cfg.catalogue_roots["agents"]).specs[name]


def test_every_graph_kingfisher_builds_has_it_and_only_a_subagent_marks_its_answer(writing):
    """The agent, a subagent and deepagents' `general-purpose`, which is handed the agent's
    own tools: the three graphs whose middleware kingfisher composes."""
    subagents_dir(writing).mkdir(parents=True, exist_ok=True)
    (subagents_dir(writing) / "helper.yaml").write_text(
        "name: helper\ndescription: Helps.\nsystem_prompt: |\n  You help.\n", encoding="utf-8"
    )
    an_agent(writing, subagents="[helper]")
    backend = backend_at(writing, writing.workspace / "sessions" / "s")

    built = build_agent(writing, backend=backend, agent=_spec(writing, "only"))

    def marks(middleware: Any) -> list[bool]:
        return [one.subagent for one in middleware if isinstance(one, CutReplies)]

    assert marks(built.middleware) == [False]
    specs = {spec["name"]: spec for spec in built.subagents or ()}
    assert set(specs) >= {"helper", "general-purpose"}
    for name, spec in specs.items():
        assert marks(spec.get("middleware", ())) == [True], name


def test_it_adds_no_step_to_the_agents_loop(writing):
    """langgraph's recursion limit counts graph steps, and a `before_model` or
    `after_model` hook is one: measured, one cut a turn's 50 model calls to 37."""
    backend = backend_at(writing, writing.workspace / "sessions" / "s")

    nodes = build_agent(writing, backend=backend).graph.get_graph().nodes

    # The witness that a hook is a node named for its class, without which a check for
    # this class's name would pass whatever the class held.
    assert "TodoListMiddleware.after_model" in nodes
    assert not [one for one in nodes if CutReplies.__name__ in one]
