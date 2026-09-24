from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import fields
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml
from dotenv import load_dotenv
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

from kingfisher.config import Config, Endpoint, ModelProfile, Models
from kingfisher.infrastructure.harness.models import ADAPTERS
from kingfisher.infrastructure.workspace import ensure_layout, ensure_session_layout

if TYPE_CHECKING:
    # Type-only, and deliberately: naming the record at runtime would pull
    # deepagents into every collection of this file for one annotation, which is
    # the 1.4s the lazy front door exists to avoid paying.
    from kingfisher.infrastructure.harness.agent import Assembled
    from kingfisher.kinds.subagents.spec import SubagentSpec


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
    from kingfisher.infrastructure.workspace import LocalSessionDirs

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
#: The variable the fake endpoints read their key from. Named once, because the
#: command reads the catalogue as a *file* -- and a file has to say which variable
#: rather than carry the key -- so the two forms of this catalogue agree on it.
FAKE_KEY_VAR = "FAKE_KEY"

FAKE_ENDPOINT = Endpoint(
    api="anthropic",
    base_url="http://127.0.0.1:9/never-called",
    api_key="test-key-not-real",
    key_env=FAKE_KEY_VAR,
    adapter=ADAPTERS["anthropic"],
)

#: A second host, so "somewhere else" is expressible. `indistinct` compares
#: hosts rather than endpoint names, deliberately -- two endpoints may point at
#: one gateway -- so a fixture needs a different netloc, not a different key.
OTHER_ENDPOINT = Endpoint(
    api="anthropic",
    base_url="http://127.0.0.2:9/never-called",
    api_key="test-key-not-real",
    key_env=FAKE_KEY_VAR,
    adapter=ADAPTERS["anthropic"],
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


#: Everything `ModelProfile` fills in for itself, so writing the catalogue out can
#: say only what a test actually chose. Read off the type rather than listed, or this
#: file would carry a second copy of those defaults to drift from.
_MODEL_DEFAULTS = {f.name: f.default for f in fields(ModelProfile)}


def models_document() -> dict[str, object]:
    """`FAKE_CATALOGUE`, in the shape `models.yaml` is written in.

    Derived from the record rather than written out beside it. The two used to be
    separate declarations and had already come apart: the file every command test
    pointed at named one endpoint and one model where the record has two and three,
    so a test that ran the command saw a different deployment from one that used
    `cfg`, and a model added to `FAKE_MODELS` reached only half the suite.
    """
    return {
        "endpoints": {
            name: {"api": e.api, "base_url": e.base_url, "key_env": e.key_env}
            for name, e in FAKE_CATALOGUE.endpoints.items()
        },
        "default": FAKE_CATALOGUE.default,
        "models": {
            # The key *is* the model's name -- the format defines no `model:` -- and
            # everything else only where this fixture chose something.
            name: {
                field: value
                for field, value in vars(profile).items()
                if field != "model" and value != _MODEL_DEFAULTS.get(field)
            }
            for name, profile in FAKE_CATALOGUE.models.items()
        },
    }


def models_file(workspace: Path) -> Path:
    """Write that catalogue where a command will read it, and say where."""
    path = Path(workspace) / "models.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(models_document(), sort_keys=False), encoding="utf-8")
    return path


@pytest.fixture
def cfg(workspace):
    return Config(
        workspace=workspace,
        models=FAKE_CATALOGUE,
        turn_timeout_s=3600,
        execution_timeout_s=30,
    )


@pytest.fixture
def at_the_command_line(cfg, monkeypatch):
    """The `cfg` fixture, with the environment pointing a command at it.

    `kingfisher` reads the environment and nothing else, so a test that drives the
    command has to wire it. These three lines were written out eighteen times, each
    calling a `_catalogue` helper that two files held byte-identical copies of --
    and that helper wrote a catalogue naming one endpoint and one model, where the
    record beside it has two and three.

    Returns the `cfg` it wired, though most callers take `cfg` as well and use this
    for the wiring alone. Tests pointing the command at some *other* workspace still
    say so themselves: that is a different arrangement, not this one written out.
    """
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_MODELS_FILE", str(models_file(cfg.workspace)))
    monkeypatch.setenv(FAKE_KEY_VAR, "not-a-real-key")
    return cfg


def start(cfg, session_id: str) -> str:
    """Create a named session, as a service would before serving a turn."""
    from kingfisher.infrastructure.workspace import ensure_session_layout

    ensure_session_layout(cfg.workspace / "sessions" / session_id)
    return session_id


def pin(kf, session_id: str, name: str) -> None:
    """Fix a session's agent before it has run, the way its first turn would.

    The document comes from the service's own catalogue, which is where
    `_pin_agent_in` reads it. A deployment that supplies its own graph never
    resolves an agent at all, so a test whose subject is the pin cannot get one by
    running a turn.
    """
    from kingfisher.infrastructure.workspace import remember_agent

    document = kf.catalogue.agents.documents[name]
    remember_agent(kf.workspace / "sessions" / session_id, document)


def a_subagent(text: str, name: str) -> SubagentSpec:
    """A subagent definition written to a file called `name`, read the way the catalogue
    reads one.
    """
    from kingfisher.kinds.subagents import reading

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / name
        path.write_text(text, encoding="utf-8")
        return reading.read(path)


def declared_subagents(built: Assembled) -> list:
    """The delegate specs a build activated, without the built-in one.

    The specs stay plain dicts: `Assembled` records what `create_deep_agent` was
    handed, and what it is handed for a delegate is deepagents' own `SubAgent`
    mapping. Only the outer record gained a type.
    """
    return [s for s in built.subagents or () if s.get("name") != "general-purpose"]


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


#: Every module that calls `load_dotenv`, each reached by a test. Named rather than
#: found at run time so the guard below patches the name those modules bound, and
#: `test_every_module_that_loads_a_dotenv_file_is_guarded` is what finds a third.
READS_DOTENV = ("kingfisher.presentation.cli.__main__", "tests.integration.driver")


def refusing(protected: Path) -> Callable[..., bool]:
    """`load_dotenv`, answering as though one file were not there."""

    def load(dotenv_path: str | os.PathLike[str] | None = None, *args, **kwargs) -> bool:
        # Bare, `load_dotenv` walks up from its caller -- and from anywhere in this
        # checkout that reaches the checkout's own file.
        target = protected if dotenv_path is None else Path(dotenv_path).resolve()
        if target == protected:
            return False
        return load_dotenv(dotenv_path, *args, **kwargs)

    return load


@contextmanager
def environment_restored() -> Iterator[None]:
    """`os.environ` as it was on entry, whatever changed it in between."""
    before = dict(os.environ)
    try:
        yield
    finally:
        for key in set(os.environ) - set(before):
            del os.environ[key]
        for key, value in before.items():
            if os.environ.get(key) != value:
                os.environ[key] = value


@pytest.fixture(autouse=True)
def isolated_from_the_developer(monkeypatch):
    """No test reads the checkout's `.env`, and none leaves the environment changed.

    Both halves were one bug. `main()` loads `./.env`, pytest runs from the checkout,
    so a CLI test loaded the developer's own file -- keys included -- and four `doctor`
    tests failed on any machine that had one. What it loaded was set outside
    `monkeypatch`, which restores only what it recorded, so it stayed set for every
    test after.
    """
    for module in READS_DOTENV:
        monkeypatch.setattr(f"{module}.load_dotenv", refusing(repository_root() / ".env"))
    with environment_restored():
        yield


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
    from kingfisher.infrastructure.harness.tools import registered_tools

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


def middlewares_dir(cfg) -> Path:
    """Where this config's middleware modules live. See `subagents_dir`."""
    return cfg.catalogue_roots["middlewares"]


def verbs(parser) -> dict:
    """Every subcommand a parser offers, keyed by name."""
    return {
        name: subparser
        for action in parser._actions
        for name, subparser in (getattr(action, "choices", None) or {}).items()
    }


def an_inventory(**wrong: Any) -> Any:
    """An `Inventory` describing a workspace with nothing in it and nothing wrong.

    For the rules that break one thing at a time: an error reaching no consumer is
    how `middlewares_error` came to be computed and read by nothing, and driving that
    needs a record whose *other* fields are all quiet.
    """
    from kingfisher.application.inventory import Inventory
    from kingfisher.application.origins import Origin, Origins

    nowhere = Origin(kind="default")
    return Inventory(
        origins=Origins(
            workspace=Path("/nowhere"),
            agents=nowhere,
            middlewares=nowhere,
            skills=nowhere,
            subagents=nowhere,
            tools=nowhere,
            models=nowhere,
            source_ids=nowhere,
            seed=nowhere,
            sessions=nowhere,
        ),
        **wrong,
    )
