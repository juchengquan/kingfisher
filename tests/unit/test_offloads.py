"""Where deepagents' own files land in a session: the tool results it evicts and the
messages it keeps aside.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from kingfisher.core.access.capabilities import Capabilities
from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.layout import SCRATCH, SCRATCH_ROUTE
from kingfisher.plugins.sessions.backend import backend_at
from tests.conftest import FakeToolCallingModel, subagents_dir, tools_dir
from tests.unit.test_delegation_ceiling import _subagent_graphs

#: deepagents' names for the two folders, under whatever root the backend gives it.
EVICTED = "large_tool_results"
KEPT_ASIDE = "conversation_history"

#: About 101,000 characters, past the 20,000 tokens deepagents keeps in a tool
#: message, which it counts at four characters each.
LOUD = '''
def loud() -> str:
    """Return a long listing. Use never; this is a fixture."""
    return "\\n".join(f"row {n:05d} " + "x" * 90 for n in range(1_000))


TOOLS = [loud]
'''


class _Shown(FakeToolCallingModel):
    """Keeps what it was shown on every call."""

    shown: list[list[Any]] = []

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.shown.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def _calls_loud() -> _Shown:
    return _Shown(
        responses=[
            AIMessage(content="", tool_calls=[{"name": "loud", "args": {}, "id": "c1"}]),
            AIMessage(content="done"),
        ]
    )


def _with_loud(cfg) -> None:
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "loud.py").write_text(LOUD, encoding="utf-8")


def _evicted_into_the_scratchpad(model: _Shown, session_dir) -> None:
    """The stub the model was shown names the scratchpad, and the result is there."""
    (stub,) = [m for m in model.shown[-1] if isinstance(m, ToolMessage)]
    # Before the location, so a result that was never evicted fails as that and not
    # as a file in the wrong place.
    assert len(str(stub.text)) < 80_000, "the model was shown it whole: nothing was evicted"
    assert f"{SCRATCH_ROUTE}{EVICTED}/c1" in str(stub.text)
    kept = session_dir / SCRATCH / EVICTED / "c1"
    assert kept.read_text(encoding="utf-8").startswith("row 00000 ")
    assert not (session_dir / EVICTED).exists(), "evicted to the session root"


def test_an_evicted_tool_result_lands_in_the_scratchpad(cfg, session_dir):
    """deepagents evicted a long tool result to `large_tool_results/` at the session
    root, in no folder the layout names and nowhere the prompt sends the agent.
    """
    _with_loud(cfg)
    model = _calls_loud()

    build_agent(cfg, backend=backend_at(cfg, session_dir), model=model).graph.invoke(
        {"messages": [{"role": "user", "content": "go"}]}, config={"recursion_limit": 8}
    )

    _evicted_into_the_scratchpad(model, session_dir)


def test_a_subagent_s_evicted_results_land_in_the_scratchpad_too(cfg, session_dir):
    """A subagent is given a `FilesystemMiddleware` of its own, and its evictions
    landed at the session root with the parent's.
    """
    _with_loud(cfg)
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "lister.yaml").write_text(
        "name: lister\ndescription: Lists things.\nsystem_prompt: |\n  You list things.\n",
        encoding="utf-8",
    )
    model = _calls_loud()
    graph = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=model,
        capabilities=Capabilities(subagents=("lister",)),
    ).graph

    _subagent_graphs(graph)["lister"].invoke(
        {"messages": [{"role": "user", "content": "go"}]}, config={"recursion_limit": 8}
    )

    _evicted_into_the_scratchpad(model, session_dir)


def test_a_request_too_long_to_send_is_kept_in_the_scratchpad(cfg, session_dir):
    """deepagents kept a user message too long to send under `conversation_history/`
    at the session root, beside the folders the layout names.
    """
    # About 250,000 characters, past the 50,000 tokens it keeps in a user message.
    request = "\n".join(f"line {n:06d} " + "y" * 90 for n in range(2_500))
    model = _Shown(responses=[AIMessage(content="done")])

    build_agent(cfg, backend=backend_at(cfg, session_dir), model=model).graph.invoke(
        {"messages": [{"role": "user", "content": request}]}, config={"recursion_limit": 8}
    )

    (shown,) = [m for m in model.shown[0] if isinstance(m, HumanMessage)]
    assert len(str(shown.text)) < len(request), "the model was shown it whole"
    assert f"{SCRATCH_ROUTE}{KEPT_ASIDE}/" in str(shown.text)
    (kept,) = (session_dir / SCRATCH / KEPT_ASIDE).glob("*.md")
    assert kept.read_text(encoding="utf-8") == request
    assert not (session_dir / KEPT_ASIDE).exists(), "kept at the session root"
