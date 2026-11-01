"""A turn that stops for a person, and the turn that answers it.

Driven through the service with a real gated agent rather than a stub that reports a
pause. The pause has to survive being written out, read back, and re-entered by a
graph built a second time -- none of which a stub exercises, and all of which is what
the feature is.
"""

from __future__ import annotations

import json
from dataclasses import replace as replace_cfg
from typing import Any

import pytest
from deepagents import create_deep_agent
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from kingfisher import Kingfisher
from kingfisher.core.access.capabilities import Capabilities
from kingfisher.core.layout import HARNESS, PAUSED_MARK, PAUSED_STATE, TRANSCRIPT_FILE
from kingfisher.core.request import Decision, DecisionError, Request, Resume
from kingfisher.core.result import AWAITING, DECISIONS, END_TURN
from kingfisher.core.session.steps import drive
from kingfisher.core.session.store import read_pause_mark, write_pause_mark
from kingfisher.plugins import LocalSessions, backend_at
from kingfisher.plugins.sessions.folders import session_bytes
from kingfisher.plugins.sessions.permissions import writable_inputs
from tests.conftest import FakeToolCallingModel, Through, harness_of

PAUSED = f"{HARNESS}/{PAUSED_STATE}"
PAUSED_PROVENANCE = f"{HARNESS}/{PAUSED_MARK}"


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

    Worth the extra wiring in the tests that use it. Without a real backend the
    file tools write into deepagents' in-state filesystem, where an approved call and
    a rejected one leave the session directory looking identical -- so the assertion
    that tells them apart would be asserting on nothing.
    """
    return create_deep_agent(
        model=FakeToolCallingModel(
            responses=[AIMessage(content="", tool_calls=calls), AIMessage(content=after)]
        ),
        backend=backend_at(cfg, session_dir),
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
    kf = Kingfisher(
        cfg, graph=_gated(calls=[_write("/outputs/a.txt", "x", "c1")]), sessions=LocalSessions()
    )

    result = kf.run(Request("write it"))

    assert result.stop_reason == AWAITING
    assert not result.completed, "a turn waiting on a person reported itself finished"
    assert [(p.tool, p.args["file_path"]) for p in result.pending] == [
        ("write_file", "/outputs/a.txt")
    ]
    assert result.pending[0].call_id, "a pending call with no id cannot be answered"


def test_the_offered_decisions_exclude_edit(cfg):
    """deepagents offers all four for a bare `True` gate, `edit` included -- and `edit`
    is the one that lets the answering caller author a call rather than judge one.
    """
    kf = Kingfisher(
        cfg, graph=_gated(calls=[_write("/outputs/a.txt", "x", "c1")]), sessions=LocalSessions()
    )

    offered = kf.run(Request("write it")).pending[0].decisions

    assert "edit" not in offered
    assert set(offered) <= set(DECISIONS)
    assert "approve" in offered, "nothing could be approved, so the gate is unanswerable"


def test_a_gated_turn_emits_the_event_before_it_finishes(cfg):
    """`run` drains the stream, so a caller watching one must hear it there too."""
    kf = Kingfisher(
        cfg, graph=_gated(calls=[_write("/outputs/a.txt", "x", "c1")]), sessions=LocalSessions()
    )

    kinds = [event.kind for event in kf.stream(Request("write it"))]

    assert "decision_needed" in kinds
    assert kinds.index("decision_needed") < kinds.index("finished")


def test_the_pause_is_kept_inside_the_session(cfg):
    """It has to die with the session and be counted by the quota, which a file
    anywhere else would not be -- and `.harness` is the one place the agent's file
    tools are refused.
    """
    kf = Kingfisher(
        cfg, graph=_gated(calls=[_write("/outputs/a.txt", "x", "c1")]), sessions=LocalSessions()
    )

    result = kf.run(Request("write it"))

    directory = _session_dir(cfg, result.session_id)
    assert (directory / PAUSED).is_file()
    assert (directory / PAUSED_PROVENANCE).is_file()
    assert session_bytes(directory) >= (directory / PAUSED).stat().st_size > 0


def test_an_ordinary_turn_leaves_no_pause_behind(cfg):
    """The file's presence *is* the mark that a session is waiting, so a turn that
    ended on its own leaving one would make every later turn think it was gated.
    """
    kf = Kingfisher(cfg, graph=_gated(calls=[], after="nothing to gate"), sessions=LocalSessions())

    result = kf.run(Request("just answer"))

    assert result.stop_reason == END_TURN
    assert not (_session_dir(cfg, result.session_id) / PAUSED).exists()
    assert not (_session_dir(cfg, result.session_id) / PAUSED_PROVENANCE).exists()


def test_a_paused_turn_keeps_its_session_even_when_asked_to_delete_it(cfg):
    """`delete_session=True` on a turn that stopped for an answer would throw away the
    thing the answer is for.
    """
    kf = Kingfisher(
        cfg, graph=_gated(calls=[_write("/outputs/a.txt", "x", "c1")]), sessions=LocalSessions()
    )

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
        graph=_gated_on_disk(cfg, session_dir, calls=[_write("/outputs/a.txt", "approved", "c1")]),
        sessions=LocalSessions(),
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
    assert (_session_dir(cfg, paused.session_id) / "outputs" / "a.txt").read_text() == "approved"


def test_a_paused_turn_writes_nothing_its_resume_will_not_rebuild(cfg, session_dir):
    """A paused state is read with langgraph's safe types only, and a type outside them
    comes back as its raw fields with a warning on stderr, not as the object it was.
    """
    from langgraph.checkpoint.serde.event_hooks import register_serde_event_listener

    events: list[Any] = []
    unregister = register_serde_event_listener(events.append)
    try:
        kf = Kingfisher(
            cfg,
            graph=_gated_on_disk(cfg, session_dir, calls=[_write("/outputs/a.txt", "ok", "c1")]),
            sessions=LocalSessions(),
        )
        paused = kf.run(Request("write it", session_id=session_dir.name))
        answered = kf.run(
            Resume(
                session_id=paused.session_id,
                decisions=(Decision(call_id=paused.pending[0].call_id, action="approve"),),
            )
        )
    finally:
        unregister()

    assert answered.stop_reason == END_TURN, "the paused turn did not resume"
    assert events == [], "a paused turn holds a type its resume refuses to rebuild"


def test_a_paused_pre_built_graph_resumes_in_a_graph_built_after_a_restart(cfg, session_dir):
    """A supplied graph paused into the saver it was compiled with, which lives as long
    as the process; the pause file beside it held kingfisher's saver, which that graph
    never ran on. Driven with a second graph, as a restart builds one, and judged by the
    approved write landing: a graph resumed into nothing answers and writes nothing.
    """
    before = Kingfisher(
        cfg,
        graph=_gated_on_disk(cfg, session_dir, calls=[_write("/outputs/a.txt", "approved", "c1")]),
        sessions=LocalSessions(),
    )
    paused = before.run(Request("write it", session_id=session_dir.name))
    after = Kingfisher(
        cfg,
        graph=create_deep_agent(
            model=FakeToolCallingModel(responses=[AIMessage(content="written after all")]),
            backend=backend_at(cfg, session_dir),
            tools=None,
            interrupt_on={"write_file": True},
            checkpointer=InMemorySaver(),
        ),
        sessions=LocalSessions(),
    )

    answered = after.run(
        Resume(
            session_id=paused.session_id,
            decisions=(Decision(call_id=paused.pending[0].call_id, action="approve"),),
        )
    )

    assert answered.stop_reason == END_TURN
    assert (_session_dir(cfg, paused.session_id) / "outputs" / "a.txt").read_text() == "approved"


def test_rejecting_does_not_run_the_call(cfg, session_dir):
    """The half that a test asserting only on the turn's end would miss: a rejected
    gate still finishes the turn, and the difference is whether the file is there.
    """
    kf = Kingfisher(
        cfg,
        graph=_gated_on_disk(cfg, session_dir, calls=[_write("/outputs/a.txt", "nope", "c1")]),
        sessions=LocalSessions(),
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
    assert not (_session_dir(cfg, paused.session_id) / "outputs" / "a.txt").exists()


def test_answering_clears_the_pause(cfg):
    """Or the next ordinary turn would think it was superseding a live gate."""
    kf = Kingfisher(
        cfg, graph=_gated(calls=[_write("/outputs/a.txt", "x", "c1")]), sessions=LocalSessions()
    )
    paused = kf.run(Request("write it"))

    kf.run(
        Resume(
            session_id=paused.session_id,
            decisions=(Decision(call_id=paused.pending[0].call_id, action="approve"),),
        )
    )

    assert not (_session_dir(cfg, paused.session_id) / PAUSED).exists()
    assert drive(read_pause_mark(harness_of(cfg, paused.session_id))) is None


@pytest.mark.parametrize("rejection_first", [False, True], ids=["gate order", "rejection first"])
def test_two_gated_calls_are_answered_one_each(cfg, session_dir, rejection_first):
    """One interrupt covers every gated call in a message, so a caller that could only
    answer the interrupt as a whole could not reject one write and approve another.

    Driven in both orders the pair may be written in, because langgraph takes the
    answers as a list positional against its own `action_requests` and never reads
    the ids back off them: left in the caller's order, the approval is carried out on
    the call the person rejected and the rejection on the one they approved.
    """
    kf = Kingfisher(
        cfg,
        graph=_gated_on_disk(
            cfg,
            session_dir,
            calls=[_write("/outputs/yes.txt", "kept", "c1"), _write("/outputs/no.txt", "x", "c2")],
        ),
        sessions=LocalSessions(),
    )
    paused = kf.run(Request("write both", session_id=session_dir.name))
    assert len(paused.pending) == 2, "the two calls did not arrive as two decisions"

    by_file = {p.args["file_path"]: p.call_id for p in paused.pending}
    answers = (
        Decision(call_id=by_file["/outputs/yes.txt"], action="approve"),
        Decision(call_id=by_file["/outputs/no.txt"], action="reject"),
    )
    kf.run(
        Resume(
            session_id=paused.session_id,
            decisions=answers[::-1] if rejection_first else answers,
        )
    )

    outputs = _session_dir(cfg, paused.session_id) / "outputs"
    assert outputs.joinpath("yes.txt").read_text() == "kept"
    assert not outputs.joinpath("no.txt").exists()


# -- refusing --------------------------------------------------------------


def test_a_resume_for_a_session_that_is_not_waiting_is_refused(cfg):
    kf = Kingfisher(cfg, graph=_gated(calls=[], after="ok"), sessions=LocalSessions())
    ran = kf.run(Request("just answer"))

    with pytest.raises(DecisionError, match="not waiting"):
        kf.run(Resume(session_id=ran.session_id, decisions=(Decision("nope", "approve"),)))


def test_an_id_nothing_is_waiting_on_is_refused(cfg):
    """Named, rather than counted two frames later by langgraph as a length mismatch."""
    kf = Kingfisher(
        cfg, graph=_gated(calls=[_write("/outputs/a.txt", "x", "c1")]), sessions=LocalSessions()
    )
    paused = kf.run(Request("write it"))

    with pytest.raises(DecisionError, match="invented"):
        kf.run(
            Resume(
                session_id=paused.session_id,
                decisions=(Decision(call_id="invented", action="approve"),),
            )
        )


def test_a_decision_the_gate_did_not_offer_is_refused(cfg):
    """`edit` is the decision that lets the answering caller author a call rather than
    judge the one proposed, and `DECISIONS` withholds it -- but leaving it out of what
    a gate *reports* does not stop a caller sending it, and the translation forwards an
    action it does not recognise verbatim.
    """
    kf = Kingfisher(
        cfg, graph=_gated(calls=[_write("/outputs/a.txt", "x", "c1")]), sessions=LocalSessions()
    )
    paused = kf.run(Request("write it"))

    with pytest.raises(DecisionError, match="takes approve, reject, respond -- not 'edit'"):
        kf.run(
            Resume(
                session_id=paused.session_id,
                decisions=(
                    Decision(call_id=paused.pending[0].call_id, action="edit", message="mine"),
                ),
            )
        )


def test_leaving_a_gated_call_unanswered_is_refused(cfg):
    """Half an answer would run the approved call and leave the other one hanging."""
    kf = Kingfisher(
        cfg,
        graph=_gated(
            calls=[_write("/outputs/a.txt", "x", "c1"), _write("/outputs/b.txt", "y", "c2")]
        ),
        sessions=LocalSessions(),
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
    kf = Kingfisher(
        cfg, graph=_gated(calls=[_write("/outputs/a.txt", "x", "c1")]), sessions=LocalSessions()
    )
    paused = kf.run(Request("write it"))

    with pytest.raises(DecisionError, match="nothing to respond"):
        kf.run(
            Resume(
                session_id=paused.session_id,
                decisions=(Decision(call_id=paused.pending[0].call_id, action="respond"),),
            )
        )


def _under_a_policy(cfg):
    """A workspace with two locked agents, which is what makes a session have a
    *resolved* agent at all.

    An injected graph where nothing is locked resolves none -- setup says so in as many
    words -- so a session built that way has no agent for a resume to name a different
    one from, and the rules below would assert on nothing.
    """
    from tests.conftest import an_agent

    # Locked, because setup resolves an agent only where a graph was not injected *or*
    # something is locked, and these rules inject one.
    for name in ("analyst", "somebody-else"):
        an_agent(cfg, name, source_ids="[A]")
    return cfg


def _paused_on_a_pinned_session(cfg, call_id: str):
    """Open a session on `analyst`, then pause a *later* turn, which names no agent.

    The later turn is the point: a session's agent is fixed when it opens, so every turn
    after the first names nothing. A pause recorded on one of those is the case that was
    broken, and a pause on the opening turn is not -- which is why the rules below do not
    use one.
    """
    opening = Kingfisher(
        cfg,
        graph=create_deep_agent(
            model=FakeToolCallingModel(responses=[AIMessage(content="hi")]),
            tools=None,
            checkpointer=InMemorySaver(),
        ),
        sessions=LocalSessions(),
    )
    first = opening.run(Request("say hi", agent="analyst"), source_ids=("A",))
    kf = Kingfisher(
        cfg, graph=_gated(calls=[_write("/outputs/a.txt", "x", call_id)]), sessions=LocalSessions()
    )
    paused = kf.run(Request("write it", session_id=first.session_id), source_ids=("A",))
    return kf, paused


def test_a_pause_records_the_agent_the_turn_actually_ran(cfg):
    """It recorded what the *request* named, which is empty on every turn after the one
    that opened the session -- so the mark said `none` for a session plainly running
    something, and `_refuse_stale_pause` compared every resume against nothing.
    """
    policied = _under_a_policy(cfg)
    _kf, paused = _paused_on_a_pinned_session(policied, "c1")

    written = drive(read_pause_mark(harness_of(policied, paused.session_id))) or {}

    assert written.get("agent") == "analyst"


def test_a_resume_naming_the_agent_the_session_runs_is_accepted(cfg):
    """The half that was missing, and the reason the bug survived: with the mark empty
    this was refused as `paused under agent 'none'` -- the correct name turned away.
    """
    policied = _under_a_policy(cfg)
    kf, paused = _paused_on_a_pinned_session(policied, "c1")

    done = kf.run(
        Resume(
            session_id=paused.session_id,
            agent="analyst",
            decisions=(Decision(call_id=paused.pending[0].call_id, action="approve"),),
        ),
        source_ids=("A",),
    )

    assert done.stop_reason == END_TURN


def test_a_resume_naming_a_different_agent_is_refused(cfg):
    """A different agent is a different graph, whose nodes this checkpoint's are not.

    Driven on a session with a pinned agent, so the refusal is a real mismatch. It used
    to run on an injected graph under no policy, where nothing was resolved and nothing
    recorded -- so it passed against a mark of `none`, which is to say it passed for
    every name including the right one.
    """
    policied = _under_a_policy(cfg)
    kf, paused = _paused_on_a_pinned_session(policied, "c1")

    with pytest.raises(DecisionError, match="paused under agent 'analyst'"):
        kf.run(
            Resume(
                session_id=paused.session_id,
                agent="somebody-else",
                decisions=(Decision(call_id=paused.pending[0].call_id, action="approve"),),
            ),
            source_ids=("A",),
        )


def test_a_resume_naming_nothing_is_not_a_mismatch(cfg):
    """What a caller normally sends. The agent is already fixed by the session, so there
    is nothing to disagree with, and the pause check has nothing to say about it.
    """
    policied = _under_a_policy(cfg)
    kf, paused = _paused_on_a_pinned_session(policied, "c1")

    done = kf.run(
        Resume(
            session_id=paused.session_id,
            decisions=(Decision(call_id=paused.pending[0].call_id, action="approve"),),
        ),
        source_ids=("A",),
    )

    assert done.stop_reason == END_TURN


def test_an_injected_graph_under_no_policy_has_no_agent_to_disagree_with(cfg):
    """Setup resolves no agent for a deployment that supplied its own graph and
    declares no policy, so a pause records none -- and a resume naming one is then not a
    mismatch, because there is nothing for it to mismatch.

    The parity is the point: a *Request* naming an agent on such a session is accepted,
    and the resume was refused, so the same name meant two different things depending on
    which call carried it.
    """
    kf = Kingfisher(
        cfg, graph=_gated(calls=[_write("/outputs/a.txt", "x", "c1")]), sessions=LocalSessions()
    )
    paused = kf.run(Request("write it", agent="whatever"))

    assert (drive(read_pause_mark(harness_of(cfg, paused.session_id))) or {}).get("agent") == ""

    done = kf.run(
        Resume(
            session_id=paused.session_id,
            agent="whatever",
            decisions=(Decision(call_id=paused.pending[0].call_id, action="approve"),),
        )
    )

    assert done.stop_reason == END_TURN


def test_a_pause_that_did_not_survive_an_upgrade_is_refused(cfg):
    """A paused session outliving a deploy is ordinary. Checked before the load, so it
    is one sentence rather than a deserialiser's traceback about an unknown node.
    """
    kf = Kingfisher(
        cfg, graph=_gated(calls=[_write("/outputs/a.txt", "x", "c1")]), sessions=LocalSessions()
    )
    paused = kf.run(Request("write it"))
    kept = harness_of(cfg, paused.session_id)
    written = drive(read_pause_mark(kept))
    assert written is not None, "the pause recorded nothing to move under it"
    drive(write_pause_mark(kept, {**written, "deepagents": "0.0.1-before"}))

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
        graph=_gated_on_disk(cfg, session_dir, calls=[_write("/outputs/a.txt", "kept", "c1")]),
        sessions=LocalSessions(),
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
    assert (_session_dir(cfg, paused.session_id) / "outputs" / "a.txt").read_text() == "kept"


# -- superseding -----------------------------------------------------------


def test_a_new_request_supersedes_the_pause_and_says_so(cfg, session_dir):
    """A gate that quietly stops being a gate is the one failure here that would
    otherwise leave no trace anywhere.

    On the real backend, with the next test as its control: in deepagents' in-state
    filesystem a write carried out and a write discarded both leave no file.
    """
    kf = Kingfisher(
        cfg,
        graph=_gated_on_disk(cfg, session_dir, calls=[_write("/outputs/a.txt", "x", "c1")]),
        sessions=LocalSessions(),
    )
    paused = kf.run(Request("write it", session_id=session_dir.name))

    events = list(kf.stream(Request("never mind", session_id=paused.session_id)))
    superseded = next(e.result for e in events if e.kind == "finished")

    assert "decision_discarded" in [e.kind for e in events]
    assert superseded.discarded == ("write_file",)
    assert not (_session_dir(cfg, paused.session_id) / PAUSED).exists()
    assert not (_session_dir(cfg, paused.session_id) / "outputs" / "a.txt").exists()


def test_the_write_a_supersede_discards_lands_when_approved(cfg, session_dir):
    """Without it the missing file above could be a setup that writes nowhere."""
    kf = Kingfisher(
        cfg,
        graph=_gated_on_disk(cfg, session_dir, calls=[_write("/outputs/a.txt", "x", "c1")]),
        sessions=LocalSessions(),
    )
    paused = kf.run(Request("write it", session_id=session_dir.name))

    kf.run(
        Resume(
            session_id=paused.session_id,
            decisions=(Decision(call_id=paused.pending[0].call_id, action="approve"),),
        )
    )

    assert (_session_dir(cfg, paused.session_id) / "outputs" / "a.txt").read_text() == "x"


def test_a_refused_request_leaves_the_pause_it_would_supersede(cfg, session_dir):
    """Setup dropped the pause before the build's refusals had run, so a request refused
    there destroyed a waiting gate with no turn to report `decision_discarded`.
    """
    kf = Kingfisher(
        cfg,
        graph=_gated_on_disk(cfg, session_dir, calls=[_write("/outputs/a.txt", "kept", "c1")]),
        sessions=LocalSessions(),
    )
    paused = kf.run(Request("write it", session_id=session_dir.name))

    with pytest.raises(ValueError, match="pre-built graph"):
        kf.run(
            Request("never mind", session_id=paused.session_id, capabilities=Capabilities(tools=()))
        )

    answered = kf.run(
        Resume(
            session_id=paused.session_id,
            decisions=(Decision(call_id=paused.pending[0].call_id, action="approve"),),
        )
    )
    assert answered.stop_reason == END_TURN
    assert (_session_dir(cfg, paused.session_id) / "outputs" / "a.txt").read_text() == "kept"


def test_a_supersede_that_cannot_drop_the_pause_lets_go_of_the_built_turn(cfg, monkeypatch):
    """The drop runs after the build, so a backend failing it holds a graph no ending will
    release: its interpreter, and the session's claim, must be let go of there.
    """
    from kingfisher.core.session import preparation

    kf = Kingfisher(
        cfg, graph=_gated(calls=[_write("/outputs/a.txt", "x", "c1")]), sessions=LocalSessions()
    )
    paused = kf.run(Request("write it"))
    released: list[Any] = []

    def failing(harness):
        msg = "the session's backend kept the pause"
        raise OSError(msg)
        yield

    monkeypatch.setattr(preparation, "clear_pause", failing)
    monkeypatch.setattr(preparation, "release_interpreter", lambda _, graph: released.append(graph))
    with pytest.raises(OSError, match="kept the pause"):
        kf.run(Request("never mind", session_id=paused.session_id))
    monkeypatch.undo()

    assert len(released) == 1, "the turn's graph was built and never let go of"
    assert kf.run(Request("again", session_id=paused.session_id)).stop_reason == END_TURN


def test_a_report_failing_after_the_build_lets_go_of_the_built_graph(cfg, monkeypatch):
    """What setup reports of a graph is read after the build and before the turn holds
    it, so a failure there left a graph no ending would release, its interpreter held."""
    from kingfisher.core.session import preparation

    kf = Kingfisher(cfg, graph=_gated(calls=[], after="ok"), sessions=LocalSessions())
    released: list[Any] = []

    def failing(*_: Any, **__: Any) -> Any:
        msg = "the graph's tools could not be read"
        raise RuntimeError(msg)

    monkeypatch.setattr(preparation, "withheld_by_kind", failing)
    monkeypatch.setattr(preparation, "release_interpreter", lambda _, graph: released.append(graph))
    with pytest.raises(RuntimeError, match="could not be read"):
        kf.run(Request("hi"))
    monkeypatch.undo()

    assert len(released) == 1, "the graph was built and never let go of"


def test_a_refused_request_leaves_the_inputs_it_would_replace(cfg, tmp_path):
    """Setup copied a request's files into the session before the build's refusals had run,
    so a refused request overwrote the caller's earlier file and ran no turn.
    """
    kf = Kingfisher(cfg, graph=_gated(calls=[], after="ok"), sessions=LocalSessions())
    report = tmp_path / "report.txt"
    report.write_text("first", encoding="utf-8")
    first = kf.run(Request("read it", inputs=(report,)))
    report.write_text("second", encoding="utf-8")

    with pytest.raises(ValueError, match="pre-built graph"):
        kf.run(
            Request(
                "again",
                session_id=first.session_id,
                inputs=(report,),
                capabilities=Capabilities(tools=()),
            )
        )

    placed = _session_dir(cfg, first.session_id) / "inputs" / "report.txt"
    assert placed.read_text(encoding="utf-8") == "first"


def test_a_turn_that_runs_still_places_its_inputs_and_says_what_it_replaced(cfg, tmp_path):
    """The control for the rule above: placed after the build, a re-supplied file still
    lands before the graph runs and is still reported as replaced.
    """
    kf = Kingfisher(cfg, graph=_gated(calls=[], after="ok"), sessions=LocalSessions())
    report = tmp_path / "report.txt"
    report.write_text("first", encoding="utf-8")
    first = kf.run(Request("read it", inputs=(report,)))
    report.write_text("second", encoding="utf-8")

    events = list(kf.stream(Request("again", session_id=first.session_id, inputs=(report,))))

    (placed,) = [event for event in events if event.kind == "inputs_placed"]
    assert placed.text == "report.txt (1 replaced)"
    inputs = _session_dir(cfg, first.session_id) / "inputs" / "report.txt"
    assert inputs.read_text(encoding="utf-8") == "second"


def test_an_input_replaced_while_a_turn_waits_is_named_after_its_resume(cfg, session_dir):
    """A resume adds no message, so a resume that kept the inputs folder as it found it
    would leave the replacement named on no turn at all.
    """
    kf = Kingfisher(
        cfg,
        graph=_gated_on_disk(cfg, session_dir, calls=[_write("/outputs/a.txt", "x", "c1")]),
        sessions=LocalSessions(),
    )
    report = cfg.workspace / "report.txt"
    report.write_text("first", encoding="utf-8")
    paused = kf.run(Request("write it", session_id=session_dir.name, inputs=(report,)))
    with writable_inputs(session_dir, cfg.layout) as inputs:
        (inputs / "report.txt").write_text("second, and longer", encoding="utf-8")
    kf.run(
        Resume(
            session_id=paused.session_id,
            decisions=(Decision(call_id=paused.pending[0].call_id, action="approve"),),
        )
    )

    kf.run(Request("again", session_id=session_dir.name))

    transcript = (session_dir / HARNESS / TRANSCRIPT_FILE).read_text(encoding="utf-8")
    asked = [json.loads(line) for line in transcript.splitlines()]
    *_, last = (one["content"] for one in asked if one["role"] == "user")
    assert "Replaced in /inputs/: report.txt." in last


def test_a_supersede_whose_inputs_cannot_be_placed_leaves_the_pause_waiting(
    cfg, session_dir, tmp_path, monkeypatch
):
    """The upload runs after the build and before the pause is dropped: a backend refusing
    it must let go of the built graph and leave the gate to be answered, since a caller can
    send a file again and nobody can recreate a pause.
    """
    from kingfisher.core.session import preparation

    kf = Kingfisher(
        cfg,
        graph=_gated_on_disk(cfg, session_dir, calls=[_write("/outputs/a.txt", "kept", "c1")]),
        sessions=LocalSessions(),
    )
    paused = kf.run(Request("write it", session_id=session_dir.name))
    report = tmp_path / "report.txt"
    report.write_text("x", encoding="utf-8")
    released: list[Any] = []

    def failing(*_):
        msg = "the session's backend refused the inputs"
        raise OSError(msg)
        yield

    monkeypatch.setattr(preparation, "place_inputs", failing)
    monkeypatch.setattr(preparation, "release_interpreter", lambda _, graph: released.append(graph))
    with pytest.raises(OSError, match="refused the inputs"):
        kf.run(Request("never mind", session_id=paused.session_id, inputs=(report,)))
    monkeypatch.undo()

    assert len(released) == 1, "the turn's graph was built and never let go of"
    answered = kf.run(
        Resume(
            session_id=paused.session_id,
            decisions=(Decision(call_id=paused.pending[0].call_id, action="approve"),),
        )
    )
    assert answered.stop_reason == END_TURN


def test_superseding_is_silent_where_there_was_no_pause(cfg):
    """Every ordinary turn takes this path, so a false positive would be on all of them."""
    kf = Kingfisher(cfg, graph=_gated(calls=[], after="ok"), sessions=LocalSessions())
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
    kf = Kingfisher(
        stateless,
        graph=_gated(calls=[_write("/outputs/a.txt", "x", "c1")]),
        sessions=LocalSessions(),
    )

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

    kf = Kingfisher(
        cfg, graph=_gated(calls=[_write("/outputs/a.txt", "x", "c1")]), sessions=LocalSessions()
    )
    paused = kf.run(Request("write it"))
    directory = _session_dir(cfg, paused.session_id)
    assert (directory / PAUSED).is_file(), "nothing was left waiting, so nothing is swept"

    swept = kf.reap(now=time.time())

    assert paused.session_id in swept.removed
    assert not directory.exists()
    assert swept.failures == ()


def test_deleting_a_waiting_session_takes_the_pause_with_it(cfg):
    """The explicit half of the same thing. `delete_session=True` on the turn that
    paused declines -- the session is what the answer is for -- so this is the path
    somebody takes once they have decided not to answer after all.
    """
    kf = Kingfisher(
        cfg, graph=_gated(calls=[_write("/outputs/a.txt", "x", "c1")]), sessions=LocalSessions()
    )
    paused = kf.run(Request("write it"))
    directory = _session_dir(cfg, paused.session_id)

    assert Through(kf).delete(paused.session_id) is None

    assert not directory.exists()
    assert not (directory / PAUSED).exists()
