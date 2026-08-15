"""The application service: wired once, asked many times."""

from __future__ import annotations

import pytest

from kingfisher import Kingfisher
from kingfisher.domain.capabilities import Capabilities
from kingfisher.domain.request import Request
from tests.conftest import StubCheckpointer
from tests.test_run import StubAgent


class CountingCheckpointer(StubCheckpointer):
    """Counts how many times a thread store had to be opened."""

    built = 0

    def __init__(self) -> None:
        super().__init__()
        type(self).built += 1


def test_the_deployment_scoped_wiring_happens_once(cfg, monkeypatch):
    """The reason this object exists. `stream()` opened the thread store on
    every call; a server serving many turns should open it at startup."""
    CountingCheckpointer.built = 0
    monkeypatch.setattr(
        "kingfisher.app.service.build_checkpointer", lambda _cfg: CountingCheckpointer()
    )

    service = Kingfisher(cfg, agent=StubAgent("ok"))
    assert CountingCheckpointer.built == 1

    for _ in range(3):
        service.run(Request("go"))

    assert CountingCheckpointer.built == 1  # three turns later, still one


def test_three_turns_share_one_service_and_still_get_their_own_directories(cfg):
    """Wiring is shared; per-turn state is not."""
    service = Kingfisher(cfg, agent=StubAgent("ok"), threads=StubCheckpointer())

    turns = [service.run(Request("go", session_id="s")).turn_id for _ in range(3)]
    assert turns == ["t001", "t002", "t003"]


def test_construction_prepares_only_what_sessions_share(cfg):
    """Eagerly, so a broken workspace fails at startup rather than mid-turn --
    but only the shared tier. `/data` and the rest belong to a session, whose
    path is not known until a request names it."""
    service = Kingfisher(cfg, threads=StubCheckpointer())

    assert service.workspace.is_dir()
    assert (service.workspace / "skills").is_dir()
    assert (service.workspace / "sessions").is_dir()
    assert (service.workspace / ".gitignore").is_file()
    assert not (service.workspace / "data").exists()


def test_an_injected_agent_is_reused_and_refuses_narrowing(cfg, session_dir):
    """Injection is by collaborator, not by monkeypatching -- and an agent
    built elsewhere cannot honour restrictions it never saw."""
    agent = StubAgent("ok")
    service = Kingfisher(cfg, agent=agent, threads=StubCheckpointer())

    assert service.agent_for(Request("go"), session_dir) is agent

    with pytest.raises(ValueError, match="pre-built agent"):
        service.agent_for(
            Request("go", capabilities=Capabilities(tools=("read_file",))), session_dir
        )


def test_a_fresh_agent_is_built_per_request(cfg, session_dir):
    """Deliberately not cached: it reads the workspace's skills and subagent
    definitions, which a user can edit between turns. ~30ms against a model
    call of seconds is not a trade worth taking."""
    # A real checkpointer: this builds a real agent, and deepagents type-checks
    # the saver it is handed.
    service = Kingfisher(cfg)

    assert service.agent_for(Request("go"), session_dir) is not service.agent_for(
        Request("go"), session_dir
    )


def test_the_module_level_helpers_are_unchanged(cfg):
    """`run("do a thing")` was the whole public surface before this object, and
    is unaffected by it."""
    from kingfisher import run

    result = run("say hello", cfg=cfg, agent=StubAgent("hello"), checkpointer=StubCheckpointer())
    assert result.answer == "hello"
