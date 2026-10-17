from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import fields, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml
from dotenv import load_dotenv
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

from kingfisher.core.agent.models import WIRE_FORMATS
from kingfisher.core.config import Config, Endpoint, Landing, ModelProfile, Models, WireFormat
from kingfisher.core.layout import DEFAULT_LAYOUT
from kingfisher.core.session.steps import drive
from kingfisher.core.workspace import ensure_definitions, ensure_layout
from kingfisher.plugins.sessions.folders import ensure_session_layout
from tests.unit.scripted import Scripted

if TYPE_CHECKING:
    # Type-only, and deliberately: naming the record at runtime would pull
    # deepagents into every collection of this file for one annotation, which is
    # the second the lazy front door exists to avoid paying.
    from kingfisher.core.agent.assembly import Assembled
    from kingfisher.core.definitions.subagents.spec import SubagentSpec


class FakeGraph:
    """What a stand-in for a compiled graph answers before it is driven.

    The service runs a deployment's graph as a copy on the turn's own saver. A stand-in
    keeps no saver, so it is its own copy.
    """

    def copy(self, update=None):
        return self


class FakeToolCallingModel(FakeMessagesListChatModel):
    """A fake model that can be handed to `create_agent`."""

    def bind_tools(self, tools, **kwargs):
        return self


@pytest.fixture
def scripted(cfg):
    """`cfg`, with every endpoint answered by `Scripted`."""
    row = WireFormat(
        f"{Scripted.__module__}:Scripted",
        Landing(
            model="model",
            base_url="base_url",
            api_key="api_key",
            max_tokens="max_tokens",
            timeout="timeout",
        ),
    )
    endpoints = {
        name: replace(endpoint, wire_format=row) for name, endpoint in cfg.models.endpoints.items()
    }
    Scripted.script.clear()
    Scripted.heard.clear()
    Scripted.summaries = 0
    yield replace(cfg, models=replace(cfg.models, endpoints=endpoints))
    Scripted.script.clear()
    Scripted.heard.clear()
    Scripted.summaries = 0


class RecordedEvents:
    """A `RunEvents` that keeps what it is handed, for a test to read back."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def record(self, event) -> None:
        self.events.append(dict(event))

    def named(self, name: str) -> list[dict[str, Any]]:
        return [event for event in self.events if event["event"] == name]


@pytest.fixture
def workspace(tmp_path):
    return ensure_layout(tmp_path / "ws")


@pytest.fixture
def definitions(tmp_path):
    """A definitions folder beside the workspace, laid out as seeding leaves one."""
    return ensure_definitions(tmp_path / "definitions")


@pytest.fixture
def session_dir(workspace):
    """One session's directory — the backend root."""
    return ensure_session_layout(workspace / "sessions" / "test-session", DEFAULT_LAYOUT)


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
    wire_format=WIRE_FORMATS["anthropic"],
)

#: A second host, so "somewhere else" is expressible. `indistinct` compares
#: hosts rather than endpoint names, deliberately -- two endpoints may point at
#: one gateway -- so a fixture needs a different netloc, not a different key.
OTHER_ENDPOINT = Endpoint(
    api="anthropic",
    base_url="http://127.0.0.2:9/never-called",
    api_key="test-key-not-real",
    key_env=FAKE_KEY_VAR,
    wire_format=WIRE_FORMATS["anthropic"],
)

#: Two models on one endpoint, which is the shape the catalogue exists to allow
#: and the shape a subagent test needs: `cheap-model` carries params that differ
#: from the default's, so a test asserting a subagent got *its own* ceiling
#: cannot pass by accident on the deployment's.
FAKE_MODELS = {
    "fake-model": ModelProfile(model="fake-model", endpoint="fake"),
    "cheap-model": ModelProfile(
        model="cheap-model", endpoint="fake", max_tokens=321, timeout_s=45
    ),
    #: On the *other* endpoint, and that is its whole job. A subagent written
    #: `distinct: true` refuses a model sharing a host with the default, so a
    #: one-endpoint fixture cannot show a subagent genuinely running elsewhere
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


def models_file(definitions: Path) -> Path:
    """Write that catalogue where a command will read it, and say where."""
    path = Path(definitions) / "models.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(models_document(), sort_keys=False), encoding="utf-8")
    return path


@pytest.fixture
def cfg(workspace, definitions):
    return Config(
        workspace=workspace,
        definitions=definitions,
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
    monkeypatch.setenv("KINGFISHER_DEFINITIONS_DIR", str(cfg.definitions))
    models_file(cfg.definitions)
    monkeypatch.setenv(FAKE_KEY_VAR, "not-a-real-key")
    return cfg


def start(cfg, session_id: str) -> str:
    """Create a named session, as a service would before serving a turn.

    Under `cfg`'s own layout, which is what a service does. Called with no layout, this
    made the default folders whatever the configuration said, so a test of a renamed
    deployment ran against a session holding both spellings -- and a measurement of
    which folder production had made was really a measurement of this helper.
    """
    from kingfisher.plugins.sessions.folders import ensure_session_layout

    ensure_session_layout(cfg.workspace / "sessions" / session_id, cfg.layout)
    return session_id


@pytest.fixture(params=["sync", "async"])
def way(request) -> str:
    """Which way into a session a test reads through: the sync methods or their twins."""
    return request.param


class Through:
    """A session's methods, on the `Session` `kf` hands out, through the way `way` names.

    Shorthand for the two calls a test would otherwise write each time, and both ways
    with one spelling. A session the caller is handed no `Session` for answers as a wrong
    id does, with `UnknownSessionError`. `delete` and `files` act for nobody in
    particular, as an operator's housekeeping does, and `files` makes a session that is
    not there yet.
    """

    def __init__(self, kf, way: str) -> None:
        self._kf = kf
        self._awaited = way == "async"

    def session(self, session_id: str, **kwargs):
        if self._awaited:
            return asyncio.run(self._kf.asession(session_id, **kwargs))
        return self._kf.session(session_id, **kwargs)

    def _on(self, session_id: str, method: str, *args, **session_kwargs):
        from kingfisher.core.session.model import unknown_session

        handle = self.session(session_id, **session_kwargs)
        if handle is None:
            raise unknown_session(session_id)
        if self._awaited:
            return asyncio.run(getattr(handle, f"a{method}")(*args))
        return getattr(handle, method)(*args)

    def pending(self, session_id: str, *, source_ids=None):
        return self._on(session_id, "pending", source_ids=source_ids)

    def artifact(self, session_id: str, name: str, *, source_ids=None):
        return self._on(session_id, "artifact", name, source_ids=source_ids)

    def size(self, session_id: str, *, source_ids=None):
        return self._on(session_id, "size", source_ids=source_ids)

    def delete(self, session_id: str):
        from kingfisher.core.access.source_ids import UNSCOPED

        handle = self.session(session_id, source_ids=UNSCOPED)
        if handle is None:
            return None
        return asyncio.run(handle.adelete()) if self._awaited else handle.delete()

    def files(self, session_id: str):
        from kingfisher.core.access.source_ids import UNSCOPED

        return self._on(session_id, "files", source_ids=UNSCOPED, create=True)


def _backend_in(cfg, session_dir: Path):
    """Kingfisher's own backend for a session kept in this directory, laid out first as
    `LocalSessions` lays out every session it opens.
    """
    from kingfisher.plugins.sessions.backend import backend_at
    from kingfisher.plugins.sessions.folders import ensure_session_layout

    return backend_at(cfg, ensure_session_layout(Path(session_dir), DEFAULT_LAYOUT))


def harness_of(cfg, session_id: str):
    """What kingfisher keeps about a session, as a test reads and writes it."""
    return harness_in(cfg, cfg.workspace / "sessions" / session_id)


def harness_in(cfg, session_dir: Path):
    """The same, for a session directory a test already holds -- including one kept
    somewhere other than under the workspace.
    """
    from kingfisher.core.session.store import HarnessFiles

    return HarnessFiles(_backend_in(cfg, session_dir), Path(session_dir).name)


def paths_in(cfg, session_dir: Path, *, sessions: Path | None = None):
    """What a tool's paths mean in a session kept in this directory, under the rules
    every turn starts from -- the `SessionPaths` a build hands its tool guards. Its
    sibling sessions are under `sessions`, or beside it when that is not given.
    """
    from kingfisher.core.agent.assembly import read_only_permissions
    from kingfisher.core.agent.permitted_backend import PermittedBackend
    from kingfisher.core.agent.session_paths import SessionPaths

    under_rules = PermittedBackend(_backend_in(cfg, session_dir), read_only_permissions())
    return SessionPaths(under_rules, Path(session_dir).parent if sessions is None else sessions)


def on_backend(tool, backend, **args):
    """Call a tool that reads through `runtime.context.backend`, handed `backend`.

    The function under the decorator, called directly: a test of what a tool does
    with a file wants the tool and a backend, not a graph to dispatch it.
    """
    from langchain.tools import ToolRuntime

    from kingfisher import ToolContext

    runtime = ToolRuntime(
        state={},
        context=ToolContext(backend=backend),
        config={},
        stream_writer=lambda _: None,
        tool_call_id="t",
        store=None,
    )
    return getattr(tool, "func", tool)(**args, runtime=runtime)


def a_pdf(*texts: str) -> bytes:
    """A PDF with one page per text, each page showing its text; `""` is a page with
    none, which is what a scan is to text extraction.

    Written out by hand, because nothing installed here draws text onto a new page.
    """
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "",  # the page tree, once its pages are numbered
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    kids = []
    for text in texts:
        content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET" if text else ""
        objects.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream")
        objects.append(
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {len(objects)} 0 R >>"
        )
        kids.append(f"{len(objects)} 0 R")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{at:010d} 00000 n \n".encode() for at in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode()
    out += f"startxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def pin(kf, session_id: str, name: str) -> None:
    """Fix a session's agent before it has run, the way its first turn would.

    The document comes from the service's own catalogue, which is where
    `_pin_agent_in` reads it. A deployment that supplies its own graph never
    resolves an agent at all, so a test whose subject is the pin cannot get one by
    running a turn.
    """
    from kingfisher.core.session.store import remember_agent

    document = kf.catalogue.agents.documents[name]
    drive(remember_agent(harness_of(kf.cfg, session_id), document))


def a_subagent(text: str, name: str) -> SubagentSpec:
    """A subagent definition as if read from a file called `name`."""
    from kingfisher.core.definitions.documents import DefinitionText
    from kingfisher.core.definitions.subagents import reading

    return reading.read(DefinitionText(text, Path(name)))


def declared_subagents(built: Assembled) -> list:
    """The subagent specs a build activated, without the built-in one.

    The specs stay plain dicts: `Assembled` records what `create_deep_agent` was
    handed, and what it is handed for a subagent is deepagents' own `SubAgent`
    mapping. Only the outer record gained a type.
    """
    return [s for s in built.subagents or () if s.get("name") != "general-purpose"]


def subagent_of(built: Assembled, name: str) -> dict:
    """One subagent a build activated, as deepagents received it.

    The outer read is the record and the inner one stays a subscript, because what
    `create_deep_agent` is handed for a subagent is deepagents' own `SubAgent`
    mapping -- only the outer record gained a type.
    """
    (found,) = [s for s in built.subagents or () if s["name"] == name]
    return found


def middleware_of(built: Assembled, name: str) -> list:
    """One subagent's middleware, in the order it was attached.

    Here rather than in each file: two of the three copies were identical down to
    the docstring, and the third was this one's first line under another name.
    """
    return subagent_of(built, name).get("middleware", [])


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
READS_DOTENV = ("kingfisher.cli.__main__", "tests.integration.driver")


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
    # `LocalSessions` reads its shell's settings from here, so a developer who exported
    # `off` would unfence every backend a test builds.
    for setting in ("KINGFISHER_SHELL_SANDBOX", "KINGFISHER_SHELL_PATH_EXTRA"):
        monkeypatch.delenv(setting, raising=False)
    with environment_restored():
        yield


@pytest.fixture(scope="session")
def shipped():
    """The definitions this repository ships, found in this repository."""
    return repository_root() / "assets"


@pytest.fixture
def workspace_with_presets(cfg, shipped):
    """A workspace holding the shipped definitions, as `kingfisher seed` leaves one."""
    import shutil

    # `tools` too, because a subagent that names one is refused before its
    # skills are looked at -- so leaving them out would make a skills test pass
    # by never reaching the check.
    #
    # `agents` stays out: `build_agent` takes capabilities rather than an agent
    # definition, and two of the shipped agents cannot run on a bare checkout.
    for kind in ("skills", "subagents", "tools"):
        shutil.copytree(shipped / kind, cfg.definitions / kind, dirs_exist_ok=True)
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
    from kingfisher.core.agent.kinds.tools import registered_tools

    names = registered_tools(graph)
    assert names is not None, "a graph built here must be readable"
    return names


#: Both tool lists at `["*"]`, for an `an_agent` that has to act: left out, each grants none.
EVERY_TOOL = {"builtin_tools": '["*"]', "tools": '["*"]'}


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
    from kingfisher.core.inventory import Inventory
    from kingfisher.core.origins import Origin, Origins

    nowhere = Origin(kind="default")
    return Inventory(
        origins=Origins(
            workspace=Path("/nowhere"),
            definitions=Path("/nowhere-definitions"),
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
