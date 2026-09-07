from __future__ import annotations

import os
from dataclasses import fields, replace
from pathlib import Path

import pytest

from kingfisher.application import config as config_module
from kingfisher.application.config import config_from_env
from kingfisher.config import ConfigError
from kingfisher.domain.access import AccessError
from tests.conftest import FAKE_CATALOGUE, subagents_dir

CATALOGUE = """
endpoints:
  gateway:
    api: anthropic
    base_url: https://example.invalid/anthropic
    key_env: GATEWAY_API_KEY
  openai:
    api: openai_responses
    base_url: https://example.invalid/v1
    key_env: OPENAI_API_KEY

default: MiniMax-M3

models:
  MiniMax-M3:
    endpoint: gateway
  gpt-5:
    endpoint: openai
"""


@pytest.fixture
def env(tmp_path):
    """A deployment with a catalogue on disk and both its keys present."""
    path = tmp_path / "models.yaml"
    path.write_text(CATALOGUE, encoding="utf-8")
    return {
        "KINGFISHER_WORKSPACE": str(tmp_path / "ws"),
        "KINGFISHER_MODELS_FILE": str(path),
        "GATEWAY_API_KEY": "sk-gateway",
        "OPENAI_API_KEY": "sk-openai",
    }


# -- the catalogue is required ---------------------------------------------


def test_the_catalogue_is_required_with_no_default(tmp_path):
    """No fallback and no shipped table, for the reason `KINGFISHER_API_STYLE` was
    required and had none: a default silently picks a destination nobody chose the
    first time kingfisher is pointed somewhere new.
    """
    with pytest.raises(ConfigError, match="no model catalogue at"):
        config_from_env({"KINGFISHER_WORKSPACE": str(tmp_path / "ws")})


def test_the_absent_file_error_shows_a_working_example(tmp_path):
    """It is the first thing a new deployment hits, replacing a message that came with
    an unusually explanatory `.env.example`.
    """
    with pytest.raises(ConfigError) as raised:
        config_from_env({"KINGFISHER_WORKSPACE": str(tmp_path / "ws")})

    message = str(raised.value)
    for expected in ("endpoints:", "api: anthropic", "key_env:", "default:", "models:"):
        assert expected in message


def test_it_defaults_inside_the_workspace(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "models.yaml").write_text(CATALOGUE, encoding="utf-8")

    cfg = config_from_env({"KINGFISHER_WORKSPACE": str(workspace), "GATEWAY_API_KEY": "sk-gateway"})

    assert cfg.models.source == workspace / "models.yaml"


# -- what it resolves to ---------------------------------------------------


def test_endpoints_and_models_come_from_the_file(env):
    cfg = config_from_env(env)

    assert set(cfg.models.endpoints) == {"gateway", "openai"}
    assert set(cfg.models.models) == {"MiniMax-M3", "gpt-5"}
    assert cfg.models.default == "MiniMax-M3"


def test_credentials_come_from_the_variable_each_endpoint_names(env):
    """Keys are named, not written: the file is meant to be reviewed and shared, which a
    file holding credentials could not be.
    """
    cfg = config_from_env(env)

    assert cfg.models.endpoints["gateway"].api_key == "sk-gateway"
    assert cfg.models.endpoints["openai"].api_key == "sk-openai"
    assert "sk-gateway" not in CATALOGUE


def test_an_endpoint_without_its_key_is_dropped_and_warned_about(env):
    """One reviewed file across a fleet is the point of `key_env`, so a machine holding
    only some of the keys must still start.
    """
    del env["OPENAI_API_KEY"]

    with pytest.warns(UserWarning, match="OPENAI_API_KEY"):
        cfg = config_from_env(env)

    assert set(cfg.models.endpoints) == {"gateway"}
    assert set(cfg.models.models) == {"MiniMax-M3"}  # its models went with it


def test_a_default_whose_endpoint_has_no_key_is_refused(env):
    """Dropping is for endpoints nothing needs."""
    del env["GATEWAY_API_KEY"]

    with pytest.raises(ConfigError, match="no credentials"), pytest.warns(UserWarning):
        config_from_env(env)


def test_a_default_naming_nothing_is_a_different_error(env, tmp_path):
    """A broken file and an unfinished deployment read differently and are worded
    differently.
    """
    (tmp_path / "models.yaml").write_text(
        CATALOGUE.replace("default: MiniMax-M3", "default: typo-5"), encoding="utf-8"
    )

    with pytest.raises(ConfigError, match="is not defined here"):
        config_from_env(env)


# -- the rest of the environment -------------------------------------------


def test_hosted_tracing_is_disabled_explicitly(monkeypatch):
    """Q13: a stray var from another project must not start exporting."""
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    config_module.enforce_local_only_tracing()
    assert os.environ["LANGSMITH_TRACING"] == "false"
    assert os.environ["LANGCHAIN_TRACING_V2"] == "false"


def test_state_defaults_to_the_workspace(env):
    """Unset means self-contained: nothing is written outside the workspace."""
    cfg = config_from_env(env)

    assert cfg.state_root is None
    assert cfg.state_dir == cfg.workspace / ".kingfisher"


def test_state_can_be_pointed_elsewhere(env, tmp_path):
    """Host-side state is relocatable; the agent addresses none of it by path."""
    cfg = config_from_env({**env, "KINGFISHER_STATE_DIR": str(tmp_path / "state")})

    assert cfg.state_dir == tmp_path / "state"


def test_there_is_no_knob_for_the_agent_s_tmpdir(env):
    """`TMPDIR` is a session directory, so it cannot be pointed out of the session.

    `KINGFISHER_SCRATCH_DIR` existed to move it, and a scratch directory that is
    both per-session and elsewhere is not expressible: `session_bytes` counts one
    directory, so anywhere else is a cost the quota cannot see.
    """
    cfg = config_from_env({**env, "KINGFISHER_SCRATCH_DIR": "/tmp/somewhere"})

    assert not hasattr(cfg, "scratch_dir")
    assert not hasattr(cfg, "scratch_root")


def test_the_catalogue_defaults_inside_the_workspace(env):
    """Unset changes nothing: definitions stay where they have always been."""
    cfg = config_from_env(env)

    assert cfg.skills_root is None
    assert cfg.subagents_root is None
    assert cfg.skills_dir == cfg.workspace / "skills"
    assert subagents_dir(cfg) == cfg.workspace / "subagents"


def test_the_catalogue_can_be_shared_between_workspaces(env, tmp_path):
    """The point of the phase: one reviewed set of definitions, deployed once, rather
    than a copy per workspace that nobody can audit centrally.
    """
    cfg = config_from_env(
        {
            **env,
            "KINGFISHER_SKILLS_DIR": str(tmp_path / "catalogue" / "skills"),
            "KINGFISHER_SUBAGENTS_DIR": str(tmp_path / "catalogue" / "subagents"),
        }
    )

    assert cfg.skills_dir == tmp_path / "catalogue" / "skills"
    assert subagents_dir(cfg) == tmp_path / "catalogue" / "subagents"


def test_the_file_shows_exactly_the_knobs_that_exist():
    """`.env.example` is the only place a deployment learns a knob exists -- and, since
    nothing anywhere warns about an unknown `KINGFISHER_` variable, the only place it
    learns one does not.
    """
    import re
    from pathlib import Path as _Path

    from kingfisher.application import config as config_module

    # The repository, found by its marker rather than counted in parents: this
    # is two levels further up since the library moved under `packages/`, and a
    # count would have gone looking for `.env.example` inside the package.
    root = next(
        p for p in _Path(__file__).resolve().parents if (p / ".env.example").is_file()
    )
    # Asked of the module rather than spelled as a path: this test shipped
    # naming `src/kingfisher/app/config.py`, one rename after that directory
    # stopped existing, and went red on main rather than at review.
    source = _Path(config_module.__file__).read_text()
    # Quoted, so this is a name the module looks *up* rather than one it
    # mentions in a comment -- which is the half the old rule got wrong.
    read = set(re.findall(r'"(KINGFISHER_[A-Z_]+)"', source))
    # Read, deliberately undocumented as something to set, and listed under an
    # arrow instead. Subtracted from what the file must show rather than added
    # to it: see the docstring.
    read -= set(config_module.RENAMED.values())
    # `#?` because a knob with no sensible default is shown commented out, and
    # a line nobody uncommented still documents it.
    shown = set(
        re.findall(r"^#?\s*(KINGFISHER_[A-Z_]+)=", (root / ".env.example").read_text(), re.M)
    )

    assert shown == read, (
        f"read by config.py but not shown in .env.example: {sorted(read - shown)}; "
        f"shown in .env.example but read by nothing: {sorted(shown - read)}"
    )


def test_no_message_names_a_variable_nothing_reads():
    """The other direction, and the one that had gone wrong."""
    import ast
    import re
    from pathlib import Path as _Path

    import kingfisher
    from kingfisher.application import config as config_module

    read = set(re.findall(r"KINGFISHER_[A-Z_]+", _Path(config_module.__file__).read_text()))
    package = _Path(kingfisher.__file__).parent

    def messages(tree: ast.Module) -> list[str]:
        """Every string constant that is not a docstring."""
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        }
        return [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ]

    invented = {
        (path.relative_to(package).as_posix(), name)
        for path in package.rglob("*.py")
        for text in messages(ast.parse(path.read_text(encoding="utf-8")))
        for name in re.findall(r"KINGFISHER_[A-Z_]+", text)
        if name not in read
    }

    assert not invented, (
        f"named in a runtime string but read nowhere: {sorted(invented)} -- a message "
        "pointing at a variable that does not exist sends its reader to set "
        "something with no effect, and nothing else would ever notice"
    )

def test_the_variables_that_chose_a_model_are_gone(env):
    """`KINGFISHER_MODEL`, `KINGFISHER_API_STYLE` and `KINGFISHER_MAX_TOKENS` are the
    catalogue's job now, and the per-role pair went before them.
    """
    cfg = config_from_env(
        {
            **env,
            "KINGFISHER_MODEL": "ignored",
            "KINGFISHER_API_STYLE": "openai",
            "KINGFISHER_MAX_TOKENS": "999999",
            "KINGFISHER_MODEL_SUBAGENT": "cheap-model",
            "KINGFISHER_PROVIDER_SUBAGENT": "openai",
        }
    )

    assert cfg.models.default == "MiniMax-M3"  # the file said so, not the environment
    assert cfg.models.resolve()[0].endpoint == "gateway"
    assert cfg.models.models["MiniMax-M3"].max_tokens == 4096
    assert not [f for f in fields(cfg) if f.name in {"model", "api_style", "max_tokens"}]
    assert "ignored" not in repr(cfg)


def test_the_execution_timeout_is_named_for_what_it_bounds(env):
    """It was `KINGFISHER_TIMEOUT_S` and bounded a model call as well as the shell and
    the interpreter -- three unrelated jobs for one number.
    """
    cfg = config_from_env({**env, "KINGFISHER_EXECUTION_TIMEOUT_S": "45"})

    assert cfg.execution_timeout_s == 45
    assert not [f for f in fields(cfg) if f.name == "timeout_s"]

def test_the_paths_half_honours_the_catalogue_overrides(monkeypatch, tmp_path):
    """The reason seeding reads `paths_from_env` rather than one env var."""
    from kingfisher import paths_from_env

    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("KINGFISHER_SKILLS_DIR", str(tmp_path / "elsewhere" / "skills"))

    roots = paths_from_env().catalogue_roots

    assert roots["skills"] == tmp_path / "elsewhere" / "skills"
    assert roots["subagents"] == tmp_path / "ws" / "subagents"  # the others still default


def test_the_two_records_cannot_disagree_about_where_things_go(tmp_path):
    """`Config` and `WorkspacePaths` answer the same question, and one of them is used
    to seed while the other is used to serve.
    """
    from kingfisher.config import Config, WorkspacePaths

    overrides = {"skills_root": tmp_path / "s", "tools_root": tmp_path / "t"}
    paths = WorkspacePaths(workspace=tmp_path / "ws", **overrides)
    cfg = Config(
        workspace=tmp_path / "ws",
        models=FAKE_CATALOGUE,
        turn_timeout_s=1,
        execution_timeout_s=1,
        **overrides,
    )

    assert paths.catalogue_roots == cfg.catalogue_roots


def test_the_two_records_cannot_disagree_about_the_authored_files(tmp_path):
    """The same rule as above for the two files a deployment writes itself."""
    from kingfisher.config import Config, WorkspacePaths

    elsewhere = tmp_path / "shared"
    paths = WorkspacePaths(
        workspace=tmp_path / "ws",
        models_file=elsewhere / "models.yaml",
        groups_file=elsewhere / "groups.yaml",
    )
    cfg = Config(
        workspace=tmp_path / "ws",
        models=replace(FAKE_CATALOGUE, source=elsewhere / "models.yaml"),
        access_source=elsewhere / "groups.yaml",
        turn_timeout_s=1,
        execution_timeout_s=1,
    )

    assert paths.authored_files == cfg.authored_files
    assert paths.authored_files["models.yaml"] == elsewhere / "models.yaml"


def test_the_authored_files_default_into_the_workspace(tmp_path):
    """The ordinary deployment, which relocates neither."""
    from kingfisher.config import WorkspacePaths

    files = WorkspacePaths(workspace=tmp_path / "ws").authored_files

    assert files == {
        "models.yaml": tmp_path / "ws" / "models.yaml",
        "groups.yaml": tmp_path / "ws" / "groups.yaml",
    }


def test_seeding_sees_a_relocated_catalogue(tmp_path, monkeypatch):
    """`paths_from_env` is what a first run has, and it has to carry these."""
    from kingfisher import paths_from_env

    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("KINGFISHER_MODELS_FILE", str(tmp_path / "shared" / "models.yaml"))
    monkeypatch.delenv("KINGFISHER_GROUPS_FILE", raising=False)

    files = paths_from_env().authored_files

    assert files["models.yaml"] == tmp_path / "shared" / "models.yaml"
    assert files["groups.yaml"] == tmp_path / "ws" / "groups.yaml"


def test_a_config_is_a_seeding_destination(tmp_path):
    """Both records satisfy the protocol the seeder asks for, by shape and without
    either being told about it.
    """
    from kingfisher.config import WorkspacePaths
    from kingfisher.infrastructure.workspace.seeding import Destination

    assert isinstance(WorkspacePaths(workspace=tmp_path), Destination)


# -- the group vocabulary ------------------------------------------------------


def test_a_workspace_without_a_vocabulary_file_has_none(env):
    """Absent is the whole of what "this deployment controls nothing by group" means,
    and it is what every deployment that predates the field has.
    """
    assert config_from_env(env).access is None


def test_a_vocabulary_in_the_workspace_is_read(env):
    workspace = Path(env["KINGFISHER_WORKSPACE"])
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "groups.yaml").write_text("groups: [A, B]\n", encoding="utf-8")
    access = config_from_env(env).access
    assert access is not None
    assert set(access.names) == {"A", "B"}


def test_the_vocabulary_file_can_be_relocated(env, tmp_path):
    """It can be deployed once and shared by several workspaces, the way a model
    catalogue can -- it holds content a person authored and reviewed.
    """
    elsewhere = tmp_path / "vocab.yaml"
    elsewhere.write_text("groups: [B]\n", encoding="utf-8")
    access = config_from_env({**env, "KINGFISHER_GROUPS_FILE": str(elsewhere)}).access
    assert access is not None
    assert access.names == {"B": ("B",)}


def test_a_relocated_vocabulary_wins_over_one_in_the_workspace(env, tmp_path):
    workspace = Path(env["KINGFISHER_WORKSPACE"])
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "groups.yaml").write_text("groups: [A]\n", encoding="utf-8")
    elsewhere = tmp_path / "vocab.yaml"
    elsewhere.write_text("groups: [B]\n", encoding="utf-8")
    access = config_from_env({**env, "KINGFISHER_GROUPS_FILE": str(elsewhere)}).access
    assert access is not None
    assert set(access.names) == {"B"}


def test_a_vocabulary_that_will_not_parse_stops_the_deployment(env):
    """Fail closed."""
    workspace = Path(env["KINGFISHER_WORKSPACE"])
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "groups.yaml").write_text("groups: [A\n", encoding="utf-8")
    with pytest.raises(AccessError):
        config_from_env(env)


# -- the environment, bound once -------------------------------------------


def test_a_reader_answers_from_the_mapping_it_was_given(monkeypatch):
    """The property that makes it testable at all: it reads what it was handed, never
    the process.
    """
    from kingfisher.application.config import Environment

    monkeypatch.setenv("KINGFISHER_RECURSION_LIMIT", "9999")
    reading = Environment({"KINGFISHER_RECURSION_LIMIT": "7"})

    assert reading.number("KINGFISHER_RECURSION_LIMIT", 150) == 7


def test_one_path_reading_now_rather_than_two(tmp_path):
    """`_optional_path` was defined twice -- once inside `paths_from_env`, once inside
    `config_from_env` -- because a closure over `environ` was the only way to share
    it while `environ` was a parameter.
    """
    from kingfisher.application.config import Environment

    reading = Environment({"KINGFISHER_WORKSPACE": "ws", "KINGFISHER_TOOLS_DIR": "tools"})

    assert reading.optional_path("KINGFISHER_TOOLS_DIR") == Path("tools").resolve()
    assert reading.paths().tools_root == reading.optional_path("KINGFISHER_TOOLS_DIR")
    assert reading.optional_path("KINGFISHER_UNSET") is None


def test_the_exported_functions_are_the_class(tmp_path, monkeypatch):
    """`paths_from_env` stays a function because callers import it, and it has to keep
    answering identically -- so it is asserted against the reader rather than left to
    look obvious.
    """
    from kingfisher.application.config import Environment, paths_from_env

    environ = {"KINGFISHER_WORKSPACE": str(tmp_path / "ws")}

    assert paths_from_env(environ) == Environment(environ).paths()


def test_a_reader_with_no_mapping_reads_the_process(monkeypatch, tmp_path):
    """The default that `os.environ if environ is None` used to express, now in one
    place instead of at the top of every function.
    """
    from kingfisher.application.config import Environment

    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(tmp_path / "ws"))

    assert Environment.current().paths().workspace == (tmp_path / "ws").resolve()
