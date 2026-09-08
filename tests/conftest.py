from __future__ import annotations

from pathlib import Path

import pytest
from deepagents import create_deep_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

from kingfisher.config import Config, Endpoint, ModelProfile, Models
from kingfisher.infrastructure.workspace.layout import ensure_layout
from kingfisher.infrastructure.workspace.sessions import ensure_session_layout


class FakeToolCallingModel(FakeMessagesListChatModel):
    """A fake model that can be handed to `create_agent`."""

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
    """The real `SessionDirs`."""
    from kingfisher.infrastructure.workspace.sessions import LocalSessionDirs

    return LocalSessionDirs()


@pytest.fixture
def workspace(tmp_path):
    return ensure_layout(tmp_path / "ws")


@pytest.fixture
def session_dir(workspace):
    """One session's directory — the backend root."""
    return ensure_session_layout(workspace / "sessions" / "test-session")


#: The endpoint every fixture builds against. Port 9 is discard: a test that
#: accidentally makes a real call hangs on connect rather than reaching anyone.
FAKE_ENDPOINT = Endpoint(
    api="anthropic",
    base_url="http://127.0.0.1:9/never-called",
    api_key="test-key-not-real",
)

#: A second host, so "somewhere else" is expressible. `indistinct` compares
#: hosts rather than endpoint names, deliberately -- two endpoints may point at
#: one gateway -- so a fixture needs a different netloc, not a different key.
OTHER_ENDPOINT = Endpoint(
    api="anthropic",
    base_url="http://127.0.0.2:9/never-called",
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
    #: On the *other* endpoint, and that is its whole job. A delegate written
    #: `distinct: true` refuses a model sharing a host with the default, so a
    #: one-endpoint fixture cannot show a delegate genuinely running elsewhere
    #: -- every model it could name resolves to the same machine.
    "elsewhere-model": ModelProfile(model="elsewhere-model", endpoint="elsewhere"),
}


#: One record where the fixture used to set four fields on `Config`.
FAKE_CATALOGUE = Models(
    models=FAKE_MODELS,
    endpoints={"fake": FAKE_ENDPOINT, "elsewhere": OTHER_ENDPOINT},
    default="fake-model",
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
    """Create a named session, as a service would before serving a turn."""
    from kingfisher.infrastructure.workspace.sessions import ensure_session_layout

    ensure_session_layout(cfg.workspace / "sessions" / session_id)
    return session_id


def declared_subagents(captured: dict) -> list:
    """The delegate specs a build activated, without the built-in one."""
    return [s for s in captured.get("subagents") or () if s.get("name") != "general-purpose"]


def capture_build(monkeypatch) -> dict:
    """Record the arguments `create_deep_agent` was called with -- and let the call
    through.

    Calling through costs about 30ms per test and removes the whole category. A test
    that genuinely wants no construction can still patch it directly.
    """
    captured: dict = {}
    real = create_deep_agent

    def spy(**kwargs):
        captured.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr("kingfisher.infrastructure.harness.agent.create_deep_agent", spy)
    return captured


def repository_root(start: Path | None = None) -> Path:
    """The checkout this file is in, found rather than counted."""
    here = (start or Path(__file__)).resolve()
    for candidate in here.parents:
        if (candidate / "pyproject.toml").is_file() and (candidate / "src" / "kingfisher").is_dir():
            return candidate
    msg = (
        f"no repository root above {here}: expected a directory holding both "
        f"pyproject.toml and src/kingfisher. Tests that read files by path would "
        f"otherwise scan the wrong tree, or nothing, and report success."
    )
    raise AssertionError(msg)


@pytest.fixture(scope="session")
def shipped():
    """This repository's worked definitions, found in this repository."""
    return repository_root() / "assets_examples"


@pytest.fixture
def workspace_with_presets(cfg, shipped):
    """A workspace holding the shipped definitions, as `kingfisher seed` leaves one."""
    import shutil

    # `tools` too, because a delegate that names one is refused before its
    # skills are looked at -- so leaving them out would make a skills test pass
    # by never reaching the check.
    #
    # `agents` stays out: `build_agent` takes capabilities rather than an agent
    # definition, and two of the shipped agents cannot run on a bare checkout.
    for kind in ("skills", "subagents", "tools"):
        shutil.copytree(shipped / kind, cfg.workspace / kind, dirs_exist_ok=True)
    return cfg


@pytest.fixture
def fake_model():
    """A model that answers once and stops, for a build that must not call out."""
    from langchain_core.messages import AIMessage

    return FakeToolCallingModel(responses=[AIMessage(content="ok")])


def dispatched(graph) -> tuple[str, ...]:
    """`registered_tools` for a graph the tests built themselves.

    It answers `None` for a graph it cannot read, and reading that as the empty tuple is
    how a rename upstream would empty the built-in set with every assertion still
    passing.
    """
    from kingfisher.tools.harness import registered_tools

    names = registered_tools(graph)
    assert names is not None, "a graph built here must be readable"
    return names


def an_agent(cfg, name: str = "only", **fields: str) -> str:
    """Write one agent into this workspace and return its name."""
    directory = cfg.catalogue_roots["agents"]
    directory.mkdir(parents=True, exist_ok=True)
    written = "".join(f"{key}: {value}\n" for key, value in fields.items())
    prompt = "" if "system_prompt" in fields else "system_prompt: |\n  You do the task.\n"
    (directory / f"{name}.yaml").write_text(
        f"name: {name}\ndescription: An agent.\n{written}{prompt}", encoding="utf-8"
    )
    return name


def subagents_dir(cfg) -> Path:
    """Where this config's subagent definitions live."""
    return cfg.catalogue_roots["subagents"]


def tools_dir(cfg) -> Path:
    """Where this config's tool modules live. See `subagents_dir`."""
    return cfg.catalogue_roots["tools"]


def verbs(parser) -> dict:
    """Every subcommand a parser offers, keyed by name."""
    return {
        name: subparser
        for action in parser._actions
        for name, subparser in (getattr(action, "choices", None) or {}).items()
    }
