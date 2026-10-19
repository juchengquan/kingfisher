from __future__ import annotations

import os
from dataclasses import fields
from pathlib import Path

import pytest

from kingfisher.core import environment as config_module
from kingfisher.core.access.source_ids import AccessError
from kingfisher.core.config import DEFAULT_THREAD_POOL_SIZE, FOLDED_SETTINGS, Config, ConfigError
from kingfisher.core.definitions.catalogue import DEFINITION_KINDS
from kingfisher.core.environment import Environment, config_from_env
from tests.conftest import FAKE_CATALOGUE, repository_root, subagents_dir

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


def bare(tmp_path: Path) -> dict[str, str]:
    """A workspace and a definitions folder holding nothing at all."""
    definitions = tmp_path / "definitions"
    definitions.mkdir(exist_ok=True)
    return {
        "KINGFISHER_WORKSPACE": str(tmp_path / "ws"),
        "KINGFISHER_DEFINITIONS_DIR": str(definitions),
    }


@pytest.fixture
def env(tmp_path):
    """A deployment with a catalogue on disk and both its keys present."""
    values = bare(tmp_path)
    (tmp_path / "definitions" / "models.yaml").write_text(CATALOGUE, encoding="utf-8")
    return {**values, "GATEWAY_API_KEY": "sk-gateway", "OPENAI_API_KEY": "sk-openai"}


# -- the catalogue is required ---------------------------------------------


def test_the_catalogue_is_required_with_no_default(tmp_path):
    """No fallback and no shipped table, for the reason `KINGFISHER_API_STYLE` was
    required and had none: a default silently picks a destination nobody chose the
    first time kingfisher is pointed somewhere new.
    """
    with pytest.raises(ConfigError, match="no model catalogue at"):
        config_from_env(bare(tmp_path))


def test_the_absent_file_error_shows_a_working_example(tmp_path):
    """It is the first thing a new deployment hits, replacing a message that came with
    an unusually explanatory `.env.example`.
    """
    with pytest.raises(ConfigError) as raised:
        config_from_env(bare(tmp_path))

    message = str(raised.value)
    for expected in ("endpoints:", "api: anthropic", "key_env:", "default:", "models:"):
        assert expected in message


def test_the_catalogue_is_read_from_the_definitions_folder(env, tmp_path):
    """A `models.yaml` read from anywhere else is one a deployment did not review."""
    assert config_from_env(env).models.source == tmp_path / "definitions" / "models.yaml"


# -- the two folders ---------------------------------------------------------


def test_the_definitions_folder_is_required(tmp_path):
    """A default would put it inside the workspace again, which is the mix this ends."""
    with pytest.raises(ConfigError, match="KINGFISHER_DEFINITIONS_DIR is required"):
        config_from_env({"KINGFISHER_WORKSPACE": str(tmp_path / "ws")})


def test_a_definitions_folder_that_is_not_there_is_refused_rather_than_made(env, tmp_path):
    """A start that made one would hide a mount that failed behind an empty catalogue,
    and write into a folder several deployments may read.
    """
    missing = tmp_path / "not-mounted"

    with pytest.raises(ConfigError, match="not a directory"):
        config_from_env({**env, "KINGFISHER_DEFINITIONS_DIR": str(missing)})

    assert not missing.exists()


def test_seeding_needs_the_definitions_folder_and_nothing_else(tmp_path):
    """Seeding writes into the definitions folder alone, so a deployment that only
    seeds is not asked for a workspace -- while serving one still is.
    """
    only = {"KINGFISHER_DEFINITIONS_DIR": str(tmp_path / "definitions")}

    paths = Environment(only).paths()

    assert paths.workspace is None
    assert paths.catalogue_roots["skills"] == tmp_path / "definitions" / "skills"
    with pytest.raises(ConfigError, match="KINGFISHER_WORKSPACE is required"):
        config_from_env(only)


@pytest.mark.parametrize(
    ("workspace", "definitions"),
    [("same", "same"), ("defs/ws", "defs"), ("ws", "ws/defs")],
    ids=["one-folder", "workspace-inside", "definitions-inside"],
)
def test_two_folders_that_overlap_are_refused(env, tmp_path, workspace, definitions):
    """Either way round loses what the split is for: sessions written into the folder
    people review, or definitions where every session's shell can read them.

    Asked of the environment, of seeding's half of it and of a `Config` built in code,
    because each is a way in that would otherwise lay one folder over the other.
    """
    from kingfisher.core.config import Config

    (tmp_path / definitions).mkdir(parents=True)
    values = {
        **env,
        "KINGFISHER_WORKSPACE": str(tmp_path / workspace),
        "KINGFISHER_DEFINITIONS_DIR": str(tmp_path / definitions),
    }

    with pytest.raises(ConfigError, match="neither may be inside the other"):
        config_from_env(values)
    with pytest.raises(ConfigError, match="neither may be inside the other"):
        Environment(values).paths()
    with pytest.raises(ConfigError, match="neither may be inside the other"):
        Config(
            workspace=tmp_path / workspace,
            definitions=tmp_path / definitions,
            models=FAKE_CATALOGUE,
        )


def test_two_folders_side_by_side_are_not_refused(env):
    """The control beside the refusals: a rule refusing every pair passes all of them."""
    assert config_from_env(env).workspace.parent == config_from_env(env).definitions.parent


@pytest.mark.parametrize("setting", sorted(FOLDED_SETTINGS))
def test_a_setting_that_moved_one_kind_is_refused(env, tmp_path, setting):
    """Ignored, it would serve the definitions folder's copy where the deployment chose
    another -- and seeding would copy into a folder it was never told about.
    """
    values = {**env, setting: str(tmp_path / "elsewhere")}

    with pytest.raises(ConfigError, match=f"{setting} was removed"):
        config_from_env(values)
    with pytest.raises(ConfigError, match=f"{setting} was removed"):
        Environment(values).paths()


# -- what it resolves to ---------------------------------------------------


def test_the_thread_pool_size_is_read_from_its_setting(env):
    """Read but not passed on, every service would run on the default and say nothing."""
    assert config_from_env(env).thread_pool_size == DEFAULT_THREAD_POOL_SIZE
    assert config_from_env({**env, "KINGFISHER_THREAD_POOL_SIZE": "8"}).thread_pool_size == 8


LIMITS = {
    "execution_timeout_s": "KINGFISHER_EXECUTION_TIMEOUT_S",
    "turn_timeout_s": "KINGFISHER_TURN_TIMEOUT_S",
    "session_max_bytes": "KINGFISHER_SESSION_MAX_BYTES",
    "session_ttl_s": "KINGFISHER_SESSION_TTL_S",
    "recursion_limit": "KINGFISHER_RECURSION_LIMIT",
    "thread_pool_size": "KINGFISHER_THREAD_POOL_SIZE",
}


def test_an_unset_limit_is_the_one_a_library_caller_gets(env):
    """A default written again in the reader would let the command and an embedded
    `Config` run on different limits with the suite green.
    """
    defaults = {f.name: f.default for f in fields(Config) if f.name in LIMITS}
    built = config_from_env(env)

    assert set(defaults) == set(LIMITS)
    assert {name: getattr(built, name) for name in LIMITS} == defaults


@pytest.mark.parametrize(("name", "key"), sorted(LIMITS.items()))
def test_a_limit_that_is_set_is_passed_on(env, name, key):
    """The control beside the defaults: a reader that ignored the setting passes that."""
    assert getattr(config_from_env({**env, key: "7"}), name) == 7


def test_the_keys_file_resolves_like_the_other_path_settings(env, tmp_path, monkeypatch):
    """`~` expanded and a relative path taken from where kingfisher started, where `.env`
    is read -- not from the workspace, which the macOS fence lets the shell read.
    """
    (tmp_path / "keys.yaml").write_text("unused: sk-x\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))

    relative = config_from_env({**env, "KINGFISHER_MODEL_KEYS_FILE": "keys.yaml"})
    home = config_from_env({**env, "KINGFISHER_MODEL_KEYS_FILE": "~/keys.yaml"})

    assert relative.models.keys_file == (tmp_path / "keys.yaml").resolve()
    assert home.models.keys_file == (tmp_path / "keys.yaml").resolve()
    assert config_from_env(env).models.keys_file is None, "named by nothing, read from nowhere"


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
    (tmp_path / "definitions" / "models.yaml").write_text(
        CATALOGUE.replace("default: MiniMax-M3", "default: typo-5"), encoding="utf-8"
    )

    with pytest.raises(ConfigError, match="is not defined here"):
        config_from_env(env)


# -- the rest of the environment -------------------------------------------


def test_hosted_tracing_is_disabled_explicitly(monkeypatch):
    """A stray var from another project must not start exporting."""
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    config_module.enforce_local_only_tracing()
    assert os.environ["LANGSMITH_TRACING"] == "false"
    assert os.environ["LANGCHAIN_TRACING_V2"] == "false"


def test_the_host_side_roots_are_no_longer_settings(env):
    """Both existed to move harness state out of the workspace, and there is none
    left to move: claims, pinned agents and conversations are inside their
    sessions, run events go to a logger, and what remains in `.kingfisher` -- the
    marker, the sandbox profile and the skills view -- describes the workspace rather
    than any session in it.

    `KINGFISHER_SCRATCH_DIR` could not have survived either way. `TMPDIR` is a
    session directory now, and `session_bytes` counts one directory, so a scratch
    directory that is both per-session and elsewhere is a cost the quota cannot
    see -- which is half of why it moved.
    """
    cfg = config_from_env(
        {
            **env,
            "KINGFISHER_STATE_DIR": "/somewhere/else",
            "KINGFISHER_SCRATCH_DIR": "/tmp/somewhere",
        }
    )

    for gone in ("state_dir", "state_root", "scratch_dir", "scratch_root"):
        assert not hasattr(cfg, gone), f"{gone} is still a setting"


def test_every_kind_is_read_from_its_own_name_in_the_definitions_folder(env, tmp_path):
    """Asked of the kinds rather than listed, so a sixth kind meets this the day it is
    added -- the way `middlewares` arrived once with no way to reach it.
    """
    cfg = config_from_env(env)
    definitions = tmp_path / "definitions"

    assert cfg.catalogue_roots == {kind: definitions / kind for kind in DEFINITION_KINDS}
    assert cfg.skills_dir == definitions / "skills"
    assert subagents_dir(cfg) == definitions / "subagents"
    assert cfg.authored_files == {
        "models.yaml": definitions / "models.yaml",
        "source_ids.yaml": definitions / "source_ids.yaml",
        "INSTRUCTIONS.md": definitions / "INSTRUCTIONS.md",
    }


def test_the_definitions_can_be_shared_between_workspaces(env, tmp_path):
    """The point of the folder: one reviewed set of definitions, deployed once, rather
    than a copy per workspace that nobody can audit centrally.
    """
    one = config_from_env(env)
    other = config_from_env({**env, "KINGFISHER_WORKSPACE": str(tmp_path / "other")})

    assert one.workspace != other.workspace
    assert one.catalogue_roots == other.catalogue_roots


def _looked_up() -> set[str]:
    """Every `KINGFISHER_` name read from the environment: the core's settings, the
    shell's, and the sessions and plugins the command runs on.

    Quoted, so this is a name a module looks *up* rather than one it mentions in a
    comment -- which is the half the old rule got wrong. Asked of the modules rather
    than spelled as paths: a rule naming `src/kingfisher/app/config.py` shipped one
    rename after that directory stopped existing, and went red on main rather than at
    review.
    """
    import re
    from pathlib import Path as _Path

    from kingfisher.cli import configured
    from kingfisher.core import environment
    from kingfisher.plugins.sessions.sandbox import confinement

    return {
        name
        for module in (environment, confinement, configured)
        for name in re.findall(r'"(KINGFISHER_[A-Z_]+)"', _Path(module.__file__).read_text())
    }


def test_the_file_shows_exactly_the_knobs_that_exist():
    """`.env.example` is the only place a deployment learns a knob exists -- and, since
    nothing anywhere warns about an unknown `KINGFISHER_` variable, the only place it
    learns one does not.
    """
    import re

    root = repository_root()
    read = _looked_up()
    # `#?` because a knob with no sensible default is shown commented out, and
    # a line nobody uncommented still documents it.
    shown = set(
        re.findall(r"^#?\s*(KINGFISHER_[A-Z_]+)=", (root / ".env.example").read_text(), re.M)
    )

    assert shown == read, (
        f"read but not shown in .env.example: {sorted(read - shown)}; "
        f"shown in .env.example but read by nothing: {sorted(shown - read)}"
    )


def test_no_message_names_a_variable_nothing_reads():
    """The other direction, and the one that had gone wrong."""
    import ast
    import re
    from pathlib import Path as _Path

    import kingfisher
    from kingfisher.cli import health

    read = _looked_up()
    # A name `doctor` reports as retired is the one kind of message that names a
    # variable nothing reads *on purpose* -- saying so is the whole of what it is
    # for. Allowed rather than exempted by file, so a message inventing a name
    # anywhere, `health.py` included, still fails.
    # And a name refused where the environment is read, for the same reason from the
    # other side: the refusal names it so that whoever set it knows what went.
    from kingfisher.core.config import REMOVED_SETTINGS, RENAMED_SETTINGS

    allowed = (
        read
        | set(health.RETIRED)
        | set(health.RETIRED_PREFIXES)
        | set(REMOVED_SETTINGS)
        | set(RENAMED_SETTINGS)
        | set(FOLDED_SETTINGS)
    )
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
        if name not in allowed
    }

    assert not invented, (
        f"named in a runtime string but read nowhere: {sorted(invented)} -- a message "
        "pointing at a variable that does not exist sends its reader to set "
        "something with no effect, and nothing else would ever notice"
    )

    # The other half of the allowance above: a name is retired or it is read, and
    # never both. Without this, moving a live setting into `RETIRED` would tell
    # every deployment to stop setting something that still works.
    assert not set(health.RETIRED) & read, (
        f"{sorted(set(health.RETIRED) & read)} are reported as retired and still "
        "read -- `doctor` would tell a deployment to drop a setting that works"
    )


def test_every_retired_name_is_one_the_file_lists():
    """`RETIRED` is what `doctor` reports and `.env.example` is where a deployment
    reads about it, so a name in one and not the other is a deployment told nothing
    -- or told to stop setting something this list invented.

    The typo case is the one worth catching: a misspelled key warns about a
    variable nobody has, and stays silent about the one they do.
    """
    import re

    from kingfisher.cli import health

    root = repository_root()
    # The two lists in that file spell a retired name to the left of an arrow,
    # which is how it shows one without re-advertising it as an assignment.
    listed = set(
        re.findall(
            r"^#\s+(KINGFISHER_[A-Z_]+)\s+->", (root / ".env.example").read_text(), re.M
        )
    )

    assert listed == set(health.RETIRED), (
        f"reported by doctor but not listed in .env.example: "
        f"{sorted(set(health.RETIRED) - listed)}; listed but reported by nothing: "
        f"{sorted(listed - set(health.RETIRED))}"
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


def test_the_two_records_cannot_disagree_about_where_things_go(tmp_path):
    """`Config` and `WorkspacePaths` answer the same question, and one of them is used
    to seed while the other is used to serve.
    """
    from kingfisher.core.config import Config, WorkspacePaths

    paths = WorkspacePaths(definitions=tmp_path / "defs", workspace=tmp_path / "ws")
    cfg = Config(
        workspace=tmp_path / "ws",
        definitions=tmp_path / "defs",
        models=FAKE_CATALOGUE,
        turn_timeout_s=1,
        execution_timeout_s=1,
    )

    assert paths.catalogue_roots == cfg.catalogue_roots
    assert paths.authored_files == cfg.authored_files


def test_a_config_is_a_seeding_destination(tmp_path, cfg):
    """Both records satisfy the protocol the seeder asks for, by shape and without
    either being told about it.
    """
    from kingfisher.core.config import WorkspacePaths
    from kingfisher.core.definitions.seeding import Destination

    assert isinstance(WorkspacePaths(definitions=tmp_path), Destination)
    assert isinstance(cfg, Destination)


# -- the source-id vocabulary ------------------------------------------------------


def test_a_workspace_without_a_vocabulary_file_has_none(env):
    """Absent is the whole of what "this deployment controls nothing by source id" means,
    and it is what every deployment that predates the field has.
    """
    assert config_from_env(env).access is None


def test_a_vocabulary_in_the_definitions_folder_is_read(env, tmp_path):
    (tmp_path / "definitions" / "source_ids.yaml").write_text(
        "source_ids: [A, B]\n", encoding="utf-8"
    )
    access = config_from_env(env).access
    assert access is not None
    assert set(access.names) == {"A", "B"}


def test_a_vocabulary_that_will_not_parse_stops_the_deployment(env, tmp_path):
    """Fail closed."""
    (tmp_path / "definitions" / "source_ids.yaml").write_text(
        "source_ids: [A\n", encoding="utf-8"
    )
    with pytest.raises(AccessError):
        config_from_env(env)


# -- the environment, bound once -------------------------------------------


def test_a_reader_answers_from_the_mapping_it_was_given(monkeypatch):
    """The property that makes it testable at all: it reads what it was handed, never
    the process.
    """
    from kingfisher.core.environment import Environment

    monkeypatch.setenv("KINGFISHER_RECURSION_LIMIT", "9999")
    reading = Environment({"KINGFISHER_RECURSION_LIMIT": "7"})

    assert reading.optional_number("KINGFISHER_RECURSION_LIMIT") == 7


def test_one_path_reading_now_rather_than_two(tmp_path):
    """`_optional_path` was defined twice -- once inside `paths_from_env`, once inside
    `config_from_env` -- because a closure over `environ` was the only way to share
    it while `environ` was a parameter.
    """
    from kingfisher.core.environment import Environment

    reading = Environment({"KINGFISHER_DEFINITIONS_DIR": "defs", "KINGFISHER_ASSETS": "assets"})

    assert reading.optional_path("KINGFISHER_ASSETS") == Path("assets").resolve()
    assert reading.paths().assets == reading.optional_path("KINGFISHER_ASSETS")
    assert reading.optional_path("KINGFISHER_UNSET") is None


def test_the_exported_functions_are_the_class(tmp_path, monkeypatch):
    """`paths_from_env` stays a function because callers import it, and it has to keep
    answering identically -- so it is asserted against the reader rather than left to
    look obvious.
    """
    from kingfisher.core.environment import Environment, paths_from_env

    environ = {
        "KINGFISHER_WORKSPACE": str(tmp_path / "ws"),
        "KINGFISHER_DEFINITIONS_DIR": str(tmp_path / "defs"),
    }

    assert paths_from_env(environ) == Environment(environ).paths()


def test_a_reader_with_no_mapping_reads_the_process(monkeypatch, tmp_path):
    """The default that `os.environ if environ is None` used to express, now in one
    place instead of at the top of every function.
    """
    from kingfisher.core.environment import Environment

    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("KINGFISHER_DEFINITIONS_DIR", str(tmp_path / "defs"))

    assert Environment.current().paths().workspace == (tmp_path / "ws").resolve()


def test_the_two_forms_of_the_test_catalogue_agree(tmp_path):
    """One is the `Models` record the `cfg` fixture holds; the other is the file a
    command test points at, because the command reads a file and nothing else.

    They were separate declarations and had already come apart -- the file named one
    endpoint and one model where the record has two and three -- so a model added to
    the fixture reached only half the suite, and the halves disagreed about what this
    deployment can run.
    """
    from kingfisher.core.model_catalogue import load
    from tests.conftest import FAKE_CATALOGUE, FAKE_KEY_VAR, models_file

    loaded = load(models_file(tmp_path), {FAKE_KEY_VAR: "not-a-real-key"})

    assert loaded.models == FAKE_CATALOGUE.models
    assert loaded.default == FAKE_CATALOGUE.default
    # Everything but the key itself, which differs by construction and only there: a
    # file names the variable to read, a record holds what was read from it.
    assert {n: (e.api, e.base_url, e.key_env) for n, e in loaded.endpoints.items()} == {
        n: (e.api, e.base_url, e.key_env) for n, e in FAKE_CATALOGUE.endpoints.items()
    }


# -- the session is the request's to name ------------------------------------


def test_the_session_id_setting_is_refused_rather_than_ignored(env):
    """Named rather than read off the table of removed settings, which a test
    parametrized over it would pass with the entry gone. Ignored, a deployment's
    requests naming no session would run in `default` rather than the one it chose.
    """
    with pytest.raises(ConfigError, match="KINGFISHER_SESSION_ID was removed: name the session"):
        config_from_env({**env, "KINGFISHER_SESSION_ID": "xxx"})
