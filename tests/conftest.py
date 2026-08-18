from __future__ import annotations

import pytest
from deepagents import create_deep_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

from kingfisher.config import Config, Endpoint, ModelProfile, Models
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


#: The endpoint every fixture builds against. Port 9 is discard: a test that
#: accidentally makes a real call hangs on connect rather than reaching anyone.
FAKE_ENDPOINT = Endpoint(
    api="anthropic",
    base_url="http://127.0.0.1:9/never-called",
    api_key="test-key-not-real",
)

#: Two models on one endpoint, which is the shape the catalogue exists to allow
#: and the shape a delegate test needs: `cheap-model` carries params that differ
#: from the default's, so a test asserting a delegate got *its own* ceiling
#: cannot pass by accident on the deployment's.
FAKE_MODELS = {
    "fake-model": ModelProfile(model="fake-model", endpoint="fake"),
    "cheap-model": ModelProfile(
        model="cheap-model", endpoint="fake", max_tokens=321, timeout_s=45
    ),
}


#: Bound here because an unbound alias refuses the build, which is the behaviour
#: under test elsewhere -- a fixture that left them unbound would make every test
#: naming one a test of that refusal instead. They used to be here because the
#: shipped presets named them; the presets are a separate distribution now and
#: bind their own, and these are kingfisher's own fixtures.
FAKE_ALIASES = {"cheap": "cheap-model", "alternate": "cheap-model"}

#: One record where the fixture used to set four fields on `Config`.
FAKE_CATALOGUE = Models(
    models=FAKE_MODELS,
    endpoints={"fake": FAKE_ENDPOINT},
    default="fake-model",
    aliases=FAKE_ALIASES,
)


@pytest.fixture
def cfg(workspace):
    return Config(
        workspace=workspace,
        models=FAKE_CATALOGUE,
        turn_timeout_s=3600,
        execution_timeout_s=30,
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

    monkeypatch.setattr("kingfisher.infrastructure.harness.agent.create_deep_agent", spy)
    return captured


@pytest.fixture(scope="session")
def shipped():
    """The definitions that ship with kingfisher, reached as an install would.

    A fixture rather than a module constant because `importlib.resources` does
    not promise the files sit on disk -- a zip-imported package materialises
    them for the duration of the context and cleans up after.

    In `conftest` rather than in one test module because two files want it: the
    tests for the definitions themselves, and the seeding tests that check the
    README describes them accurately.
    """
    from kingfisher.infrastructure import seeding

    with seeding.opened(seeding.ASSETS) as root:
        yield root


@pytest.fixture
def workspace_with_presets(cfg, shipped):
    """A workspace holding the shipped definitions, as `kingfisher seed` leaves one."""
    import shutil

    for kind in ("skills", "subagents"):
        shutil.copytree(shipped / kind, cfg.workspace / kind, dirs_exist_ok=True)
    return cfg


@pytest.fixture
def fake_model():
    """A model that answers once and stops, for a build that must not call out."""
    from langchain_core.messages import AIMessage

    return FakeToolCallingModel(responses=[AIMessage(content="ok")])


def dispatched(graph) -> tuple[str, ...]:
    """`registered_tools` for a graph the tests built themselves.

    It answers `None` for a graph it cannot read, which is a real state and has
    its own tests. It is never the right answer *here*: every graph these tests
    pass in came from `build_agent`, so unreadable means the introspection broke
    rather than that the agent dispatches nothing -- and silently reading it as
    the empty tuple is how a rename upstream would empty the built-in set with
    every assertion still passing.
    """
    from kingfisher.infrastructure.harness.agent import registered_tools

    names = registered_tools(graph)
    assert names is not None, "a graph built here must be readable"
    return names
