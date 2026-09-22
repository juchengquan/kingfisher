"""A turn that stops for a person, and the turn that answers it.

Driven through the service with a real gated agent rather than a stub that reports a
pause. The pause has to survive being written out, read back, and re-entered by a
graph built a second time -- none of which a stub exercises, and all of which is what
the feature is.
"""

from __future__ import annotations

from dataclasses import replace as replace_cfg
from typing import Any

import pytest
from deepagents import create_deep_agent
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from kingfisher import Kingfisher, default_backend
from kingfisher.domain.request import Decision, DecisionError, Request, Resume
from kingfisher.domain.result import AWAITING, DECISIONS, END_TURN
from kingfisher.infrastructure.session_store import (
    PAUSED,
    PAUSED_PROVENANCE,
    read_pause_mark,
    write_pause_mark,
)
from kingfisher.infrastructure.workspace.sessions import session_bytes
from tests.conftest import FakeToolCallingModel


def _gated(*, calls: list[dict[str, Any]], after: str = "done", saver: Any = None) -> Any:
    """A real deep agent whose file writes need a person."""
    return create_deep_agent(
        model=FakeToolCallingModel(
            responses=[AIMessage(content="", tool_calls=calls), AIMessage(content=after)]
        ),
        tools=None,
        interrupt_on={"write_file": True},
        checkpointer=saver or InMemorySaver(),
    )


def _gated_on_disk(cfg, session_dir, *, calls: list[dict[str, Any]], after: str = "done") -> Any:
    """The same, on kingfisher's own backend so an approved write lands in the session.

    Worth the extra wiring in the three tests that use it. Without a real backend the
    file tools write into deepagents' in-state filesystem, where an approved call and
    a rejected one leave the session directory looking identical -- so the assertion
    that tells them apart would be asserting on nothing.
    """
    return create_deep_agent(
        model=FakeToolCallingModel(
            responses=[AIMessage(content="", tool_calls=calls), AIMessage(content=after)]
        ),
        backend=default_backend(cfg, session_dir),
        tools=None,
        interrupt_on={"write_file": True},
        checkpointer=InMemorySaver(),
    )


def _write(path: str, body: str, call_id: str) -> dict[str, Any]:
    return {"name": "write_file", "args": {"file_path": path, "content": body}, "id": call_id}


def _session_dir(cfg, session_id: str):
    return cfg.workspace / "sessions" / session_id


# -- stopping --------------------------------------------------------------


def test_a_gated_turn_stops_and_reports_what_it_is_waiting_on(cfg):
    """Without this a gated call either runs unapproved or vanishes: langgraph's
    `GraphInterrupt` is suppressed by the root graph and surfaces to nobody.
    """
    kf = Kingfisher(cfg, graph=_gated(calls=[_write("/derived/a.txt", "x", "c1")]))

    result = kf.run(Request("write it"))

    assert result.stop_reason == AWAITING
    assert not result.completed, "a turn waiting on a person reported itself finished"
    assert [(p.tool, p.args["file_path"]) for p in result.pending] == [
        ("write_file", "/derived/a.txt")
    ]
    assert result.pending[0].call_id, "a pending call with no id cannot be answered"


def test_the_offered_decisions_exclude_edit(cfg):
    """deepagents offers all four for a bare `True` gate, `edit` included -- and `edit`
    is the one that lets the answering caller author a call rather than judge one.
    """
    kf = Kingfisher(cfg, graph=_gated(calls=[_write("/derived/a.txt", "x", "c1")]))

    offered = kf.run(Request("write it")).pending[0].decisions

    assert "edit" not in offered
    assert set(offered) <= set(DECISIONS)
    assert "approve" in offered, "nothing could be approved, so the gate is unanswerable"


def test_a_gated_turn_emits_the_event_before_it_finishes(cfg):
    """`run` drains the stream, so a caller watching one must hear it there too."""
    kf = Kingfisher(cfg, graph=_gated(calls=[_write("/derived/a.txt", "x", "c1")]))

    kinds = [event.kind for event in kf.stream(Request("write it"))]

    assert "decision_needed" in kinds
    assert kinds.index("decision_needed") < kinds.index("finished")


def test_the_pause_is_kept_inside_the_session(cfg):
    """It has to die with the session and be counted by the quota, which a file
    anywhere else would not be -- and `.harness` is the one place the agent's file
    tools are refused.
    """
    kf = Kingfisher(cfg, graph=_gated(calls=[_write("/derived/a.txt", "x", "c1")]))

    result = kf.run(Request("write it"))

    directory = _session_dir(cfg, result.session_id)
    assert (directory / PAUSED).is_file()
    assert (directory / PAUSED_PROVENANCE).is_file()
    assert session_bytes(directory) >= (directory / PAUSED).stat().st_size > 0


def test_an_ordinary_turn_leaves_no_pause_behind(cfg):
    """The file's presence *is* the mark that a session is waiting, so a turn that
    ended on its own leaving one would make every later turn think it was gated.
    """
    kf = Kingfisher(cfg, graph=_gated(calls=[], after="nothing to gate"))

    result = kf.run(Request("just answer"))

    assert result.stop_reason == END_TURN
    assert not (_session_dir(cfg, result.session_id) / PAUSED).exists()
    assert not (_session_dir(cfg, result.session_id) / PAUSED_PROVENANCE).exists()


def test_a_paused_turn_keeps_its_session_even_when_asked_to_delete_it(cfg):
    """`delete_session=True` on a turn that stopped for an answer would throw away the
    thing the answer is for.
    """
    kf = Kingfisher(cfg, graph=_gated(calls=[_write("/derived/a.txt", "x", "c1")]))

    result = kf.run(Request("write it"), delete_session=True)

    assert _session_dir(cfg, result.session_id).is_dir()
    assert result.deletion_failure is None, "keeping it was reported as a failure to delete"


# -- answering -------------------------------------------------------------


def test_approving_runs_the_call_and_finishes_the_turn(cfg, session_dir):
    """The whole point: the gated call runs *after* the answer, in a graph built a
    second time from state that went to disk and came back.
    """
    kf = Kingfisher(
        cfg,
        graph=_gated_on_disk(cfg, session_dir, calls=[_write("/derived/a.txt", "approved", "c1")]),
    )
    paused = kf.run(Request("write it", session_id=session_dir.name))

    answered = kf.run(
        Resume(
            session_id=paused.session_id,
            decisions=(Decision(call_id=paused.pending[0].call_id, action="approve"),),
        )
    )

    assert answered.stop_reason == END_TURN
    assert answered.pending == ()
    assert (_session_dir(cfg, paused.session_id) / "derived" / "a.txt").read_text() == "approved"


def test_rejecting_does_not_run_the_call(cfg, session_dir):
    """The half that a test asserting only on the turn's end would miss: a rejected
    gate still finishes the turn, and the difference is whether the file is there.
    """
    kf = Kingfisher(
        cfg,
        graph=_gated_on_disk(cfg, session_dir, calls=[_write("/derived/a.txt", "nope", "c1")]),
    )
    paused = kf.run(Request("write it", session_id=session_dir.name))

    answered = kf.run(
        Resume(
            session_id=paused.session_id,
            decisions=(
                Decision(call_id=paused.pending[0].call_id, action="reject", message="no"),
            ),
        )
    )

    assert answered.stop_reason == END_TURN
    assert not (_session_dir(cfg, paused.session_id) / "derived" / "a.txt").exists()


def test_answering_clears_the_pause(cfg):
    """Or the next ordinary turn would think it was superseding a live gate."""
    kf = Kingfisher(cfg, graph=_gated(calls=[_write("/derived/a.txt", "x", "c1")]))
    paused = kf.run(Request("write it"))

    kf.run(
        Resume(
            session_id=paused.session_id,
            decisions=(Decision(call_id=paused.pending[0].call_id, action="approve"),),
        )
    )

    assert not (_session_dir(cfg, paused.session_id) / PAUSED).exists()
    assert read_pause_mark(_session_dir(cfg, paused.session_id)) is None


def test_two_gated_calls_are_answered_one_each(cfg, session_dir):
    """One interrupt covers every gated call in a message, so a caller that could only
    answer the interrupt as a whole could not reject one write and approve another.
    """
    kf = Kingfisher(
        cfg,
        graph=_gated_on_disk(
            cfg,
            session_dir,
            calls=[_write("/derived/yes.txt", "kept", "c1"), _write("/derived/no.txt", "x", "c2")],
        ),
    )
    paused = kf.run(Request("write both", session_id=session_dir.name))
    assert len(paused.pending) == 2, "the two calls did not arrive as two decisions"

    by_file = {p.args["file_path"]: p.call_id for p in paused.pending}
    kf.run(
        Resume(
            session_id=paused.session_id,
            decisions=(
                Decision(call_id=by_file["/derived/yes.txt"], action="approve"),
                Decision(call_id=by_file["/derived/no.txt"], action="reject"),
            ),
        )
    )

    derived = _session_dir(cfg, paused.session_id) / "derived"
    assert derived.joinpath("yes.txt").read_text() == "kept"
    assert not derived.joinpath("no.txt").exists()


# -- refusing --------------------------------------------------------------


def test_a_resume_for_a_session_that_is_not_waiting_is_refused(cfg):
    kf = Kingfisher(cfg, graph=_gated(calls=[], after="ok"))
    ran = kf.run(Request("just answer"))

    with pytest.raises(DecisionError, match="not waiting"):
        kf.run(Resume(session_id=ran.session_id, decisions=(Decision("nope", "approve"),)))


def test_an_id_nothing_is_waiting_on_is_refused(cfg):
    """Named, rather than counted two frames later by langgraph as a length mismatch."""
    kf = Kingfisher(cfg, graph=_gated(calls=[_write("/derived/a.txt", "x", "c1")]))
    paused = kf.run(Request("write it"))

    with pytest.raises(DecisionError, match="invented"):
        kf.run(
            Resume(
                session_id=paused.session_id,
                decisions=(Decision(call_id="invented", action="approve"),),
            )
        )


def test_leaving_a_gated_call_unanswered_is_refused(cfg):
    """Half an answer would run the approved call and leave the other one hanging."""
    kf = Kingfisher(
        cfg,
        graph=_gated(
            calls=[_write("/derived/a.txt", "x", "c1"), _write("/derived/b.txt", "y", "c2")]
        ),
    )
    paused = kf.run(Request("write both"))

    with pytest.raises(DecisionError, match="still waiting"):
        kf.run(
            Resume(
                session_id=paused.session_id,
                decisions=(Decision(call_id=paused.pending[0].call_id, action="approve"),),
            )
        )


def test_responding_with_nothing_to_respond_is_refused(cfg):
    """`respond` replaces the tool's result, so an empty one is a tool that returned
    nothing rather than a decision.
    """
    kf = Kingfisher(cfg, graph=_gated(calls=[_write("/derived/a.txt", "x", "c1")]))
    paused = kf.run(Request("write it"))

    with pytest.raises(DecisionError, match="nothing to respond"):
        kf.run(
            Resume(
                session_id=paused.session_id,
                decisions=(Decision(call_id=paused.pending[0].call_id, action="respond"),),
            )
        )


def test_a_resume_naming_a_different_agent_is_refused(cfg):
    """A different agent is a different graph, whose nodes this checkpoint's are not."""
    kf = Kingfisher(cfg, graph=_gated(calls=[_write("/derived/a.txt", "x", "c1")]))
    paused = kf.run(Request("write it"))

    with pytest.raises(DecisionError, match="paused under agent"):
        kf.run(
            Resume(
                session_id=paused.session_id,
                agent="somebody-else",
                decisions=(Decision(call_id=paused.pending[0].call_id, action="approve"),),
            )
        )


def test_a_pause_that_did_not_survive_an_upgrade_is_refused(cfg):
    """A paused session outliving a deploy is ordinary. Checked before the load, so it
    is one sentence rather than a deserialiser's traceback about an unknown node.
    """
    kf = Kingfisher(cfg, graph=_gated(calls=[_write("/derived/a.txt", "x", "c1")]))
    paused = kf.run(Request("write it"))
    directory = _session_dir(cfg, paused.session_id)
    written = read_pause_mark(directory)
    assert written is not None, "the pause recorded nothing to move under it"
    write_pause_mark(directory, {**written, "deepagents": "0.0.1-before"})

    with pytest.raises(DecisionError, match="did not survive the upgrade"):
        kf.run(
            Resume(
                session_id=paused.session_id,
                decisions=(Decision(call_id=paused.pending[0].call_id, action="approve"),),
            )
        )


def test_a_refused_resume_leaves_the_pause_answerable(cfg, session_dir):
    """A bad answer must not consume the question it got wrong."""
    kf = Kingfisher(
        cfg,
        graph=_gated_on_disk(cfg, session_dir, calls=[_write("/derived/a.txt", "kept", "c1")]),
    )
    paused = kf.run(Request("write it", session_id=session_dir.name))
    with pytest.raises(DecisionError):
        kf.run(Resume(session_id=paused.session_id, decisions=(Decision("invented", "approve"),)))

    answered = kf.run(
        Resume(
            session_id=paused.session_id,
            decisions=(Decision(call_id=paused.pending[0].call_id, action="approve"),),
        )
    )

    assert answered.stop_reason == END_TURN
    assert (_session_dir(cfg, paused.session_id) / "derived" / "a.txt").read_text() == "kept"


# -- superseding -----------------------------------------------------------


def test_a_new_request_supersedes_the_pause_and_says_so(cfg):
    """A gate that quietly stops being a gate is the one failure here that would
    otherwise leave no trace anywhere.
    """
    kf = Kingfisher(cfg, graph=_gated(calls=[_write("/derived/a.txt", "x", "c1")]))
    paused = kf.run(Request("write it"))

    kf.graph = _gated(calls=[], after="something else")
    events = list(kf.stream(Request("never mind", session_id=paused.session_id)))
    superseded = next(e.result for e in events if e.kind == "finished")

    assert "decision_discarded" in [e.kind for e in events]
    assert superseded.discarded == ("write_file",)
    assert not (_session_dir(cfg, paused.session_id) / PAUSED).exists()
    assert not (_session_dir(cfg, paused.session_id) / "derived" / "a.txt").exists()


def test_superseding_is_silent_where_there_was_no_pause(cfg):
    """Every ordinary turn takes this path, so a false positive would be on all of them."""
    kf = Kingfisher(cfg, graph=_gated(calls=[], after="ok"))
    first = kf.run(Request("one"))

    again = kf.run(Request("two", session_id=first.session_id))

    assert again.discarded == ()


# -- with the conversation off --------------------------------------------


def test_a_stateless_deployment_writes_no_pause(cfg):
    """No checkpointer means no state to keep, and a mark written with nothing behind
    it would make every later turn on that session refuse to resume something that
    was never there.
    """
    stateless = replace_cfg(cfg, conversation_enabled=False)
    kf = Kingfisher(stateless, graph=_gated(calls=[_write("/derived/a.txt", "x", "c1")]))

    result = kf.run(Request("write it"))

    assert not (_session_dir(cfg, result.session_id) / PAUSED).exists()
    assert not (_session_dir(cfg, result.session_id) / PAUSED_PROVENANCE).exists()


# -- and when nobody ever answers -----------------------------------------
#
# A pause has no expiry of its own. It ages like any idle session and leaves with
# the retention sweep, which holds only because the checkpoint is kept *inside* the
# session -- so these are really tests of where it lives. Put it anywhere else and
# both go red, which is the orphan the old per-session sqlite left behind: one real
# workspace held 132 threads and 1,894 checkpoints after every session was reaped.


def test_a_pause_nobody_answers_is_swept_with_its_session(cfg):
    """Nothing special-cases a waiting session, and nothing should: a second timer
    for pending decisions is a second thing to disagree with the first.
    """
    import time

    kf = Kingfisher(cfg, graph=_gated(calls=[_write("/derived/a.txt", "x", "c1")]))
    paused = kf.run(Request("write it"))
    directory = _session_dir(cfg, paused.session_id)
    assert (directory / PAUSED).is_file(), "nothing was left waiting, so nothing is swept"

    swept = kf.reap(older_than_seconds=0, now=time.time())

    assert paused.session_id in swept.removed
    assert not directory.exists()
    assert swept.orphans == (), "the pause was reaped as residue rather than with its session"
    assert swept.failures == ()


def test_deleting_a_waiting_session_takes_the_pause_with_it(cfg):
    """The explicit half of the same thing. `delete_session=True` on the turn that
    paused declines -- the session is what the answer is for -- so this is the path
    somebody takes once they have decided not to answer after all.
    """
    kf = Kingfisher(cfg, graph=_gated(calls=[_write("/derived/a.txt", "x", "c1")]))
    paused = kf.run(Request("write it"))
    directory = _session_dir(cfg, paused.session_id)

    assert kf.delete_session(paused.session_id) is None

    assert not directory.exists()
    assert not (directory / PAUSED).exists()
