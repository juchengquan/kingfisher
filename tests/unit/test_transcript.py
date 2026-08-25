"""A session's history, as records kingfisher owns rather than a framework's.

Two claims are under test and they pull in opposite directions. A transcript has
to be *portable* -- readable by whatever runs the next turn, which may not be
this harness -- and it has to be *faithful*, because an agent that cannot see
what it already did will do it again.
"""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from kingfisher.domain.transcript import Message, ToolCall, as_json, from_json
from kingfisher.infrastructure.harness.runtime import as_transcript, user_payload


def a_conversation():
    """One exchange with a tool call in the middle -- the shape that matters."""
    return [
        HumanMessage(content="summarise /data/x.csv"),
        AIMessage(
            content="",
            tool_calls=[{"name": "csv_profile", "args": {"path": "/data/x.csv"}, "id": "c1"}],
        ),
        ToolMessage(content="40 rows", tool_call_id="c1", name="csv_profile"),
        AIMessage(content="Forty rows."),
    ]


def test_a_conversation_survives_the_round_trip(tmp_path):
    """Through records, through a file, and back. If this loses anything, a
    session that outlived its machine comes back subtly wrong -- which is worse
    than coming back empty, because nothing would say so."""
    records = as_transcript(a_conversation())
    written = tmp_path / "transcript.jsonl"
    written.write_text(as_json(records), encoding="utf-8")

    assert from_json(written.read_text(encoding="utf-8")) == records


def test_what_the_agent_did_is_kept_not_only_what_it_said():
    """The reason "flattened" is not the same as "user and assistant".

    Keep only the question and the answer and the next turn sees "summarise
    /data/x.csv" -> "Forty rows." with no record that `csv_profile` ran or what
    it returned. The agent then re-does work and cannot refer to its own.
    """
    records = as_transcript(a_conversation())

    assert [m.role for m in records] == ["user", "assistant", "tool", "assistant"]
    assert records[1].tool_calls == (
        ToolCall(name="csv_profile", args={"path": "/data/x.csv"}, id="c1"),
    )
    assert records[2].call_id == "c1", "a tool result that answers nothing is not a result"
    assert records[2].content == "40 rows"


def test_nothing_stored_belongs_to_a_framework(tmp_path):
    """The portability claim, made checkable.

    A transcript is the thing most likely to outlive this harness, so what lands
    on disk has to be readable without it. Asserted against the *file* rather
    than the objects, because that is what a later reader has.
    """
    document = as_json(as_transcript(a_conversation()))

    for line in document.splitlines():
        parsed = json.loads(line)
        assert set(parsed) <= {"role", "content", "tool_calls", "call_id", "name"}
        assert parsed["role"] in {"system", "user", "assistant", "tool"}
    assert "langchain" not in document.lower()


def test_records_go_back_to_the_shape_the_graph_wants():
    """The other direction. A tool result has to become a real `ToolMessage`:
    `tool_call_id` has no dict spelling that survives coercion, and losing it
    breaks the pairing that makes the result mean anything."""
    records = as_transcript(a_conversation())

    sent = user_payload("and now?", records)["messages"]

    assert len(sent) == len(records) + 1
    tool_message = next(m for m in sent if isinstance(m, ToolMessage))
    assert tool_message.tool_call_id == "c1"
    assert sent[-1] == {"role": "user", "content": "and now?"}


def test_a_turn_with_no_history_sends_only_the_question():
    """The first turn of a session, and the shape this had before history was
    passed at all."""
    assert user_payload("hello")["messages"] == [{"role": "user", "content": "hello"}]


def test_block_content_is_flattened_to_text():
    """Anthropic returns a list of blocks where OpenAI returns a string. A
    transcript that stored one shape would be unreadable by the other."""
    blocks = AIMessage(content=[{"type": "text", "text": "one "}, {"type": "text", "text": "two"}])

    assert as_transcript([blocks])[0].content == "one two"


def test_a_message_kind_this_vocabulary_has_no_role_for_is_dropped():
    """Visibly losing information beats inventing a role.

    A framework that grows a fifth kind of message should make this obvious
    rather than store something the next reader has to guess at.
    """

    class Odd:
        type = "some_future_thing"
        content = "?"

    assert as_transcript([Odd(), HumanMessage(content="hi")]) == (
        Message(role="user", content="hi"),
    )


def test_a_system_message_has_a_role_even_though_none_is_stored():
    """The prompt is the cached prefix and is rebuilt per turn, so one should
    not appear here -- but the vocabulary covers it, because a transcript that
    could not express a system message would be one no other harness could
    hand back."""
    assert as_transcript([SystemMessage(content="rules")])[0].role == "system"


def test_a_blank_line_is_skipped_and_a_broken_one_is_not(tmp_path):
    """A file re-read across a crash may end in whitespace, and losing a whole
    conversation to that is a worse answer than ignoring it.

    A *malformed* line is different: skipping it would be data loss with no
    error, handing back a shorter conversation that looks complete.
    """
    good = as_json((Message(role="user", content="hi"),))

    assert from_json(good + "\n\n") == (Message(role="user", content="hi"),)
    with pytest.raises(json.JSONDecodeError):
        from_json(good + "{not json\n")
