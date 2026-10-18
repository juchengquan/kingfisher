"""Clearing older results and file text to handles in the main agent's conversation.

Two halves. The rule -- what is cleared, what a handle says, when a pass runs -- is driven
on conversations built here, because its cases are positions in a list and a turn can
reach few of them. What the rule is for -- the summariser counting the smaller
conversation, the transcript carrying it, the handle naming a file that is still there --
is driven through real turns, because a stack assembled by hand would put the hook
wherever the test did.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any

import pytest
from deepagents.middleware._message_eviction import (
    _TOO_LARGE_TOOL_MSG,
    _create_content_preview,
    _render_preview_stub,
)
from langchain.agents.middleware import ExtendedModelResponse, ModelRequest, ModelResponse
from langchain_anthropic import ChatAnthropic
from langchain_anthropic.chat_models import _format_messages
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately

from kingfisher import Kingfisher, Request
from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.agent.middlewares.clearing import (
    DEFAULT_MARK,
    ResultClearingMiddleware,
    high_mark,
)
from kingfisher.core.agent.middlewares.model_reply import _refusal
from kingfisher.core.agent.middlewares.recorder import (
    UNRECORDED,
    _arguments,
    _summary,
)
from kingfisher.core.agent.models import summarizer_trigger
from kingfisher.core.definitions.agents.catalogue import LocalAgentRepository
from kingfisher.core.layout import HARNESS, OFFLOADS_ROUTE, TOOL_RESULTS_ROUTE, TRANSCRIPT_FILE
from kingfisher.core.result import MAIN, LedgerEntry
from kingfisher.core.session.ledger import Ledger
from kingfisher.core.session.store import KeptLedger
from kingfisher.core.session.transcript import from_json
from kingfisher.plugins import LocalSessions
from kingfisher.plugins.sessions.backend import backend_at
from tests.conftest import (
    EVERY_TOOL,
    FakeToolCallingModel,
    an_agent,
    start,
    subagents_dir,
    tools_dir,
)
from tests.unit.scripted import Scripted

#: About 2,000 tokens, which a mark of 1,000 clears at once.
BIG = "x" * 8_000


def _text(first: str) -> str:
    return f"{first}\n{BIG}"


class Talk:
    """A main agent's conversation built call by call, with the rows the turn's recorder
    would have kept for it: a row and an id for every successful result of a recorded
    tool, as `RecorderMiddleware` writes them.
    """

    def __init__(self, earlier: KeptLedger | None = None) -> None:
        self.messages: list[Any] = [HumanMessage("go", id="m0")]
        self.ledger = Ledger("t", earlier or KeptLedger())

    def _id(self) -> str:
        return f"m{len(self.messages)}"

    def say(self, text: str) -> None:
        self.messages.append(AIMessage(content=text, id=self._id()))

    def ask(self, text: str) -> None:
        self.messages.append(HumanMessage(content=text, id=self._id()))

    def batch(self, *calls: tuple[str, dict[str, Any], str], status: str = "success") -> list[str]:
        """One reply asking for these calls together, then each one's result."""
        ids = [f"c{len(self.messages)}{n}" for n in range(len(calls))]
        made = [
            {"name": name, "args": args, "id": at}
            for (name, args, _), at in zip(calls, ids, strict=True)
        ]
        self.messages.append(AIMessage(content="", tool_calls=made, id=self._id()))
        for (name, args, text), at in zip(calls, ids, strict=True):
            self.messages.append(self._result(name, args, text, at, status))
        return ids

    def call(self, name: str, args: dict[str, Any], text: str, status: str = "success") -> str:
        return self.batch((name, args, text), status=status)[0]

    def pad(self) -> None:
        """The latest batch and the three most recent results, small, so that what a test
        put before them is old enough to clear."""
        for n in range(4):
            self.call("web_search", {"query": f"pad {n}"}, f"pad {n}")

    def _result(
        self, name: str, args: dict[str, Any], text: str, at: str, status: str
    ) -> ToolMessage:
        content = text
        if name not in UNRECORDED and status != "error":
            written = _arguments(args)
            entry = self.ledger.add(
                lambda number: LedgerEntry(
                    id=number, turn="t", call=at, run=None, role=MAIN, tool=name,
                    args=written, access=None, size=len(text.encode("utf-8")),
                    path=f"{TOOL_RESULTS_ROUTE}{number}-{name}.txt",
                    summary=_summary(name, written, text),
                )
            )
            content = f"{text}\n[{entry.id}]"
        return ToolMessage(
            content=content, tool_call_id=at, name=name, status=status, id=self._id()
        )

    def result(self, call: str) -> Any:
        return _of(self.messages, call)

    def write(self, path: str, content: str = BIG) -> str:
        """A `write_file` that succeeded, answered the way deepagents answers one."""
        args = {"file_path": path, "content": content}
        return self.call("write_file", args, f"Updated file {path}")

    def passed(
        self, high: int = 1_000, sent: list[Any] | None = None, system: str | None = None
    ) -> tuple[list[Any], dict[str, Any] | None]:
        """What one model call at this high mark sent, and the state update it returned.

        `sent` is the conversation as deepagents' summariser hands it on, the whole of it
        unless a test says otherwise; the state is the whole conversation either way.
        """
        request = ModelRequest(
            model=FakeToolCallingModel(responses=[]),
            messages=list(self.messages if sent is None else sent),
            system_message=None if system is None else SystemMessage(system),
            tools=[],
            state={"messages": list(self.messages)},
        )
        heard: list[Any] = []

        def model(request: Any) -> ModelResponse:
            heard.append(request)
            return ModelResponse(result=[AIMessage("ok")])

        returned = ResultClearingMiddleware(self.ledger, high, OFFLOADS_ROUTE).wrap_model_call(
            request, model
        )
        (asked,) = heard
        if isinstance(returned, ExtendedModelResponse):
            assert returned.command is not None
            return asked.messages, returned.command.update
        return asked.messages, None

    def cleared(self, high: int = 1_000, sent: list[Any] | None = None) -> list[Any]:
        """The conversation after one model call at this high mark."""
        return _applied(self.messages, self.passed(high, sent)[1])


def _stub(call: str, path: str, content: str) -> str:
    """The stub deepagents' eviction hands the model in place of `content`."""
    return _render_preview_stub(
        _TOO_LARGE_TOOL_MSG, _create_content_preview(content), tool_call_id=call, file_path=path
    )


def _applied(messages: list[Any], update: dict[str, Any] | None) -> list[Any]:
    """`update` written into `messages` the way deepagents' reducer writes it: by id."""
    if update is None:
        return list(messages)
    by_id = {one.id: one for one in update["messages"]}
    assert set(by_id) <= {one.id for one in messages}, "an update added a message"
    return [by_id.get(one.id, one) for one in messages]


def _of(messages: list[Any], call: str) -> Any:
    return next(m for m in messages if isinstance(m, ToolMessage) and m.tool_call_id == call)


def _call_in(messages: list[Any], call: str) -> dict[str, Any]:
    return next(
        c for m in messages if isinstance(m, AIMessage) for c in m.tool_calls if c["id"] == call
    )


def _changed(before: list[Any], after: list[Any]) -> list[int]:
    return [at for at, (one, two) in enumerate(zip(before, after, strict=True)) if one != two]


# -- when a pass runs ----------------------------------------------------------------


def test_nothing_is_cleared_below_the_high_mark():
    """A pass on every call would cost the cache everything after the earliest message it
    touched, for a conversation that was never large."""
    talk = Talk()
    talk.call("web_search", {"query": "one"}, _text("one"))
    talk.pad()
    total = count_tokens_approximately(talk.messages)

    assert talk.cleared(high=total + 1) == talk.messages
    assert talk.cleared(high=total) != talk.messages, "the control: at the mark it runs"


def test_a_pass_clears_the_oldest_first_and_stops_once_under_the_low_mark():
    """Clearing everything clearable would throw away results the next call may want, and
    clearing newest first would throw away the ones it most likely wants."""
    talk = Talk()
    old = [talk.call("web_search", {"query": f"q{n}"}, _text(f"result {n}")) for n in range(14)]
    talk.pad()
    high = 8_000

    after = talk.cleared(high=high)

    cleared = [call for call in old if _of(after, call) != talk.result(call)]
    assert cleared == old[: len(cleared)], "not oldest first"
    assert 0 < len(cleared) < len(old), "stopped for want of anything left, not at the mark"
    assert count_tokens_approximately(after) <= high // 2
    last = after.index(_of(after, cleared[-1]))
    one_fewer = [*after[:last], talk.messages[last], *after[last + 1 :]]
    assert count_tokens_approximately(one_fewer) > high // 2, "kept clearing past the low mark"


def test_a_pass_that_could_free_less_than_a_quarter_of_the_mark_does_not_run():
    """Once what can never be cleared nears the low mark, every call is over the high one,
    and a pass freeing a few hundred tokens would run on each of them."""
    talk = Talk()
    talk.ask("y" * 24_000)
    small = talk.call("web_search", {"query": "small"}, _text("small")[:2_000])
    talk.pad()

    assert talk.cleared(high=4_000) == talk.messages

    talk = Talk()
    talk.ask("y" * 24_000)
    large = talk.call("web_search", {"query": "large"}, _text("large"))
    talk.pad()

    after = talk.cleared(high=4_000)
    assert _of(after, large) != talk.result(large), "the control: a quarter's worth runs"
    assert small


def test_the_count_includes_the_prompt_and_tools_the_call_sends():
    """The summariser counts the system prompt and the tools with the conversation, so a
    mark counted on the conversation alone would be reached later than the one it is
    measured against."""
    talk = Talk()
    talk.call("web_search", {"query": "one"}, _text("one"))
    talk.pad()
    high = count_tokens_approximately(talk.messages) + 500

    assert talk.passed(high)[1] is None
    assert talk.passed(high, system="p" * 4_000)[1] is not None


def test_the_call_a_pass_runs_on_sends_the_handles():
    """The pass runs inside the call, so the request that call sends is already the
    cleared one: a pass that only updated the state would pay for the whole conversation
    once more."""
    talk = Talk()
    call = talk.call("web_search", {"query": "one"}, _text("one"))
    talk.pad()

    sent, update = talk.passed()

    assert update is not None
    assert _of(sent, call) == _of(_applied(talk.messages, update), call) != talk.result(call)


def test_a_handle_is_made_from_the_message_in_state_not_the_one_sent():
    """deepagents' summariser shortens older write and edit arguments in what it sends
    on, past its own trigger -- and, on a model with no window, once twenty messages
    precede them. A handle made from that copy would name the length of the shortened
    text, and write it into the conversation for good."""
    talk = Talk()
    wrote = talk.write("/outputs/r.md")
    talk.pad()
    at = talk.messages.index(talk.result(wrote)) - 1
    (call,) = talk.messages[at].tool_calls
    short = {"file_path": "/outputs/r.md", "content": "x" * 2_000 + "...(argument truncated)"}
    shortened = talk.messages[at].model_copy(update={"tool_calls": [{**call, "args": short}]})
    sent = [*talk.messages[:at], shortened, *talk.messages[at + 1 :]]

    after = talk.cleared(high=500, sent=sent)

    assert _call_in(after, wrote)["args"]["content"].startswith("8,000 chars written")


SHORTENED = "x" * 2_000 + "...(argument truncated)"


def _shortened(message: AIMessage, call: str) -> AIMessage:
    """`message` as deepagents' summariser sends it on, `call`'s file text shortened."""
    calls = [
        {**one, "args": {**one["args"], "content": SHORTENED}} if one["id"] == call else one
        for one in message.tool_calls
    ]
    return message.model_copy(update={"tool_calls": calls})


def test_what_the_summariser_shortened_in_another_call_stays_out_of_the_conversation():
    """A message cleared is written back whole, so one built from the copy sent would
    carry the summariser's shortened text for the other call in it into the conversation
    for good, where until then it shortened only what one request sent."""
    talk = Talk()
    old, recent = talk.batch(
        *(
            ("write_file", {"file_path": path, "content": BIG}, f"Updated file {path}")
            for path in ("/outputs/a.md", "/outputs/b.md")
        )
    )
    # Three more results, so that `recent` is among the most recent kept and `old` is not.
    for n in range(2):
        talk.call("web_search", {"query": f"r{n}"}, f"r{n}")
    talk.call("web_search", {"query": "latest"}, "latest")
    at = talk.messages.index(talk.result(old)) - 1
    sent = [*talk.messages[:at], _shortened(talk.messages[at], recent), *talk.messages[at + 1 :]]

    after = talk.cleared(high=500, sent=sent)

    assert _call_in(after, old)["args"]["content"].startswith("8,000 chars written")
    assert _call_in(after, recent)["args"]["content"] == BIG


def test_what_a_pass_frees_is_counted_on_what_the_call_sends():
    """Where the summariser already sends a shortened copy, clearing it frees only that
    much on this call; counting the whole would start a pass the quarter-mark guard is
    there to stop, re-caching everything after it for a few hundred tokens."""
    talk = Talk()
    talk.ask("y" * 16_000)
    wrote = talk.write("/outputs/r.md")
    talk.pad()
    at = talk.messages.index(talk.result(wrote)) - 1
    sent = [*talk.messages[:at], _shortened(talk.messages[at], wrote), *talk.messages[at + 1 :]]

    assert talk.passed(high=4_000, sent=sent)[1] is None
    assert talk.passed(high=4_000)[1] is not None, "the control: the whole text is worth it"


def test_a_message_with_no_id_is_left_alone():
    """deepagents' reducer appends a message with no id instead of replacing one, so its
    handle would arrive as a second copy at the end of the conversation."""
    talk = Talk()
    call = talk.call("web_search", {"query": "one"}, _text("one"))
    other = talk.call("web_search", {"query": "two"}, _text("two"))
    at = talk.messages.index(talk.result(call))
    talk.messages[at] = talk.messages[at].model_copy(update={"id": None})
    talk.pad()

    _, update = talk.passed()

    assert update is not None
    assert [m.tool_call_id for m in update["messages"]] == [other]


# -- what a handle says ----------------------------------------------------------------


def test_a_recorded_results_handle_names_its_file_and_ends_in_its_id():
    """The model is told to read a result back from its file rather than call the tool
    again, so a handle without the path, or without the id `system.md` explains, leaves
    it nothing to do but call again."""
    talk = Talk()
    call = talk.call("execute", {"command": "python3 count.py"}, _text("rows: 86"))
    talk.pad()
    (row,) = (e for e in talk.ledger.entries if e.call == call)

    handle = _of(talk.cleared(), call).content

    assert handle == (
        "execute command='python3 count.py' -- rows: 86\n"
        f"({row.size:,} bytes, cleared; full result: /scratchpad/tool_results/A-0001-execute.txt)\n"
        "[A-0001]"
    )


def test_an_earlier_turns_result_is_named_from_the_ledger_the_turn_found():
    """A replayed result's row is in the ledger the turn read, not in this turn's: a
    handle looked up in this turn's alone would leave every replayed result whole."""
    earlier = Talk()
    call = earlier.call("web_search", {"query": "EV"}, _text("Ten results"))
    kept = KeptLedger(last=1, exchanges=(earlier.ledger.entries,))
    talk = Talk(kept)
    talk.messages = list(earlier.messages)
    talk.pad()

    handle = _of(talk.cleared(), call).content

    assert "full result: /scratchpad/tool_results/A-0001-web_search.txt" in handle
    assert handle.endswith("\n[A-0001]")
    assert talk.ledger.entries[0].id == "A-0002", "numbered on from the ledger"


def test_an_offloaded_result_is_named_by_a_search_for_its_id():
    """deepagents names its copy after the call id, with no turn in the name, so a later
    call with the same id overwrites it: a path would then name another call's result,
    and a search for the id finds nothing instead.

    Both ways a result is offloaded: a row the last turn pointed at deepagents' copy, and
    this turn's result, whose stub is shorter than what was recorded.
    """
    earlier = Talk()
    old = earlier.call("http_fetch", {"url": "https://a.example"}, _text("<html>"))
    (row,) = earlier.ledger.entries
    moved = replace(row, path=f"{OFFLOADS_ROUTE}{old}")
    talk = Talk(KeptLedger(last=1, exchanges=((moved,),)))
    talk.messages = list(earlier.messages)
    whole = "\n".join(f"<p>{n} {'x' * 200}</p>" for n in range(500))
    new = talk.call("http_fetch", {"url": "https://b.example"}, whole)
    # What deepagents' eviction leaves, from its own template and preview, so `EVICTED`
    # is checked against the stub it names.
    stub = _stub(new, f"{OFFLOADS_ROUTE}{new}", talk.result(new).content)
    talk.messages[-1] = talk.messages[-1].model_copy(update={"content": stub})
    talk.pad()

    after = talk.cleared()

    for call, number in ((old, "A-0001"), (new, "A-0002")):
        handle = _of(after, call).content
        assert f"offloaded: grep for [{number}] under /scratchpad/large_tool_results/)" in handle
        assert handle.endswith(f"\n[{number}]")


def test_a_result_recorded_as_a_stub_is_named_by_its_own_copy():
    """A tool that offloads its own output hands the recorder the stub, so the copy the
    row names holds what the model saw and the offloaded file holds no id to search for.
    """
    talk = Talk()
    call = talk.call("execute", {"command": "make"}, _stub("c1", "/elsewhere/c1", BIG + "\n" + BIG))
    talk.pad()

    handle = _of(talk.cleared(high=500), call).content

    assert "full result: /scratchpad/tool_results/A-0001-execute.txt" in handle


def test_a_file_tools_handle_names_the_call_that_gets_it_back():
    """A file tool's result has no saved copy, so the handle is the only record of which
    file and which lines it was: without them the model cannot ask for them again."""
    talk = Talk()
    args = {"file_path": "/inputs/service.py", "offset": 300, "limit": 100}
    read = talk.call("read_file", args, "a" * 6_308)
    talk.pad()

    handle = _of(talk.cleared(), read).content

    assert handle == (
        "read_file file_path='/inputs/service.py' offset=300 limit=100 -- 6,308 chars, "
        "cleared; call read_file again to see it as it is now"
    )


def test_a_read_that_was_more_than_text_is_not_given_a_length():
    """A PDF comes back as a file block, whose text is empty: `0 chars` would tell the model
    there was nothing there."""
    talk = Talk()
    pdf = {"type": "file", "mime_type": "application/pdf", "base64": "J" * 40_000}
    read = talk.call("read_file", {"file_path": "/inputs/a.pdf"}, "")
    talk.messages[-1] = talk.messages[-1].model_copy(update={"content": [pdf]})
    talk.pad()

    handle = _of(talk.cleared(), read).content

    assert handle == (
        "read_file file_path='/inputs/a.pdf' -- cleared; "
        "call read_file again to see it as it is now"
    )


def test_a_cleared_write_and_edit_keep_their_call_id_and_file_path():
    """The text moves out and the call stays: its id pairs it with its result, and its
    path is where the text now is."""
    talk = Talk()
    wrote = talk.write("/outputs/r.md")
    edited = talk.call(
        "edit_file",
        {"file_path": "/outputs/r.md", "old_string": "z" * 492, "new_string": "w" * 7_699},
        "Successfully replaced 1 instance(s) of the string in '/outputs/r.md'",
    )
    talk.pad()

    after = talk.cleared()

    assert (_call_in(after, wrote)["name"], _call_in(after, wrote)["id"]) == ("write_file", wrote)
    assert _call_in(after, wrote)["args"] == {
        "file_path": "/outputs/r.md",
        "content": "8,000 chars written, cleared; /outputs/r.md holds the current text",
    }
    assert _call_in(after, edited)["args"] == {
        "file_path": "/outputs/r.md",
        "old_string": "492 chars, cleared",
        "new_string": "7,699 chars, cleared; /outputs/r.md holds the current text",
    }


def test_text_a_reply_carries_twice_is_cleared_in_both():
    """A streamed Anthropic reply holds each call in `tool_calls` and again as a
    `tool_use` block. langchain-anthropic sends the first and langchain counts the second,
    so clearing one alone sent the text, or counted it, all the same. Driven through
    langchain-anthropic's own formatting and langchain's own count."""
    talk = Talk()
    args = {"file_path": "/outputs/r.md", "content": BIG}
    talk.messages.append(
        AIMessage(
            content=[
                {"type": "text", "text": "Writing it."},
                {"type": "tool_use", "id": "w1", "name": "write_file", "input": {},
                 "partial_json": json.dumps(args)},
            ],
            tool_calls=[{"name": "write_file", "args": args, "id": "w1"}],
            id="streamed",
        )
    )
    talk.messages.append(
        ToolMessage("Updated file /outputs/r.md", tool_call_id="w1", name="write_file", id="w1r")
    )
    talk.pad()
    before = next(m for m in talk.messages if m.id == "streamed")

    after = next(m for m in talk.cleared() if m.id == "streamed")

    _, sent = _format_messages([after], model="claude-sonnet-4-5")
    (use,) = [b for b in sent[0]["content"] if b["type"] == "tool_use"]
    assert use["input"]["content"].startswith("8,000 chars written, cleared")
    assert count_tokens_approximately([after]) < count_tokens_approximately([before]) - 1_500
    assert after.content[0] == {"type": "text", "text": "Writing it."}


# -- what is never cleared -----------------------------------------------------------


def _refused() -> str:
    """The text of a call refused for being cut off, as `ModelReplyMiddleware` writes it."""
    spent = {"input_tokens": 1, "output_tokens": 2_000, "total_tokens": 2_001}
    cut = AIMessage(content="", usage_metadata=spent)
    return _refusal(cut, {"id": "x", "name": "write_file"}).text


def test_what_exists_nowhere_else_or_says_it_failed_is_never_cleared():
    """An inline script, a subagent's answer and what was said exist nowhere else, and a
    failure is what stops the same call being made again. A failed write's text was
    never written, so a handle saying the file holds it would be false.

    Failures as a replayed result shows them, with no status: the transcript keeps none.
    """
    talk = Talk()
    talk.ask("y" * 9_000)
    talk.say("z" * 9_000)
    script = talk.call("execute", {"command": "python3 - <<'PY'\n" + BIG + "\nPY"}, "86")
    answer = talk.call("task", {"description": "dig"}, _text("what the subagent found"))
    failed = [
        talk.call("web_search", {"query": "q"}, _text("boom"), status="error"),
        talk.call("read_file", {"file_path": "/x"}, "Error: " + BIG),
        talk.call(
            "write_file",
            {"file_path": "/outputs/r.md", "content": BIG},
            "Tool call write_file with id x did not complete - no result was recorded.",
        ),
        talk.call("execute", {"command": "pytest"}, BIG + "\n[Command failed with exit code 1]"),
        # Measured: a turn later, a refusal read as a file tool's result was cleared to a
        # handle telling the agent to call `write_file` again to see the file.
        talk.call("write_file", {"file_path": "/outputs/r.md", "content": BIG}, _refused()),
    ]
    clearable = talk.call("web_search", {"query": "fine"}, _text("fine"))
    talk.pad()

    after = talk.cleared()

    assert _changed(talk.messages, after) == [talk.messages.index(talk.result(clearable))], (
        "something but the one clearable result changed, or a pass never ran"
    )
    assert _call_in(after, script) == _call_in(talk.messages, script)
    assert answer and failed


def test_the_latest_batch_and_the_three_most_recent_results_stay_whole():
    """The latest batch is what the next call answers, and the most recent results the
    likeliest to be read again; clearing them sends the model straight back for them."""
    talk = Talk()
    old = [
        talk.call("web_search", {"query": "old"}, _text("old")),
        talk.write("/outputs/a.md"),
    ]
    recent = [
        talk.call("web_search", {"query": "r1"}, _text("r1")),
        talk.write("/outputs/b.md"),
        talk.call("read_file", {"file_path": "/inputs/c.txt"}, _text("c")),
    ]
    latest = talk.batch(
        ("web_search", {"query": "l1"}, _text("l1")), ("web_search", {"query": "l2"}, _text("l2"))
    )

    after = talk.cleared()

    assert _of(after, old[0]) != talk.result(old[0])
    assert _call_in(after, old[1])["args"]["content"].endswith("holds the current text")
    for call in (*recent, *latest):
        assert _of(after, call) == talk.result(call), call
    assert _call_in(after, recent[1])["args"]["content"] == BIG
    assert _call_in(after, latest[0]) == _call_in(talk.messages, latest[0])


def test_messages_keep_their_ids_their_order_and_their_number():
    """The summariser's cut is a position in this list; a message removed or moved would
    leave it pointing at the wrong one, which `findings.md` records breaking the view."""
    talk = Talk()
    for n in range(6):
        talk.call("web_search", {"query": f"q{n}"}, _text(f"q{n}"))
    talk.pad()

    after = talk.cleared()

    assert [m.id for m in after] == [m.id for m in talk.messages]
    assert _changed(talk.messages, after), "the control: a pass ran"


def test_a_handle_is_never_cleared_again():
    """A later pass reads a handle as if it were what it replaced. A file tool's, cleared
    again, named its own length; an offloaded result's, no longer a stub, was pointed at
    the copy the turn's sweep removes -- measured through a real turn."""
    talk = Talk()
    talk.call("read_file", {"file_path": "/inputs/a.txt"}, "a" * 6_000)
    # Long enough that its handle, cleared again, is shorter by more than the one token
    # each message's count rounds up to, so a pass would take it.
    talk.write("/outputs/b.md", "c" * 1_000_000)
    talk.call("web_search", {"query": "q"}, _text("q"))
    whole = "\n".join(f"row {n} {'x' * 200}" for n in range(500))
    offloaded = talk.call("http_fetch", {"url": "https://a.example"}, whole)
    stub = _stub(offloaded, f"{OFFLOADS_ROUTE}{offloaded}", talk.result(offloaded).content)
    talk.messages[-1] = talk.messages[-1].model_copy(update={"content": stub})
    talk.pad()
    once = talk.cleared()
    handles = _changed(talk.messages, once)
    assert len(handles) == 4, "the first pass cleared all four"
    assert "offloaded: grep for" in _of(once, offloaded).content
    talk.messages = once
    for n in range(3):
        talk.call("web_search", {"query": f"more {n}"}, _text(f"more {n}"))
    talk.pad()

    twice = talk.cleared()

    assert [twice[at] for at in handles] == [once[at] for at in handles]
    assert _changed(talk.messages, twice), "the control: the second pass ran"


def test_after_a_summary_only_what_is_still_sent_is_counted_or_cleared():
    """Past the summariser's cut the model is sent its summary instead, so clearing there
    frees nothing, and counting it would start passes the summariser never asked for."""
    talk = Talk()
    gone = [talk.call("web_search", {"query": f"g{n}"}, _text(f"g{n}")) for n in range(2)]
    cut = len(talk.messages)
    kept = talk.call("web_search", {"query": "k"}, _text("k"))
    talk.pad()
    view = [HumanMessage("a summary"), *talk.messages[cut:]]
    size = count_tokens_approximately(view)

    after = talk.cleared(high=size, sent=view)

    assert [_of(after, call) for call in gone] == [talk.result(call) for call in gone]
    assert _of(after, kept) != talk.result(kept)
    assert talk.cleared(high=size + 1, sent=view) == talk.messages


# -- the marks -----------------------------------------------------------------------


def _anthropic(window: int | None, output: int = 4_096) -> ChatAnthropic:
    profile = None if window is None else {"max_input_tokens": window}
    return ChatAnthropic(
        model="a-gateway-model", api_key="sk-not-real", max_tokens=output, profile=profile
    )


def test_the_default_high_mark_is_64k_or_half_the_summarisers_trigger():
    """64k alone would never fire before a small window's summariser; half its trigger
    alone would wait for 425k on the shipped MiniMax row."""
    assert high_mark(_anthropic(1_000_000, 32_768), None) == DEFAULT_MARK
    assert high_mark(_anthropic(None), None) == DEFAULT_MARK
    small = _anthropic(128_000)
    assert summarizer_trigger(small) == 108_800
    assert high_mark(small, None) == 54_400


def test_a_written_mark_is_used_as_written():
    """Even where the default rule would set a lower one: the entry is the deployment
    saying where, and capping it would make the setting mean something else."""
    assert high_mark(_anthropic(1_000_000, 32_768), 100_000) == 100_000
    assert high_mark(_anthropic(128_000), 90_000) == 90_000


# -- through real turns --------------------------------------------------------------

TOOLS = '''
def bulky(n: int) -> str:
    """Return about 16,000 characters, under what deepagents offloads."""
    return "\\n".join(f"row {n}-{i:04d} " + "y" * 70 for i in range(200))


def loud() -> str:
    """Return about 101,000 characters, past what deepagents shows a model whole."""
    return "\\n".join(f"row {n:05d} " + "x" * 90 for n in range(1_000))


TOOLS = [bulky, loud]
'''


@pytest.fixture
def bulky(scripted):
    tools_dir(scripted).mkdir(parents=True, exist_ok=True)
    (tools_dir(scripted) / "probe.py").write_text(TOOLS, encoding="utf-8")
    an_agent(scripted, **EVERY_TOOL)
    start(scripted, "s")
    return scripted


def _marked(cfg: Any, model: str | None = None, **fields: Any) -> Any:
    """`cfg`, with one model's entry -- the default's unless named -- carrying `fields`."""
    models = cfg.models
    name = model or models.default
    entry = replace(models.models[name], **fields)
    return replace(cfg, models=replace(models, models={**models.models, name: entry}))


def _turn(cfg: Any, *script: AIMessage, way: str = "sync") -> Any:
    """One turn, through `run` or `arun`: the two loops call different hooks."""
    Scripted.script.extend(script)
    service = Kingfisher(cfg, sessions=LocalSessions())
    request = Request("go", agent="only", session_id="s")
    return asyncio.run(service.arun(request)) if way == "async" else service.run(request)


def _one(name: str, at: int, **args: Any) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"c{at}"}])


def _transcript(cfg: Any) -> list[Any]:
    session = cfg.workspace / "sessions" / "s"
    return list(from_json((session / HARNESS / TRANSCRIPT_FILE).read_text(encoding="utf-8")))


def test_a_long_turn_clears_and_the_transcript_carries_the_handles(bulky, way):
    """What every later turn replays is the transcript, written from the turn's own
    conversation: a handle the model was shown but the transcript did not carry would
    clear nothing that matters. Each names a file still there once the turn is over."""
    cfg = _marked(bulky, clear_results_at=12_000)

    calls = (_one("bulky", n, n=n) for n in range(1, 7))
    result = _turn(cfg, *calls, AIMessage(content="done"), way=way)

    assert result.answer == "done"
    said = {one.call_id: one.content for one in _transcript(cfg) if one.role == "tool"}
    assert len(_transcript(cfg)) == 14, "a message was added or removed"
    for call, number in (("c1", "A-0001"), ("c2", "A-0002")):
        path = f"/scratchpad/tool_results/{number}-bulky.txt"
        assert said[call].startswith(f"bulky n={call[1]} -- row {call[1]}-0000 ")
        assert f"bytes, cleared; full result: {path})\n[{number}]" in said[call]
        kept = (cfg.workspace / "sessions" / "s" / path.lstrip("/")).read_text(encoding="utf-8")
        assert kept.startswith(f"row {call[1]}-0000") and len(kept) > 16_000
    for call in ("c3", "c4", "c5", "c6"):
        assert len(said[call]) > 16_000, call
    # The model was handed the handle on the calls after the pass, not only the file.
    last = [m.content for m in Scripted.heard[-1] if isinstance(m, ToolMessage)]
    assert last[0] == said["c1"] and last[1] == said["c2"]


def test_an_offloaded_results_handle_finds_it_after_the_turn(bulky):
    """The search the handle names has to find the result once the turn has swept our
    copy and pointed the row at deepagents': it finds the file ending in the id."""
    cfg = _marked(bulky, clear_results_at=12_000)

    bulky_calls = (_one("bulky", n, n=n) for n in range(1, 7))
    _turn(cfg, _one("loud", 0), *bulky_calls, AIMessage(content="done"))

    said = next(one.content for one in _transcript(cfg) if one.call_id == "c0")
    assert "cleared; offloaded: grep for [A-0001] under /scratchpad/large_tool_results/)" in said
    folder = cfg.workspace / "sessions" / "s" / "scratchpad" / "large_tool_results"
    found = [one for one in folder.iterdir() if "[A-0001]" in one.read_text(encoding="utf-8")]
    assert len(found) == 1
    assert found[0].read_text(encoding="utf-8").startswith("row 00000")


def test_the_summariser_counts_the_cleared_conversation(bulky, way):
    """What clearing is for: from the call after a pass, the summariser counts the
    conversation it rewrote. Fifteen results that would have tripped it on a
    60,000-token window go through without a summary."""
    window = {"profile": {"max_input_tokens": 60_000}}
    cfg = _marked(bulky, extra=window)
    trigger = int(60_000 * 0.85)

    calls = (_one("bulky", n, n=n) for n in range(1, 16))
    result = _turn(cfg, *calls, AIMessage(content="done"), way=way)

    assert result.answer == "done"
    assert sum(e.size for e in result.ledger) // 4 > trigger, "would not have summarised anyway"
    assert Scripted.summaries == 0
    cleared = [one for one in _transcript(cfg) if "bytes, cleared;" in one.content]
    assert cleared, "nothing was cleared, so nothing here kept the summariser off"


def test_clearing_adds_no_step_to_the_agents_loop(bulky):
    """langgraph's recursion limit counts graph steps, and a `before_model` hook is one:
    measured, it cut a turn's 150 steps from 50 model calls to 37, and a writing turn in
    the acceptance run stopped there."""
    backend = backend_at(bulky, bulky.workspace / "sessions" / "s")

    def steps(ledger: Ledger | None) -> set[str]:
        return set(build_agent(bulky, backend=backend, ledger=ledger).graph.get_graph().nodes)

    assert steps(Ledger("t", KeptLedger())) == steps(None)


def _clearing(middleware: Any) -> list[Any]:
    return [one for one in middleware if isinstance(one, ResultClearingMiddleware)]


def _spec(cfg: Any, name: str) -> Any:
    return LocalAgentRepository(cfg.catalogue_roots["agents"]).specs[name]


def test_only_the_main_agent_of_a_turn_kingfisher_built_clears(bulky):
    """A subagent's conversation ends with its answer, and its middleware is built apart;
    a build no turn keeps has no ledger for a handle to name."""
    subagents_dir(bulky).mkdir(parents=True, exist_ok=True)
    (subagents_dir(bulky) / "helper.yaml").write_text(
        "name: helper\ndescription: Helps.\n"
        'builtin_tools: ["*"]\ntools: ["*"]\n'
        "system_prompt: |\n  You help.\n",
        encoding="utf-8",
    )
    an_agent(bulky, subagents="[helper]", **EVERY_TOOL)
    backend = backend_at(bulky, bulky.workspace / "sessions" / "s")
    agent = _spec(bulky, "only")

    kept = build_agent(bulky, backend=backend, ledger=Ledger("t", KeptLedger()), agent=agent)
    none = build_agent(bulky, backend=backend, agent=agent)

    assert [type(one) for one in kept.middleware].count(ResultClearingMiddleware) == 1
    assert isinstance(kept.middleware[0], ResultClearingMiddleware), (
        "not where it measures the request"
    )
    assert {spec["name"] for spec in kept.subagents or ()} >= {"helper", "general-purpose"}
    for spec in kept.subagents or ():
        assert _clearing(spec.get("middleware", ())) == [], spec["name"]
    assert _clearing(none.middleware) == []


def test_the_mark_is_the_one_written_beside_the_model_the_agent_runs(bulky):
    """The value travels with the entry that built the model: the default's when the
    agent names none, the agent's own model's when it does, and the default rule for a
    model the deployment passed in, which has no entry."""
    cfg = _marked(_marked(bulky, clear_results_at=100_000), "cheap-model", clear_results_at=30_000)
    an_agent(cfg, "pinned", model="cheap-model")
    backend = backend_at(cfg, cfg.workspace / "sessions" / "s")
    ledger = Ledger("t", KeptLedger())

    def built(cfg: Any, **given: Any) -> Any:
        (one,) = _clearing(build_agent(cfg, backend=backend, ledger=ledger, **given).middleware)
        return one

    injected = FakeToolCallingModel(responses=[])
    assert built(cfg).high == 100_000
    assert built(cfg, agent=_spec(cfg, "only")).high == 100_000
    assert built(cfg, agent=_spec(cfg, "pinned")).high == 30_000
    assert built(cfg, model=injected).high == high_mark(injected, None) == DEFAULT_MARK
    assert built(bulky).high == DEFAULT_MARK, "an entry that sets none"
