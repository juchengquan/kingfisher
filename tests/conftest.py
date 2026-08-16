from __future__ import annotations

import pytest
from deepagents import create_deep_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

from kingfisher.config import Config
from kingfisher.infrastructure.workspace_fs import ensure_layout, ensure_session_layout


class FakeToolCallingModel(FakeMessagesListChatModel):
    """A fake model that can be handed to `create_agent`.

    `FakeMessagesListChatModel` does not implement `bind_tools`, and the agent
    binds tools during construction. Returning `self` is enough: the scripted
    responses already contain the tool calls we want to drive.
    """

    def bind_tools(self, tools, **kwargs):
        return self


class StubCheckpointer:
    """Records thread deletions so the sweep can be asserted on."""

    def __init__(self) -> None:
        self.deleted: list[str] = []

    def delete_thread(self, thread_id: str) -> None:
        self.deleted.append(thread_id)


@pytest.fixture(autouse=True)
def _git_identity(monkeypatch):
    """pre_run_commit needs an identity; CI machines often have none configured."""
    for var, value in {
        "GIT_AUTHOR_NAME": "kingfisher-test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "kingfisher-test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
    }.items():
        monkeypatch.setenv(var, value)


@pytest.fixture
def dirs():
    """The real `SessionDirs`. A test that wants to watch or break an
    individual call substitutes its own object -- that is what the port buys."""
    from kingfisher.infrastructure.workspace_fs import LocalSessionDirs

    return LocalSessionDirs()


@pytest.fixture
def workspace(tmp_path):
    return ensure_layout(tmp_path / "ws")


@pytest.fixture
def session_dir(workspace):
    """One session's directory — the backend root.

    Most tests want *a* session rather than a particular one, and building the
    backend now needs somewhere to root. `Session.open` is not used here: this
    fixture should keep working if the aggregate's naming changes.
    """
    return ensure_session_layout(workspace / "sessions" / "test-session")


@pytest.fixture
def cfg(workspace):
    return Config(
        workspace=workspace,
        api_style="anthropic",
        base_url="http://127.0.0.1:9/never-called",
        api_key="test-key-not-real",
        model="fake-model",
        turn_timeout_s=3600,
        timeout_s=30,
    )


def start(cfg, session_id: str) -> str:
    """Create a named session, as a service would before serving a turn.

    A request cannot create one -- an id it carries may have come from whoever
    called the service -- so a test that wants to name its session has to open
    it the way the service does.
    """
    from kingfisher.infrastructure.workspace_fs import ensure_session_layout

    ensure_session_layout(cfg.workspace / "sessions" / session_id)
    return session_id


def capture_build(monkeypatch) -> dict:
    """Record the arguments `create_deep_agent` was called with -- and let the
    call through.

    The recording used to *replace* the call, returning a stub. That made every
    assertion here blind to anything deepagents validates while constructing,
    and three separate bugs slipped past because of it: `permissions=` is
    refused unless every rule path is scoped to a backend route, and `/data`,
    `/skills` and `/memory` each had to be caught by a live run instead.

    Calling through costs about 30ms per test and removes the whole category.
    A test that genuinely wants no construction can still patch it directly.
    """
    captured: dict = {}
    real = create_deep_agent

    def spy(**kwargs):
        captured.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr("kingfisher.infrastructure.agent.create_deep_agent", spy)
    return captured
