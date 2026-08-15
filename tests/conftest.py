from __future__ import annotations

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

from kingfisher.adapters.workspace_fs import ensure_layout, ensure_session_layout
from kingfisher.config import Config


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
    from kingfisher.adapters.workspace_fs import LocalSessionDirs

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
        keep_runs=2,
        timeout_s=30,
    )
