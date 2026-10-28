"""A reply the model stopped at its output cap: the call it was cut inside goes unrun, a cut
answer is asked for again, shorter, and an agent cut off too often in a row stops.

Measured on MiniMax-M3: a `write_file` cut at the cap reached the tool as a valid call with
its `content` stopping mid-word, and saved. The rule -- which reply is cut, which call in it
-- is driven on replies built here, one per wire format. What it does is driven through
real turns, because the refusal, the stop and the report each happen somewhere else.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from kingfisher import Kingfisher, Request
from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.agent.middlewares.model_reply import ModelReplyMiddleware, cut_call
from kingfisher.core.agent.middlewares.step_budget import warning
from kingfisher.core.definitions.agents.catalogue import LocalAgentRepository
from kingfisher.core.layout import HARNESS, TRANSCRIPT_FILE
from kingfisher.core.session.runtime import cut_off, with_call_args
from kingfisher.core.session.transcript import from_json
from kingfisher.plugins import LocalSessions
from kingfisher.plugins.sessions.backend import backend_at
from tests.conftest import EVERY_TOOL, RecordedEvents, an_agent, start, subagents_dir
from tests.unit.scripted import Scripted
from tests.unit.test_turn_instructions import Sent

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
    """A reply as its wire format hands it over, each call in the content as a block of
    that format's own: the Responses API's as langchain-openai builds one from a real
    incomplete response, its text and calls in the order they were written."""
    blocks: list[Any] = [{"type": "text", "text": text}] if text else []
    if wire == "anthropic":
        blocks += [
            {
                "type": "tool_use",
                "id": c["id"],
                "name": c["name"],
                "input": {},
                "partial_json": json.dumps(c["args"]),
            }
            for c in calls
        ]
    else:
        blocks += [
            {
                "type": "function_call",
                "call_id": c["id"],
                "name": c["name"],
                "arguments": json.dumps(c["args"]),
                "id": f"fc_{c['id']}",
                "status": "incomplete",
            }
            for c in calls
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


@pytest.mark.parametrize("wire", sorted(STOPPED))
def test_a_reply_cut_writing_text_after_its_calls_cut_none_of_them(wire):
    """Where the content says something came after the last call, the cut was there."""
    after = reply(write(1), wire=wire)
    after.content = [*after.content, {"type": "text", "text": "Next I will"}]

    assert cut_call(after) is None


def test_where_a_provider_puts_no_calls_in_the_content_the_last_call_is_refused():
    """Nothing there says what came last, and running a cut call saves a shortened file."""
    bare = reply(write(1), write(2))
    bare.content = ""

    assert cut_call(bare) == "c2"


def test_new_arguments_reach_every_copy_of_a_call_in_each_block_shape():
    """A copy left holding the old text is still sent, or counted, in full."""
    args = {"file_path": "/outputs/f1.md", "content": "y" * 900}
    shapes = [
        {"type": "tool_use", "id": "c1", "name": "write_file", "input": {},
         "partial_json": json.dumps(args)},
        {"type": "function_call", "call_id": "c1", "name": "write_file",
         "arguments": json.dumps(args), "id": "fc_c1"},
        {"type": "tool_call", "id": "c1", "name": "write_file", "args": args},
    ]
    for block in shapes:
        call = {"name": "write_file", "args": args, "id": "c1"}
        message = AIMessage(content=[block], tool_calls=[call])

        changed = with_call_args(message, "c1", {"content": "short"})

        assert changed.tool_calls[0]["args"]["content"] == "short"
        assert "y" * 900 not in json.dumps(changed.content), block["type"]
        assert "short" in json.dumps(changed.content), block["type"]


# -- through real turns --------------------------------------------------------------


@pytest.fixture
def writing(scripted):
    an_agent(scripted, **EVERY_TOOL)
    start(scripted, "s")
    return scripted


def _turn(cfg: Any, *script: AIMessage, events: Any = None) -> Any:
    """One turn, through `run`: the hooks `arun` calls instead are copies, which
    `test_async_twins.py` holds."""
    Scripted.script.extend(script)
    service = Kingfisher(cfg, sessions=LocalSessions(), run_events=events)
    request = Request("go", agent="only", session_id="s")
    return service.run(request)


def _written(cfg: Any) -> set[str]:
    folder = cfg.workspace / "sessions" / "s" / "outputs"
    return {one.name for one in folder.iterdir()} if folder.is_dir() else set()


def _kept(cfg: Any) -> list[Any]:
    """What the turn's transcript keeps, which is what every later turn sends again."""
    session = cfg.workspace / "sessions" / "s"
    return list(from_json((session / HARNESS / TRANSCRIPT_FILE).read_text(encoding="utf-8")))


def _said(cfg: Any) -> dict[str, str]:
    """Each tool result the turn's transcript keeps, by call id."""
    return {one.call_id: one.content for one in _kept(cfg) if one.role == "tool"}


ASKED = (
    "Your answer reached the 32,768-token output limit and was cut off. Answer again, "
    "shorter. If it needs the length, first write the long part to a file under /outputs, "
    "then answer with a summary and the file's path. Name a file only after you have "
    "written it."
)


@pytest.mark.parametrize("wire", sorted(STOPPED))
def test_a_cut_write_is_not_run_and_the_agent_is_told_why(writing, wire):
    """The bug: the shortened file saved and the tool said it had."""
    result = _turn(writing, reply(write(1), wire=wire), AIMessage("done"))

    assert _written(writing) == set()
    assert _said(writing)["c1"].startswith(REFUSED)
    assert result.answer == "done"
    assert result.stop_reason == "end_turn", "one cut reply the agent got past ends nothing"


@pytest.mark.parametrize("wire", sorted(STOPPED))
def test_a_refused_call_keeps_its_path_and_drops_the_text_it_was_cut_writing(writing, wire):
    """Clearing never shortens a failed call, so the cut text, up to a whole reservation,
    was sent again on every later call of every later turn."""
    _turn(writing, reply(write(1, content="y" * 2_000), wire=wire), AIMessage("done"))

    shortened = {"file_path": "/outputs/f1.md", "content": "[2,000 characters, cut off; not run]"}
    (kept,) = [one for one in _kept(writing) if one.tool_calls]
    assert kept.tool_calls[0].args == shortened
    (sent,) = [one for one in Scripted.heard[1] if isinstance(one, AIMessage)]
    assert sent.tool_calls[0]["args"] == shortened
    assert "y" * 600 not in json.dumps(sent.content), "the copy in the content still holds it"


def test_a_whole_call_before_one_too_broken_to_parse_runs(writing):
    """Taking the last call that parsed for the cut one refused a whole write."""
    broken = reply(write(1), write(2))
    broken.tool_calls = broken.tool_calls[:1]
    broken.invalid_tool_calls = [
        {"type": "invalid_tool_call", "name": "write_file", "args": '{"file_pa', "id": "c2",
         "error": "unparsable"}
    ]

    _turn(writing, broken, AIMessage("done"))

    assert _written(writing) == {"f1.md"}


def test_the_whole_calls_before_the_cut_one_run(writing):
    """Refusing them would have the agent redo work that was fine."""
    _turn(writing, reply(write(1), write(2)), AIMessage("done"))

    assert _written(writing) == {"f1.md"}
    assert not _said(writing)["c1"].startswith("Not run")
    assert _said(writing)["c2"].startswith(REFUSED)


def test_three_cut_replies_in_a_row_stop_the_turn_and_run_nothing_of_the_third(writing):
    """Each cut reply spends the whole reservation; one session retried a whole-file write
    a dozen times before writing in pieces."""
    result = _turn(
        writing,
        reply(write(1)),
        reply(write(2)),
        reply(write(3), write(4), text="Writing it now."),
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


def test_a_cut_answer_is_asked_for_again_shorter_and_kept_out_of_the_conversation(
    writing
):
    """A final answer cut mid-sentence was reported as complete, and its text kept would be
    sent again on every later call, since clearing never touches the agent's own text."""
    shorter = "Three findings; the full list is in /outputs/findings.md."
    cut = reply(text="The three findings are: first, the")

    result = _turn(writing, cut, AIMessage(shorter))

    assert result.stop_reason == "end_turn"
    assert result.answer == shorter
    asked = Scripted.heard[1]
    assert asked[-2].content == "The three findings are: first, the"
    assert asked[-1].content == ASKED
    kept = " ".join(one.content for one in _kept(writing))
    assert "first, the" not in kept
    assert "Answer again" not in kept


def test_three_cut_answers_in_a_row_end_the_turn_on_the_last(writing):
    """The command said a turn whose answer was cut off had finished."""
    result = _turn(writing, reply(text="First try"), reply(text="Second try"), reply(text="Third"))

    assert result.stop_reason == "max_tokens"
    assert result.answer == "Third"
    assert [one.content for one in _kept(writing) if one.role == "assistant"] == ["Third"]
    # Each try is up to a whole reservation long, so the model is shown its latest only.
    shown = " ".join(str(one.content) for one in Scripted.heard[2])
    assert "Second try" in shown
    assert "First try" not in shown


def test_a_cut_answer_asked_again_still_counts_toward_three(writing):
    """It never reaches the conversation, where the count is read; uncounted, an agent
    alternating cut answers and cut writes would go well past three."""
    script = (reply(text="Here is the"), reply(write(2)), reply(write(3)), AIMessage("x"))

    result = _turn(writing, *script)

    assert result.stop_reason == "max_tokens"
    assert len(Scripted.script) == 1, "a fourth cut reply was asked for"
    assert set(_said(writing)) == {"c2"}


def test_cut_answers_asked_again_in_the_same_call_can_stop_the_reply_after_them(writing):
    """Counted where the conversation would have held them, so the third cut stops the
    agent even when it is the first the conversation sees."""
    result = _turn(writing, reply(text="A"), reply(text="B"), reply(write(3)), AIMessage("x"))

    assert result.stop_reason == "max_tokens"
    assert _said(writing) == {}, "the third cut reply's call was run or refused"
    assert len(Scripted.script) == 1


def test_a_cut_call_too_broken_to_read_is_asked_again_like_a_cut_answer(writing):
    """Its call is not sent back, since one with no result beside it is refused by the
    provider and nothing would run it; nor is the empty answer left of it, which
    `ChatAnthropic` skips, sending the task and the note as two user turns."""
    broken = AIMessage(
        content=[{"type": "tool_use", "id": "c1", "name": "write_file", "input": {}}],
        invalid_tool_calls=[
            {
                "type": "invalid_tool_call",
                "name": "write_file",
                "args": '{"file_pa',
                "id": "c1",
                "error": "unparsable",
            }
        ],
        response_metadata=STOPPED["anthropic"],
        usage_metadata={"input_tokens": 10, "output_tokens": CAP, "total_tokens": CAP + 10},
    )

    result = _turn(writing, broken, AIMessage("done"))

    assert result.answer == "done"
    asked = Scripted.heard[1]
    assert not any(isinstance(one, AIMessage) for one in asked)
    assert asked[-2].type == "human", "the task, and the note straight after it"
    assert asked[-1].content == ASKED


#: The note for a reply that wrote nothing before the cap: its thinking took the reservation.
ASKED_THINKING = (
    "Your reply reached the 32,768-token output limit before you wrote any answer: your "
    "thinking took all of it. Think less, and answer directly. If it needs the length, first "
    "write the long part to a file under /outputs, then answer with a summary and the file's "
    "path. Name a file only after you have written it."
)


@pytest.mark.parametrize(
    "beside", [[], [{"type": "text", "text": " "}]], ids=["only-thinking", "and-blank-text"]
)
def test_a_reply_cut_while_thinking_is_asked_again_as_one_user_turn(cfg, session_dir, beside):
    """Its empty answer went back to `ChatAnthropic`, which skipped it after joining its
    neighbours, so the task and the note went out as two user turns, and the note told the
    model an answer it never wrote was cut off and to make it shorter."""
    cut = AIMessage(
        content=[{"type": "thinking", "thinking": "a long plan", "signature": "SIG"}, *beside],
        response_metadata={**STOPPED["anthropic"], "model_provider": "anthropic"},
        usage_metadata={"input_tokens": 10, "output_tokens": CAP, "total_tokens": CAP + 10},
    )
    model = Sent(model="claude-sonnet-4-5", api_key="x", script=[cut, AIMessage("done")])
    built = build_agent(cfg, backend=backend_at(cfg, session_dir), model=model)
    asked: dict[str, Any] = {"messages": [{"role": "user", "content": "summarise the report"}]}

    built.graph.invoke(asked, context=built.context)

    (again,) = model.sent[1]["messages"]
    assert again["role"] == "user"
    told = [block.get("text") for block in again["content"] if isinstance(block, dict)]
    assert told[-1] == ASKED_THINKING
    assert "SIG" not in json.dumps(model.sent[1]["messages"])


def test_the_file_a_cut_answer_is_pointed_at_is_under_this_deployments_outputs(writing):
    """A deployment may name the folder, and the agent told another would write where
    nothing is returned."""
    cfg = replace(writing, layout=replace(writing.layout, outputs="deliverables"))
    start(cfg, "s")

    _turn(cfg, reply(text="The three findings are"), AIMessage("done"))

    told = Scripted.heard[1][-1].content
    assert "to a file under /deliverables, then answer" in told


def test_a_subagent_that_gives_up_hands_back_its_cut_answer_marked_and_the_turn_goes_on(
    writing,
):
    """Its text is all its caller is handed, so a cut one read as finished there; and
    ending the turn for it would throw away what the rest of the turn did."""
    subagents_dir(writing).mkdir(parents=True, exist_ok=True)
    (subagents_dir(writing) / "helper.yaml").write_text(
        "name: helper\ndescription: Helps.\n"
        'builtin_tools: ["*"]\ntools: ["*"]\n'
        "system_prompt: |\n  You help.\n",
        encoding="utf-8",
    )
    an_agent(writing, subagents="[helper]", **EVERY_TOOL)
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


def test_a_subagent_told_its_budget_is_stopped_by_its_third_cut_reply_at_once(writing):
    """The bug: the step budget's note, put last in the request, ended the count of cut
    replies at none, so a subagent near its budget was asked twice more after its third and
    still handed back "3 times in a row"."""
    subagents_dir(writing).mkdir(parents=True, exist_ok=True)
    (subagents_dir(writing) / "helper.yaml").write_text(
        "name: helper\ndescription: Helps.\n"
        'builtin_tools: ["*"]\ntools: ["*"]\n'
        "system_prompt: |\n  You help.\n",
        encoding="utf-8",
    )
    an_agent(writing, subagents="[helper]", **EVERY_TOOL)
    # A budget of 10 model calls, which tells the subagent from its 9th.
    cfg = replace(writing, recursion_limit=30)
    asked = {"description": "go", "subagent_type": "helper"}
    to_helper = AIMessage(content="", tool_calls=[{"name": "task", "args": asked, "id": "t1"}])
    listings = [
        AIMessage(content="", tool_calls=[{"name": "ls", "args": {"path": "/"}, "id": f"l{n}"}])
        for n in range(6)
    ]

    _turn(
        cfg,
        to_helper,
        *listings,
        reply(write(1)),
        reply(write(2)),
        reply(text="The report so far"),
        AIMessage("done"),
        AIMessage("x"),
        )

    # The witness that the third was asked with the note last, without which this passes
    # whatever the count reads.
    assert Scripted.heard[9][-1].text == warning(1)
    assert _said(cfg)["t1"].startswith(
        "[Incomplete: this subagent's replies reached the 32,768-token output limit 3 times "
        "in a row, and the answer below is cut off.]\n\nThe report so far"
    )
    assert len(Scripted.script) == 1, "the third cut answer was asked for again"


def test_each_cut_is_recorded_on_its_model_call(writing):
    """A cut the agent got past, a hidden cut answer included, would leave no trace at all."""
    sink = RecordedEvents()

    _turn(writing, reply(text="The"), reply(write(1)), reply(text="done", cut=False), events=sink)

    assert [one["cut_off"] for one in sink.named("model_call")] == [True, True, False]


def test_the_stream_marks_a_cut_answer_where_the_answer_starts_over(writing):
    """Its text has already streamed to whoever is watching, and with no event for it the
    shorter answer would run straight on from the cut one."""
    Scripted.script.extend([reply(text="The three findings are"), reply(text="Three.", cut=False)])
    service = Kingfisher(writing, sessions=LocalSessions())

    events = list(service.stream(Request("go", agent="only", session_id="s")))

    calls = [one for one in events if one.kind == "model_call"]
    assert [one.cut_off for one in calls] == [True, False]
    assert calls[0].usage["output_tokens"] == CAP
    assert events[-1].result is not None
    assert events[-1].result.answer == "Three."


def test_a_subagents_cut_answer_is_asked_again_and_marked_as_its_own(writing):
    """The caller is handed the shorter answer as any other, and a watcher sees whose
    answer started over."""
    subagents_dir(writing).mkdir(parents=True, exist_ok=True)
    (subagents_dir(writing) / "helper.yaml").write_text(
        "name: helper\ndescription: Helps.\n"
        'builtin_tools: ["*"]\ntools: ["*"]\n'
        "system_prompt: |\n  You help.\n",
        encoding="utf-8",
    )
    an_agent(writing, subagents="[helper]", **EVERY_TOOL)
    asked = {"description": "go", "subagent_type": "helper"}
    to_helper = AIMessage(content="", tool_calls=[{"name": "task", "args": asked, "id": "t1"}])
    Scripted.script.extend(
        [to_helper, reply(text="The report so"), AIMessage("Short report."), AIMessage("done")]
    )
    service = Kingfisher(writing, sessions=LocalSessions())

    events = list(service.stream(Request("go", agent="only", session_id="s")))

    (cut,) = [one for one in events if one.kind == "model_call" and one.cut_off]
    assert cut.agent == "helper"
    assert _said(writing)["t1"].startswith("Short report.")


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
    """A graph built without it would save a cut file, and one built for the wrong role
    would mark the agent's own answer as a subagent's, or leave a subagent's unmarked."""
    subagents_dir(writing).mkdir(parents=True, exist_ok=True)
    (subagents_dir(writing) / "helper.yaml").write_text(
        "name: helper\ndescription: Helps.\n"
        'builtin_tools: ["*"]\ntools: ["*"]\n'
        "system_prompt: |\n  You help.\n",
        encoding="utf-8",
    )
    an_agent(writing, subagents="[helper]", **EVERY_TOOL)
    backend = backend_at(writing, writing.workspace / "sessions" / "s")

    built = build_agent(writing, backend=backend, agent=_spec(writing, "only"))

    def marks(middleware: Any) -> list[bool]:
        return [one.subagent for one in middleware if isinstance(one, ModelReplyMiddleware)]

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
    assert not [one for one in nodes if ModelReplyMiddleware.__name__ in one]
