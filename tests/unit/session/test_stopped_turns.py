"""A turn stopped while its tools run: what still runs after the stop, and what the next
turn is told.

Through real turns, on a graph kingfisher built and a tool that counts what it did:
whether a call runs after a stop is decided by langgraph's executor and kingfisher's
middleware together, and a stub graph has neither.
"""

from __future__ import annotations

import contextvars
import threading
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from kingfisher import Kingfisher, Request
from kingfisher.core.agent.middlewares.stopping import NOT_RUN, STOPPING
from kingfisher.core.agent.tool_guards import guarded_tools
from kingfisher.plugins import LocalSessions
from tests.conftest import an_agent, start, tools_dir
from tests.unit.scripted import Scripted

#: More calls in one reply than langgraph's executor runs at once, so some are queued.
CALLS = 40

TOOLS = '''
import os
import time
from pathlib import Path


def charge(amount: str) -> str:
    """Charge the customer."""
    if amount != "0":
        time.sleep(0.3)
    with Path(os.environ["KF_TEST_CHARGES"]).open("a", encoding="utf-8") as kept:
        kept.write(amount + "\\n")
    return f"charged {amount}"


TOOLS = [charge]
'''


@pytest.fixture
def charging(scripted, tmp_path, monkeypatch):
    """An agent holding `charge`, and the file each charge is written to."""
    tools_dir(scripted).mkdir(parents=True, exist_ok=True)
    (tools_dir(scripted) / "charging.py").write_text(TOOLS, encoding="utf-8")
    an_agent(scripted, tools="[charge]")
    start(scripted, "s")
    charges = tmp_path / "charges.txt"
    monkeypatch.setenv("KF_TEST_CHARGES", str(charges))
    return scripted, charges


def _charged(charges: Path) -> list[str]:
    return charges.read_text(encoding="utf-8").split() if charges.exists() else []


def _many() -> AIMessage:
    """One reply asking for `CALLS` charges together, the first of them instant."""
    calls = [
        {"name": "charge", "args": {"amount": str(n)}, "id": f"c{n}"} for n in range(CALLS)
    ]
    return AIMessage(content="", tool_calls=calls)


def test_a_call_not_begun_when_a_turn_stops_is_not_run(charging):
    """Closing a turn's stream waited for every call the reply queued, and a queued call
    began then: 40 calls asked together, stopped with a few under way, charged 40 times.
    """
    cfg, charges = charging
    Scripted.script.extend([_many(), AIMessage(content="done")])
    kf = Kingfisher(cfg, sessions=LocalSessions())

    turn = kf.stream(Request("charge them all", agent="only", session_id="s"))
    for event in turn:
        if event.kind == "tool_result":
            break
    turn.close()

    assert 0 < len(_charged(charges)) < CALLS


@pytest.mark.parametrize("stopped", [False, True], ids=["running", "stopping"])
def test_a_call_from_code_is_not_begun_once_its_turn_is_stopping(stopped):
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

    said = context.run(guarded.invoke, {"amount": "10"})

    assert charges == ([] if stopped else ["10"])
    assert (said == NOT_RUN) is stopped
