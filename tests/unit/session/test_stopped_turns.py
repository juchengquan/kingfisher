"""A turn stopped while its tools run: what still runs after the stop, and what the next
turn is told.

Through real turns, on a graph kingfisher built and a tool that counts what it did:
whether a call runs after a stop is decided by langgraph's executor and kingfisher's
middleware together, and a stub graph has neither.
"""

from __future__ import annotations

import asyncio
import contextvars
import threading
import time
from contextlib import aclosing
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from kingfisher import Kingfisher, Request
from kingfisher.core.agent.middlewares.stopping import NOT_RUN, STOPPING
from kingfisher.core.agent.tool_guards import guarded_tools
from kingfisher.core.session import running
from kingfisher.core.session.model import SessionBusyError
from kingfisher.plugins import LocalSessions, backend_at
from tests.conftest import an_agent, start, tools_dir
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


TOOLS = [charge, charge_later, break_soon]
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
    an_agent(scripted, tools="[charge, charge_later, break_soon]")
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
    charging.kf().run(Request("did it go through?", session_id="s"))
    return next(
        one
        for one in Scripted.heard[-1]
        if isinstance(one, ToolMessage) and one.tool_call_id == call
    )


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
def test_a_call_from_code_is_not_begun_once_its_turn_is_stopping(stopped, awaited):
    """`eval` and a compiled subagent call tools no middleware sees, so a program still
    running when its turn stopped went on starting calls. Running is the control."""
    charges: list[str] = []

    def charge(amount: str) -> str:
        """Charge the customer."""
        charges.append(amount)
        return f"charged {amount}"

    (guarded,) = guarded_tools([charge], None)
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


def test_a_turn_out_of_time_answers_once_its_calls_have_ended(charging, monkeypatch):
    """Out of time, an awaited turn answered while a call was still running, so the next
    turn could start before its charge landed. Waits as the sync path does, with no bound:
    the caller has not left."""
    cfg = replace(charging.cfg, turn_timeout_s=0.5)
    monkeypatch.setenv("KF_TEST_SLOW", "0.8")
    monkeypatch.setattr(running, "LEFT_WAIT_S", 0.1)
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
    call charged behind whatever the caller did next. No bound: the caller has not left."""
    monkeypatch.setattr(running, "LEFT_WAIT_S", 0.1)
    broke = {"name": "break_soon", "args": {}, "id": "c-broke"}
    asked = _asking("hold")
    Scripted.script.append(AIMessage(content="", tool_calls=[*asked.tool_calls, broke]))

    async def failed() -> list[str]:
        asyncio.get_running_loop().call_later(0.4, charging.release.touch)
        with pytest.raises(BaseException, check=_broke):  # noqa: B017 -- `check` narrows it
            await charging.kf().arun(_asked())
        return charging.charged()

    assert asyncio.run(failed()) == ["hold"], "the turn raised before its call ended"


def test_a_caller_cancelling_a_turn_that_waits_out_of_time_is_let_go_of(charging, monkeypatch):
    """A turn out of time waits for its calls with no bound, and a caller giving up on it
    then has left: it is let go after the bound, and hears its cancel."""
    cfg = replace(charging.cfg, turn_timeout_s=0.5)
    monkeypatch.setenv("KF_TEST_SLOW", "0.8")
    monkeypatch.setattr(running, "LEFT_WAIT_S", 0.2)
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
    monkeypatch.setattr(running, "LEFT_WAIT_S", 0.2)
    Scripted.script.append(_asking("hold"))

    async def hung() -> float:
        kf = charging.kf()
        turn = asyncio.ensure_future(kf.arun(_asked()))
        await _until(lambda: "hold" in _lines(charging.begun))
        cancelled_at = time.monotonic()
        turn.cancel()
        with pytest.raises(asyncio.CancelledError):
            await turn
        waited = time.monotonic() - cancelled_at
        with pytest.raises(SessionBusyError):
            await kf.arun(Request("again", session_id="s"))
        charging.release.touch()
        claim = charging.cfg.workspace / "sessions" / "s" / ".harness" / "claim"
        await _until(lambda: not claim.exists())
        return waited

    assert asyncio.run(hung()) < 2, "the caller was held for the hung call"
    assert "receipt R-hold" in _told_next(charging, "c-hold").text


def test_a_call_from_code_is_waited_for_through_a_cancel():
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

    (guarded,) = guarded_tools([charge], None)

    async def cancelled() -> list[str]:
        call = asyncio.ensure_future(guarded.ainvoke({"amount": "10"}))
        await _until(begun.is_set)
        asyncio.get_running_loop().call_later(0.2, release.set)
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call
        return list(charges)

    assert asyncio.run(cancelled()) == ["10"]


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
