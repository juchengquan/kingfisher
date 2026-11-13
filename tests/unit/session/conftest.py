"""What the session tests share: a scripted agent with a search tool and a gated query in
the session `s`, and the turns and resumes they drive it with."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage

from kingfisher import Kingfisher, Request
from kingfisher.core.request import Decision, Resume
from tests.conftest import EVERY_TOOL, an_agent, start, tools_dir
from tests.unit.scripted import Scripted

TOOLS = '''
def web_search(query: str) -> str:
    """Search the web."""
    return f"Ten results for {query}"


def sql_query(query: str) -> str:
    """Run a query."""
    return f"two rows for {query}"


TOOLS = [web_search, sql_query]
'''


@pytest.fixture
def logged(scripted):
    """An agent with the tools above, gating `sql_query`, and the session `s`."""
    tools_dir(scripted).mkdir(parents=True, exist_ok=True)
    (tools_dir(scripted) / "probe.py").write_text(TOOLS, encoding="utf-8")
    an_agent(scripted, interrupt_on="[sql_query]", **EVERY_TOOL)
    start(scripted, "s")
    return scripted


@pytest.fixture
def rewinding(logged):
    """`logged`, under the name the rewind tests take it by."""
    return logged


def _calls(*made: tuple[str, str, str]) -> AIMessage:
    """One reply asking for these `(tool, query, call id)` calls together."""
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": {"query": query}, "id": at} for name, query, at in made],
    )


def _say(kf: Kingfisher, task: str, *script: AIMessage | Exception, **request: Any):
    Scripted.script.extend(script)
    return kf.run(Request(task, agent="only", session_id="s", **request))


def _resume(kf: Kingfisher, *answers: Decision, script: tuple[AIMessage, ...] = ()):
    Scripted.script.extend(script)
    return kf.run(Resume(session_id="s", agent="only", decisions=answers))


def _searches(prefix: str, count: int) -> list[AIMessage]:
    return [_calls(("web_search", f"{prefix}{n}", f"{prefix}{n}")) for n in range(count)]


@pytest.fixture
def clearing(scripted):
    """An agent with a tool returning 16,000 characters, clearing past 12,000 tokens."""
    from tests.unit.agent.middlewares.test_clearing import TOOLS as BULKY
    from tests.unit.conftest import _marked

    tools_dir(scripted).mkdir(parents=True, exist_ok=True)
    (tools_dir(scripted) / "probe.py").write_text(BULKY, encoding="utf-8")
    an_agent(scripted, **EVERY_TOOL)
    start(scripted, "s")
    return _marked(scripted, clear_results_at=12_000)
