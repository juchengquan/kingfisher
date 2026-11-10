"""A turn stopped while its tools run: what still runs after the stop, and what the next
turn is told.

Through real turns, on a graph kingfisher built and a tool that counts what it did:
whether a call runs after a stop is decided by langgraph's executor and kingfisher's
middleware together, and a stub graph has neither.
"""

from __future__ import annotations

import asyncio
import contextvars
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import aclosing
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphInterrupt

from kingfisher import Kingfisher, Request
from kingfisher.core.agent.middlewares.stopping import NOT_RUN, STOPPING, StoppingMiddleware
from kingfisher.core.agent.tool_guards import guarded_tools
from kingfisher.core.config import Config
from kingfisher.core.session import running
from kingfisher.core.session.model import SessionBusyError
from kingfisher.core.session.turn import UNFINISHED
from kingfisher.plugins import LocalSessions, backend_at
from tests.conftest import RecordedEvents, an_agent, paths_in, start, tools_dir
from tests.unit.scripted import Scripted

#: More calls in one reply than langgraph's executor runs at once, so some are queued.
CALLS = 40

TOOLS = '''
import os
import time
from pathlib import Path


def _note(name: str, amount: str) -> None:
    with Path(os.environ[name]).open("a", encoding="utf-8") as kept:
        kept.write(amount + "\\n")


def charge(amount: str) -> str:
    """Charge the customer."""
    _note("KF_TEST_BEGUN", amount)
    if amount == "hold":
        released = Path(os.environ["KF_TEST_RELEASE"])
        deadline = time.monotonic() + 10
        while not released.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
    elif amount != "0":
        time.sleep(float(os.environ.get("KF_TEST_SLOW", "0.3")))
    _note("KF_TEST_CHARGES", amount)
    return f"charged {amount}; receipt R-{amount}"


async def charge_later(amount: str) -> str:
    """Charge the customer, from a tool that awaits its service."""
    import asyncio

    _note("KF_TEST_BEGUN", amount)
    await asyncio.sleep(0.5)
    _note("KF_TEST_CHARGES", amount)
    return f"charged {amount}; receipt R-{amount}"


class Broke(BaseException):
    """What no tool middleware converts, so the graph fails, as when a subagent's does."""


async def break_soon() -> str:
    """Fail the graph shortly."""
    import asyncio

    await asyncio.sleep(0.1)
    raise Broke


def break_now() -> str:
    """Fail the graph at once."""
    raise Broke


TOOLS = [charge, charge_later, break_soon, break_now]
'''


@dataclass(frozen=True)
class Charging:
    cfg: Any
    #: Each charge as it lands.
    charges: Path
    #: Each call as it begins.
    begun: Path
    #: Made to let a `hold` charge land.
    release: Path

    def charged(self) -> list[str]:
        return _lines(self.charges)

    def kf(self, cfg: Any = None) -> Kingfisher:
        return Kingfisher(cfg or self.cfg, sessions=LocalSessions())


@pytest.fixture
def charging(scripted, tmp_path, monkeypatch):
    """An agent holding `charge`, and the files that say what it began and what it did."""
    tools_dir(scripted).mkdir(parents=True, exist_ok=True)
    (tools_dir(scripted) / "charging.py").write_text(TOOLS, encoding="utf-8")
    an_agent(scripted, tools="[charge, charge_later, break_soon, break_now]")
    start(scripted, "s")
    made = Charging(
        scripted, tmp_path / "charges.txt", tmp_path / "begun.txt", tmp_path / "released"
    )
    monkeypatch.setenv("KF_TEST_CHARGES", str(made.charges))
    monkeypatch.setenv("KF_TEST_BEGUN", str(made.begun))
    monkeypatch.setenv("KF_TEST_RELEASE", str(made.release))
    return made


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").split() if path.exists() else []


def _asking(*amounts: str) -> AIMessage:
    """One reply asking for a charge of each amount together."""
    calls = [{"name": "charge", "args": {"amount": one}, "id": f"c-{one}"} for one in amounts]
    return AIMessage(content="", tool_calls=calls)


def _many() -> AIMessage:
    """One reply asking for `CALLS` charges together, the first of them instant."""
    return _asking(*(str(n) for n in range(CALLS)))


def _asked() -> Request:
    return Request("charge it", agent="only", session_id="s")


async def _until(check: Any, within: float = 10.0) -> None:
    gone = time.monotonic() + within
    while not check():
        assert time.monotonic() < gone, "waited too long"
        await asyncio.sleep(0.01)


def _broke(raised: BaseException) -> bool:
    """`Broke`, told by name: the tool module is loaded under a name of the loader's own."""
    return type(raised).__name__ == "Broke"


def _told_next(charging: Charging, call: str) -> ToolMessage:
    """What the next turn's model was handed as the answer to `call`."""
    Scripted.script.append(AIMessage(content="noted"))
    charging.kf().run(Request("did it go through?", agent="only", session_id="s"))
    handed = Scripted.heard[-1]
    answers = [m for m in handed if isinstance(m, ToolMessage) and m.tool_call_id == call]
    # Asserted, not `next()`: called on a thread, a miss would surface as asyncio's
    # complaint about `StopIteration`, which says nothing of what the model was handed.
    assert answers, (
        f"the next turn's model was handed no answer to {call!r}, only "
        f"{[(type(m).__name__, getattr(m, 'tool_call_id', None)) for m in handed]}"
    )
    return answers[0]


# -- a call not yet begun ------------------------------------------------------


def test_a_call_not_begun_when_a_turn_stops_is_not_run(charging):
    """Closing a turn's stream waited for every call the reply queued, and a queued call
    began then: 40 calls asked together, stopped with a few under way, charged 40 times.
    """
    Scripted.script.extend([_many(), AIMessage(content="done")])

    turn = charging.kf().stream(_asked())
    for event in turn:
        if event.kind == "tool_result":
            break
    turn.close()

    assert 0 < len(charging.charged()) < CALLS


def test_a_call_not_begun_when_an_awaited_turn_stops_is_not_run(charging):
    """An awaited turn waits for the calls its threads run, and every call of a reply
    has passed the middleware by then, queued for a thread: waiting without a check where
    the thread begins ran all 40."""
    Scripted.script.extend([_many(), AIMessage(content="done")])

    async def cancelled_once_some_began() -> None:
        # By time, not at an answer: an awaited graph hands over none until most have run.
        turn = asyncio.ensure_future(charging.kf().arun(_asked()))
        await _until(lambda: len(_lines(charging.begun)) > 1)
        turn.cancel()
        with pytest.raises(asyncio.CancelledError):
            await turn

    asyncio.run(cancelled_once_some_began())

    assert 0 < len(charging.charged()) < CALLS


@pytest.mark.parametrize("awaited", [False, True], ids=["run", "awaited"])
@pytest.mark.parametrize("stopped", [False, True], ids=["running", "stopping"])
def test_a_call_from_code_is_not_begun_once_its_turn_is_stopping(
    stopped, awaited, cfg, session_dir
):
    """`eval` and a compiled subagent call tools no middleware sees, so a program still
    running when its turn stopped went on starting calls. Running is the control."""
    charges: list[str] = []

    def charge(amount: str) -> str:
        """Charge the customer."""
        charges.append(amount)
        return f"charged {amount}"

    (guarded,) = guarded_tools([charge], paths_in(cfg, session_dir))
    flag = threading.Event()
    if stopped:
        flag.set()
    context = contextvars.copy_context()
    context.run(STOPPING.set, flag)

    async def in_the_turn() -> Any:
        return await asyncio.get_running_loop().create_task(
            guarded.ainvoke({"amount": "10"}), context=context
        )

    said = asyncio.run(in_the_turn()) if awaited else context.run(guarded.invoke, {"amount": "10"})

    assert charges == ([] if stopped else ["10"])
    assert (said == NOT_RUN) is stopped


class _Failing(BaseException):
    """What no middleware converts."""


@pytest.mark.parametrize("awaited", [False, True], ids=["run", "awaited"])
@pytest.mark.parametrize(
    ("raised", "stops"),
    [(GraphInterrupt(()), False), (_Failing(), True)],
    ids=["interrupt", "failure"],
)
def test_an_interrupt_passing_up_through_a_call_does_not_stop_its_turn(raised, stops, awaited):
    """A subagent pausing for approval raises through its `task` call, and read as a
    failure it would refuse every call beside it, each then kept as "did not run" for the
    resume. A failure is the control: on the loop, as one an inner middleware raises."""
    flag = threading.Event()
    context = contextvars.copy_context()
    context.run(STOPPING.set, flag)

    def handler(_: Any) -> Any:
        raise raised

    async def ahandler(_: Any) -> Any:
        raise raised

    with pytest.raises(type(raised)):
        if awaited:
            context.run(asyncio.run, StoppingMiddleware().awrap_tool_call(None, ahandler))
        else:
            context.run(StoppingMiddleware().wrap_tool_call, None, handler)

    assert flag.is_set() is stops


# -- a call a thread is running ------------------------------------------------


@pytest.mark.parametrize("cancels", [1, 40], ids=["cancelled once", "cancelled repeatedly"])
def test_a_cancelled_turn_waits_for_the_call_its_thread_runs_and_keeps_its_answer(
    charging, cancels
):
    """Cancelled, an awaited turn let go of a call a thread was running at once: the next
    turn was told the call did not complete, and its charge landed after. A web framework
    cancels again and again, and a second cancel broke out of langgraph's own wait."""
    Scripted.script.append(_asking("hold"))

    async def cancelled() -> list[str]:
        turn = asyncio.ensure_future(charging.kf().arun(_asked()))
        await _until(lambda: "hold" in _lines(charging.begun))
        asyncio.get_running_loop().call_later(0.2, charging.release.touch)
        for _ in range(cancels):
            turn.cancel()
            await asyncio.sleep(0.005)
        with pytest.raises(asyncio.CancelledError):
            await turn
        return charging.charged()

    assert asyncio.run(cancelled()) == ["hold"], "the cancel returned before the charge"
    told = _told_next(charging, "c-hold")
    assert told.status == "success"
    assert "receipt R-hold" in told.text


def test_an_async_call_cancelled_again_and_again_does_not_act(charging):
    """A second cancel broke out of langgraph's wait while it cancelled the turn's calls,
    so an awaited tool it had not yet reached went on and charged behind the next turn,
    as a web framework cancelling again and again does."""
    later = {"name": "charge_later", "args": {"amount": "7"}, "id": "c-later"}
    Scripted.script.append(AIMessage(content="", tool_calls=[later]))

    async def cancelled_repeatedly() -> None:
        turn = asyncio.ensure_future(charging.kf().arun(_asked()))
        await _until(lambda: "7" in _lines(charging.begun))
        while not turn.done():
            turn.cancel()
            await asyncio.sleep(0)
        with pytest.raises(asyncio.CancelledError):
            await turn
        await asyncio.sleep(0.8)

    asyncio.run(cancelled_repeatedly())

    assert charging.charged() == [], "the cancelled call charged"


def test_a_caller_that_stops_reading_stops_the_awaited_calls_too(charging, monkeypatch):
    """Left while the turn held answers its caller had not read, the graph's stream was
    held at its `yield` and never closed, so an awaited call it had started was never
    cancelled and charged behind the next turn."""
    monkeypatch.setenv("KF_TEST_SLOW", "0.05")
    later = {"name": "charge_later", "args": {"amount": "7"}, "id": "c-later"}
    asked = _asking("0", "1", "2")
    Scripted.script.append(AIMessage(content="", tool_calls=[*asked.tool_calls, later]))

    async def stopped_reading() -> None:
        async with aclosing(charging.kf().astream(_asked())) as events:
            async for event in events:
                if event.kind == "tool_result":
                    # Not reading, so the answers after this one wait to be handed over.
                    await asyncio.sleep(0.2)
                    break
        await asyncio.sleep(0.8)

    asyncio.run(stopped_reading())

    assert "7" not in charging.charged(), "the awaited call charged after its turn stopped"


def test_a_caller_that_stops_reading_waits_for_the_call_its_thread_runs(charging):
    """`aclose` let go of the graph at once, so a caller breaking out of `astream` left a
    call charging behind the next turn."""
    Scripted.script.append(_asking("0", "hold"))

    async def stopped_reading() -> list[str]:
        async with aclosing(charging.kf().astream(_asked())) as events:
            async for event in events:
                if event.kind == "tool_result":
                    asyncio.get_running_loop().call_later(0.2, charging.release.touch)
                    break
        return charging.charged()

    assert "hold" in asyncio.run(stopped_reading())


def test_a_cancel_reaching_a_caller_closing_its_turn_is_raised(charging):
    """A caller closing its turn waits for the call a thread runs, and a cancel reaching it
    then was swallowed: its task went on as if never cancelled, so a timeout around it
    never fired."""
    Scripted.script.append(_asking("0", "hold"))

    async def closing() -> None:
        async with aclosing(charging.kf().astream(_asked())) as events:
            async for event in events:
                if event.kind == "tool_result":
                    break

    async def cancelled() -> None:
        task = asyncio.ensure_future(closing())
        await _until(lambda: charging.charged() == ["0"])
        await asyncio.sleep(0.2)
        task.cancel()
        asyncio.get_running_loop().call_later(0.2, charging.release.touch)
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancelled())


def test_a_turn_out_of_time_answers_once_its_calls_have_ended(charging, monkeypatch):
    """Out of time, an awaited turn answered while a call was still running, so the next
    turn could start before its charge landed. Waits as the sync path does, past
    the cancel wait: the caller has not left."""
    cfg = replace(charging.cfg, turn_timeout_s=0.5, cancel_wait_s=0.1)
    monkeypatch.setenv("KF_TEST_SLOW", "0.8")
    Scripted.script.append(_asking("1", "hold"))

    async def out_of_time() -> tuple[bool, Any]:
        turn = asyncio.ensure_future(charging.kf(cfg).arun(_asked()))
        await _until(lambda: "1" in charging.charged())
        await asyncio.sleep(0.3)
        waiting = not turn.done()
        charging.release.touch()
        return waiting, await turn

    waiting, result = asyncio.run(out_of_time())

    assert waiting, "the turn answered with a call still running"
    assert result.stop_reason == "max_duration"
    assert "c-hold" in [entry.call for entry in result.ledger]


def test_a_turn_whose_graph_fails_raises_once_its_calls_have_ended(charging, monkeypatch):
    """A graph failing beside a call a thread runs raised out of the turn at once, and the
    call charged behind whatever the caller did next. Past the cancel wait: the caller has
    not left."""
    cfg = replace(charging.cfg, cancel_wait_s=0.1)
    broke = {"name": "break_soon", "args": {}, "id": "c-broke"}
    asked = _asking("hold")
    Scripted.script.append(AIMessage(content="", tool_calls=[*asked.tool_calls, broke]))

    async def failed() -> list[str]:
        asyncio.get_running_loop().call_later(0.4, charging.release.touch)
        with pytest.raises(BaseException, check=_broke):  # noqa: B017 -- `check` narrows it
            await charging.kf(cfg).arun(_asked())
        return charging.charged()

    assert asyncio.run(failed()) == ["hold"], "the turn raised before its call ended"


@pytest.mark.parametrize("awaited", [False, True], ids=["run", "awaited"])
def test_a_turn_whose_graph_fails_begins_none_of_the_calls_still_queued(charging, awaited):
    """A graph says it failed only once every call beside the failing one has ended, so
    the turn stopped too late: 40 calls asked beside a failing one all charged, those
    still waiting for a thread too."""
    broke = {"name": "break_now", "args": {}, "id": "c-broke"}
    asked = _asking(*(str(n) for n in range(1, CALLS + 1)))
    # First, so it fails while most of the charges still wait for a thread.
    Scripted.script.append(AIMessage(content="", tool_calls=[broke, *asked.tool_calls]))

    with pytest.raises(BaseException, check=_broke):  # noqa: B017 -- `check` narrows it
        if awaited:
            asyncio.run(charging.kf().arun(_asked()))
        else:
            charging.kf().run(_asked())

    assert len(charging.charged()) < CALLS


def test_a_caller_cancelling_a_turn_that_waits_out_of_time_is_let_go_of(charging, monkeypatch):
    """A turn out of time waits for its calls past the cancel wait, and a caller giving up
    on it then has left: it is let go after the wait, and hears its cancel."""
    cfg = replace(charging.cfg, turn_timeout_s=0.5, cancel_wait_s=0.2)
    monkeypatch.setenv("KF_TEST_SLOW", "0.8")
    Scripted.script.append(_asking("1", "hold"))

    async def given_up() -> float:
        turn = asyncio.ensure_future(charging.kf(cfg).arun(_asked()))
        await _until(lambda: "1" in charging.charged())
        await asyncio.sleep(0.1)
        cancelled_at = time.monotonic()
        turn.cancel()
        with pytest.raises(asyncio.CancelledError):
            await turn
        waited = time.monotonic() - cancelled_at
        charging.release.touch()
        claim = charging.cfg.workspace / "sessions" / "s" / ".harness" / "claim"
        await _until(lambda: not claim.exists())
        return waited

    assert asyncio.run(given_up()) < 2, "the caller was held for the hung call"


def test_a_caller_is_let_go_of_while_a_hung_call_holds_the_session(charging, monkeypatch):
    """A wait with no bound would keep a caller as long as a hung tool; let go of at once,
    the next turn ran beside the call. The caller goes after the bound, the session is
    refused until the call ends, and its answer is kept for the turn after."""
    Scripted.script.append(_asking("hold"))

    async def hung() -> float:
        kf = charging.kf(replace(charging.cfg, cancel_wait_s=0.2))
        turn = asyncio.ensure_future(kf.arun(_asked()))
        await _until(lambda: "hold" in _lines(charging.begun))
        cancelled_at = time.monotonic()
        turn.cancel()
        with pytest.raises(asyncio.CancelledError):
            await turn
        waited = time.monotonic() - cancelled_at
        with pytest.raises(SessionBusyError):
            await kf.arun(Request("again", agent="only", session_id="s"))
        charging.release.touch()
        claim = charging.cfg.workspace / "sessions" / "s" / ".harness" / "claim"
        await _until(lambda: not claim.exists())
        return waited

    assert asyncio.run(hung()) < 2, "the caller was held for the hung call"
    assert "receipt R-hold" in _told_next(charging, "c-hold").text


def test_a_turn_its_caller_left_ends_before_its_claim_could_lapse(charging, monkeypatch):
    """A turn whose caller had left waited for a hung call with no bound, holding its
    session past the point another turn may take the claim over, and then wrote its ending
    behind that turn. It ends a superstep's bound past its deadline, without the answer."""
    cfg = replace(charging.cfg, turn_timeout_s=0.5, cancel_wait_s=0.1)
    monkeypatch.setattr(Config, "superstep_bound", property(lambda _: 0.5))
    Scripted.script.append(_asking("hold"))

    async def left() -> ToolMessage:
        turn = asyncio.ensure_future(charging.kf(cfg).arun(_asked()))
        await _until(lambda: "hold" in _lines(charging.begun))
        turn.cancel()
        with pytest.raises(asyncio.CancelledError):
            await turn
        await asyncio.sleep(1.5)
        try:
            # Under a claim that is not stale, so only the turn letting go frees it.
            return await asyncio.to_thread(_told_next, charging, "c-hold")
        finally:
            charging.release.touch()

    assert "receipt" not in asyncio.run(left()).text


def test_a_turn_out_of_time_ends_before_its_claim_could_lapse(charging, monkeypatch):
    """Out of time, a turn waited for a hung call with no bound, its caller held as long as
    the call and its claim left to lapse under it. It answers a superstep's bound past its
    deadline, with the session already let go of."""
    cfg = replace(charging.cfg, turn_timeout_s=0.3)
    monkeypatch.setenv("KF_TEST_SLOW", "0.5")
    monkeypatch.setattr(Config, "superstep_bound", property(lambda _: 0.5))
    Scripted.script.append(_asking("1", "hold"))

    async def out_of_time() -> tuple[Any, list[str]]:
        try:
            result = await charging.kf(cfg).arun(_asked())
            charged = charging.charged()
            # Under a claim that is not stale, so only the turn letting go frees it.
            await asyncio.to_thread(_told_next, charging, "c-hold")
            return result, charged
        finally:
            charging.release.touch()

    result, charged = asyncio.run(out_of_time())

    assert result.stop_reason == "max_duration"
    assert charged == ["1"], "the turn waited for the hung call"
    assert "c-1" in [entry.call for entry in result.ledger], "answered before it ended"


def test_a_stream_ending_with_its_last_chunk_unread_still_ends():
    """The graph's task can end with its last chunk still queued, where the wake it leaves
    does not fit, and a reader then waited on the empty queue for ever."""

    async def chunks() -> Any:
        yield "last"

    async def read() -> list[Any]:
        stream = running._OwnedStream(chunks, contextvars.copy_context())
        await asyncio.wait({stream.task})
        return [await asyncio.wait_for(stream.next(), 2) for _ in range(2)]

    assert asyncio.run(read()) == ["last", running._ENDED]


def test_a_turn_that_outlived_its_claim_writes_nothing_behind_the_turn_that_took_it(
    charging, monkeypatch
):
    """A turn whose tool hung past its claim ended after a newer turn had taken the
    session over, and wrote its ending behind that turn's: the newer exchange vanished
    from the conversation, and nothing said so."""
    monkeypatch.setattr(Config, "superstep_bound", property(lambda _: 0.3))
    cfg = replace(charging.cfg, turn_timeout_s=0.3)
    sink = RecordedEvents()
    Scripted.script.append(_asking("hold"))
    old: dict[str, Any] = {}

    def outlived() -> None:
        kf = Kingfisher(cfg, sessions=LocalSessions(), run_events=sink)
        old["result"] = kf.run(_asked())

    hung = threading.Thread(target=outlived)
    hung.start()
    while "hold" not in _lines(charging.begun):
        time.sleep(0.01)
    # Past `claim_stale_after`, 0.6s here: the hung turn's claim is a dead turn's now.
    time.sleep(1.0)
    Scripted.script.append(AIMessage(content="the newer answer"))
    charging.kf(cfg).run(Request("the newer question", agent="only", session_id="s"))
    charging.release.touch()
    hung.join(15)

    Scripted.script.append(AIMessage(content="noted"))
    charging.kf().run(Request("what happened?", agent="only", session_id="s"))
    heard = [one.text for one in Scripted.heard[-1] if isinstance(one, HumanMessage)]
    assert any("the newer question" in one for one in heard), "the newer turn was erased"
    assert old["result"].keep_failure == running.TAKEN_OVER
    [warned] = sink.named("turn_outlived_claim")
    assert (warned["session_id"], warned["calls"]) == ("s", ["c-hold"])


def test_a_turn_after_one_that_died_is_told_to_check_before_acting_again(charging, monkeypatch):
    """A turn killed after its tools ran leaves nothing in the session but its claim, so the
    turn asked the same thing next did it again, charging twice, with nothing said. That
    turn's model, its caller and the operator are now told. The control is a turn after one
    that ended, which is told nothing."""
    sink = RecordedEvents()
    kf = Kingfisher(charging.cfg, sessions=LocalSessions(), run_events=sink)
    Scripted.script.extend([_asking("0"), AIMessage(content="charged")])
    with monkeypatch.context() as killed:
        # Dead before its ending: no record written, and the claim never given back.
        killed.setattr(running.Running, "_end_turn", lambda self, turn: None)
        kf.run(_asked())
    claim = charging.cfg.workspace / "sessions" / "s" / ".harness" / "claim"
    long_ago = time.time() - charging.cfg.claim_stale_after - 60
    os.utime(claim, (long_ago, long_ago))

    Scripted.script.append(AIMessage(content="checking first"))
    events = list(kf.stream(_asked()))
    asked = [one.text for one in Scripted.heard[-1] if isinstance(one, HumanMessage)]

    assert events[0].kind == "earlier_turn_unfinished"
    assert UNFINISHED in asked[-1]
    assert events[-1].result.earlier_turn_unfinished is True
    [warned] = sink.named("earlier_turn_unfinished")
    assert warned["session_id"] == "s"

    Scripted.script.append(AIMessage(content="done"))
    events = list(kf.stream(_asked()))
    asked = [one.text for one in Scripted.heard[-1] if isinstance(one, HumanMessage)]

    assert UNFINISHED not in asked[-1]
    assert UNFINISHED in asked[0], "the warning was not kept with the message it was said in"
    assert events[-1].result.earlier_turn_unfinished is False


def test_a_turn_ending_after_its_caller_left_warns_by_its_errors_type_never_its_words(
    charging, monkeypatch
):
    """Nobody is left to raise a failed ending to, so the deployment's `RunEvents` is all
    that hears it: warned outside the turn's warnings it reached none, and logged with its
    traceback it carried the error's words."""
    ending = running.Running._aend_turn

    async def failing(self: Any, turn: Any) -> None:
        await ending(self, turn)
        msg = "the customer's card number"
        raise RuntimeError(msg)

    monkeypatch.setattr(running.Running, "_aend_turn", failing)
    sink = RecordedEvents()
    Scripted.script.append(_asking("hold"))

    async def left() -> None:
        kf = Kingfisher(
            replace(charging.cfg, cancel_wait_s=0.2), sessions=LocalSessions(), run_events=sink
        )
        turn = asyncio.ensure_future(kf.arun(_asked()))
        await _until(lambda: "hold" in _lines(charging.begun))
        turn.cancel()
        with pytest.raises(asyncio.CancelledError):
            await turn
        charging.release.touch()
        await _until(lambda: sink.named("turn_not_ended"))

    asyncio.run(left())
    [warned] = sink.named("turn_not_ended")
    assert (warned["session_id"], warned["error_type"]) == ("s", "RuntimeError")
    assert "card number" not in str(warned)


def test_a_call_from_code_is_waited_for_through_a_cancel(cfg, session_dir):
    """`eval` and a compiled subagent run a sync tool through `GuardedTool`, whose cancel
    let go of the thread at once and left the charge to land after."""
    charges: list[str] = []
    begun, release = threading.Event(), threading.Event()

    def charge(amount: str) -> str:
        """Charge the customer."""
        begun.set()
        release.wait(10)
        charges.append(amount)
        return f"charged {amount}"

    (guarded,) = guarded_tools([charge], paths_in(cfg, session_dir))

    async def cancelled() -> list[str]:
        call = asyncio.ensure_future(guarded.ainvoke({"amount": "10"}))
        await _until(begun.is_set)
        asyncio.get_running_loop().call_later(0.2, release.set)
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call
        return list(charges)

    assert asyncio.run(cancelled()) == ["10"]


def test_a_call_from_code_waiting_for_a_thread_is_not_begun_once_its_turn_stops(
    cfg, session_dir
):
    """`GuardedTool` checked its turn only before handing a call to the thread pool, so a
    compiled subagent's call still waiting for a thread charged after its turn stopped."""
    charges: list[str] = []
    begun, release = threading.Event(), threading.Event()

    def charge(amount: str) -> str:
        """Charge the customer."""
        if amount == "first":
            begun.set()
            release.wait(10)
        charges.append(amount)
        return f"charged {amount}"

    (guarded,) = guarded_tools([charge], paths_in(cfg, session_dir))
    stopping = threading.Event()
    context = contextvars.copy_context()
    context.run(STOPPING.set, stopping)

    async def stopped() -> Any:
        loop = asyncio.get_running_loop()
        # One thread, so the second call waits for the first's.
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        first = loop.create_task(guarded.ainvoke({"amount": "first"}), context=context)
        second = loop.create_task(guarded.ainvoke({"amount": "second"}), context=context)
        await _until(begun.is_set)
        stopping.set()
        release.set()
        await first
        return await second

    assert asyncio.run(stopped()) == NOT_RUN
    assert charges == ["first"]


def test_a_cancelled_command_is_ended_not_waited_out(cfg, session_dir):
    """A thread running `execute` went on with the command after a cancel, so it wrote
    behind the next turn; ended, the turn's wait is milliseconds."""
    backend = backend_at(cfg, session_dir)
    written = session_dir / "scratchpad" / "late.txt"

    async def cancelled() -> float:
        command = asyncio.ensure_future(
            backend.aexecute(f"sleep 1 && echo late > {written}", timeout=30)
        )
        await asyncio.sleep(0.3)
        cancelled_at = time.monotonic()
        command.cancel()
        with pytest.raises(asyncio.CancelledError):
            await command
        return time.monotonic() - cancelled_at

    waited = asyncio.run(cancelled())
    time.sleep(1.2)

    assert waited < 0.9
    assert not written.exists(), "the command ran on after its cancel"
