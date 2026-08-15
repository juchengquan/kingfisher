from __future__ import annotations

from langchain_core.messages import AIMessage

from kingfisher.agent import build_agent, system_prompt
from tests.conftest import FakeToolCallingModel


def _all_text(messages) -> str:
    return "\n".join(str(getattr(m, "content", "")) for m in messages)


def test_agent_runs_shell_and_writes_files(cfg):
    """The wiring test that matters: a scripted tool sequence, no network.

    Exercises the three things most likely to be misconfigured at once -- the
    shell env allowlist (does `python3` resolve?), the backend root (does a
    virtual path land inside the workspace?), and tool dispatch itself.
    """
    responses = [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "execute", "args": {"command": 'python3 -c "print(6*7)"'}, "id": "c1"}
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                {"name": "write_file", "args": {"file_path": "/out.txt", "content": "42"}, "id": "c2"}
            ],
        ),
        AIMessage(content="done"),
    ]

    agent = build_agent(cfg, model=FakeToolCallingModel(responses=responses))
    out = agent.invoke(
        {"messages": [{"role": "user", "content": "go"}]},
        config={"recursion_limit": 12},
    )

    # The shell actually ran, with a PATH we constructed rather than inherited.
    assert "42" in _all_text(out["messages"])
    # The virtual path /out.txt resolved to a real file inside root_dir.
    assert (cfg.workspace / "out.txt").read_text().strip() == "42"


def test_planning_and_permissions_are_wired(cfg, monkeypatch):
    """deepagents 0.7.6 ships no planning tool, and /data must be write-denied.

    Asserted at the construction seam rather than by digging into compiled
    graph internals, which are not a public contract.
    """
    captured: dict = {}

    def spy(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("kingfisher.agent.create_deep_agent", spy)
    build_agent(cfg, model=FakeToolCallingModel(responses=[AIMessage(content="ok")]))

    middleware_names = {type(m).__name__ for m in captured["middleware"]}
    assert "TodoListMiddleware" in middleware_names

    (rule,) = captured["permissions"]
    assert rule.mode == "deny"
    assert rule.paths == ["/data/**"]
    # `delete` maps to the `write` operation, so one rule covers write/edit/delete.
    assert "write" in rule.operations

    assert captured["system_prompt"] == system_prompt(cfg)
    # M2 capabilities are off by default, so neither is passed through.
    assert "skills" not in captured
    assert "memory" not in captured


def test_enabling_a_capability_wires_the_middleware_not_just_the_prompt(cfg, monkeypatch):
    """One switch drives both, so the prompt cannot describe a missing capability."""
    from dataclasses import replace

    captured: dict = {}
    monkeypatch.setattr("kingfisher.agent.create_deep_agent", lambda **kw: captured.update(kw))
    build_agent(
        replace(cfg, skills_enabled=True, memory_enabled=True),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
    )

    assert captured["skills"] == ["/skills/"]
    assert captured["memory"] == ["/memory/AGENTS.md"]
    assert "/skills" in captured["system_prompt"]


def test_system_prompt_carries_no_host_paths_or_session_ids():
    """Q19/Q20: the prompt must stay byte-identical across workspaces and
    sessions, or the cached prefix is invalidated on every run."""
    text = system_prompt()
    assert "/Users/" not in text
    assert "/home/" not in text
    assert "session_id" not in text
    # It must still teach the virtual layout.
    assert "/data" in text
    assert "/derived" in text
