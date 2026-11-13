"""What a session keeps about itself, where the agent cannot reach it.

`.harness` holds a session's conversation, the lock a turn holds and its run log,
inside the session's own folder, so that deleting the session deletes them with it.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage

from kingfisher.core.access.capabilities import Capabilities
from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.layout import HARNESS, RECENT_TURNS_FILE
from kingfisher.core.request import Request
from kingfisher.core.workspace import ensure_layout
from kingfisher.plugins import LocalSessions
from kingfisher.plugins.sessions.backend import CLAIM, backend_at
from tests.conftest import FakeToolCallingModel, Through, start
from tests.unit.plugins.sessions.conftest import CONVERSATION, kept_conversation

AGENT = """name: assistant
description: An agent.
system_prompt: |
  You help.
"""


def _agent(cfg) -> None:
    root = cfg.definitions / "agents"
    root.mkdir(parents=True, exist_ok=True)
    (root / "assistant.yaml").write_text(AGENT, encoding="utf-8")


def _drive(cfg, session_dir, tool, args) -> str:
    """Run one tool call through a real graph and return what the tool said."""
    call = AIMessage(content="", tool_calls=[{"name": tool, "id": "1", "args": args}])
    graph = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        capabilities=Capabilities(builtin_tools=("write_file", "read_file", "ls")),
        model=FakeToolCallingModel(responses=[call, AIMessage(content="done")]),
    ).graph
    result = graph.invoke(
        {"messages": [("user", "go")]},
        config={"configurable": {"thread_id": "t"}, "recursion_limit": 8},
    )
    return " ".join(
        str(m.content) for m in result["messages"] if type(m).__name__ == "ToolMessage"
    )


# -- the tool half --------------------------------------------------------


def test_a_file_tool_cannot_write_into_the_harness(cfg, session_dir):
    """The next turn is rebuilt from the conversation kept here. A turn that could
    rewrite it could put words in the user's mouth for every turn after it."""
    kept = kept_conversation(session_dir)
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text(CONVERSATION, encoding="utf-8")

    said = _drive(
        cfg, session_dir, "write_file",
        {"file_path": f"/{HARNESS}/{RECENT_TURNS_FILE}", "content": "rewritten"},
    )

    assert "permission denied" in said.lower()
    assert kept.read_text(encoding="utf-8") == CONVERSATION


def test_a_file_tool_cannot_read_the_harness_either(cfg, session_dir):
    """Unlike `/skills/`, which is read-only rather than unreachable: a skill is
    instructions the agent is meant to follow, and this is bookkeeping about the
    agent that it gains nothing by reading."""
    kept = kept_conversation(session_dir)
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text(CONVERSATION, encoding="utf-8")

    said = _drive(cfg, session_dir, "read_file", {"file_path": f"/{HARNESS}/{RECENT_TURNS_FILE}"})

    assert "You help" not in said


def test_a_listing_does_not_show_the_harness(cfg, session_dir):
    """Filtered out of the results rather than refused, which is what a deny rule does
    to `ls`, `glob` and `grep` -- so the model never sees a directory it would then
    try to open and be refused for.
    """
    said = _drive(cfg, session_dir, "ls", {"path": "/"})

    assert "outputs" in said, said
    assert ".harness" not in said, said


def test_the_conversation_and_the_claim_go_with_the_session(cfg):
    """The residue this whole change is about: one file per session that ever existed,
    under `state_dir`, deleted by nothing."""
    from kingfisher.core.service import Kingfisher
    from tests.unit.stand_ins import StubAgent

    _agent(cfg)
    service = Kingfisher(
        cfg, graph=StubAgent("ok"), sessions=LocalSessions()
    )
    session_id = start(cfg, "s")
    service.run(Request(task="go", session_id=session_id))
    directory = cfg.workspace / "sessions" / session_id
    assert kept_conversation(directory).is_file()

    Through(service).delete(session_id)

    assert not kept_conversation(directory).exists()
    assert not (directory / HARNESS / CLAIM).exists()
    assert not list((cfg.workspace / "sessions").iterdir())


# -- the harness-owned directory, and laying one out ----------------------


def test_the_harness_owned_directory_is_protected_too(cfg):
    """The profile lives in it, and after `TMPDIR` moved out nothing in it is the
    agent's -- so it is denied as a directory rather than as one named file."""
    from kingfisher.plugins.sessions.sandbox import confinement

    protected = confinement.protected_roots(
        cfg.kingfisher_dir, (cfg.skills_dir,), tuple(cfg.catalogue_roots.values())
    )

    assert cfg.kingfisher_dir.resolve() in protected


def test_an_empty_directory_is_laid_out_fresh(tmp_path):
    """A path with no marker reads as never used, and `ensure_layout` lays it out."""
    assert ensure_layout(tmp_path / "fresh").is_dir()
