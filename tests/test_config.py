from __future__ import annotations

import os
from dataclasses import fields

import pytest

from kingfisher.application import config as config_module
from kingfisher.application.config import from_env
from kingfisher.config import ConfigError

CATALOGUE = """
endpoints:
  gateway:
    api: anthropic
    base_url: https://example.invalid/anthropic
    key_env: GATEWAY_API_KEY
  openai:
    api: openai
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
    """No fallback and no shipped table, for the reason `KINGFISHER_API_STYLE`
    was required and had none: a default silently picks a destination nobody
    chose the first time kingfisher is pointed somewhere new."""
    with pytest.raises(ConfigError, match="no model catalogue at"):
        from_env({"KINGFISHER_WORKSPACE": str(tmp_path / "ws")})


def test_the_absent_file_error_shows_a_working_example(tmp_path):
    """It is the first thing a new deployment hits, replacing a message that
    came with an unusually explanatory `.env.example`. A path alone would leave
    someone guessing at a schema."""
    with pytest.raises(ConfigError) as raised:
        from_env({"KINGFISHER_WORKSPACE": str(tmp_path / "ws")})

    message = str(raised.value)
    for expected in ("endpoints:", "api: anthropic", "key_env:", "default:", "models:"):
        assert expected in message


def test_it_defaults_inside_the_workspace(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "models.yaml").write_text(CATALOGUE, encoding="utf-8")

    cfg = from_env({"KINGFISHER_WORKSPACE": str(workspace), "GATEWAY_API_KEY": "sk-gateway"})

    assert cfg.models.source == workspace / "models.yaml"


# -- what it resolves to ---------------------------------------------------


def test_endpoints_and_models_come_from_the_file(env):
    cfg = from_env(env)

    assert set(cfg.models.endpoints) == {"gateway", "openai"}
    assert set(cfg.models.models) == {"MiniMax-M3", "gpt-5"}
    assert cfg.models.default == "MiniMax-M3"


def test_credentials_come_from_the_variable_each_endpoint_names(env):
    """Keys are named, not written: the file is meant to be reviewed and shared,
    which a file holding credentials could not be."""
    cfg = from_env(env)

    assert cfg.models.endpoints["gateway"].api_key == "sk-gateway"
    assert cfg.models.endpoints["openai"].api_key == "sk-openai"
    assert "sk-gateway" not in CATALOGUE


def test_an_endpoint_without_its_key_is_dropped_and_warned_about(env):
    """One reviewed file across a fleet is the point of `key_env`, so a machine
    holding only some of the keys must still start. Warned even when nothing
    names the endpoint: silence would make a typo'd `key_env` look identical to
    a deployment that deliberately does not pay for that endpoint.
    """
    del env["OPENAI_API_KEY"]

    with pytest.warns(UserWarning, match="OPENAI_API_KEY"):
        cfg = from_env(env)

    assert set(cfg.models.endpoints) == {"gateway"}
    assert set(cfg.models.models) == {"MiniMax-M3"}  # its models went with it


def test_a_default_whose_endpoint_has_no_key_is_refused(env):
    """Dropping is for endpoints nothing needs. The default is needed by
    definition, so a deployment that cannot run it has not finished being set
    up -- and knowing that at startup is the whole point of closing the table.
    """
    del env["GATEWAY_API_KEY"]

    with pytest.raises(ConfigError, match="no credentials"), pytest.warns(UserWarning):
        from_env(env)


def test_a_default_naming_nothing_is_a_different_error(env, tmp_path):
    """A broken file and an unfinished deployment read differently and are
    worded differently."""
    (tmp_path / "models.yaml").write_text(
        CATALOGUE.replace("default: MiniMax-M3", "default: typo-5"), encoding="utf-8"
    )

    with pytest.raises(ConfigError, match="is not defined here"):
        from_env(env)


# -- the rest of the environment -------------------------------------------


def test_hosted_tracing_is_disabled_explicitly(monkeypatch):
    """Q13: a stray var from another project must not start exporting."""
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    config_module.enforce_local_only_tracing()
    assert os.environ["LANGSMITH_TRACING"] == "false"
    assert os.environ["LANGCHAIN_TRACING_V2"] == "false"


def test_state_and_scratch_default_to_the_workspace(env):
    """Unset means self-contained: nothing is written outside the workspace."""
    cfg = from_env(env)

    assert cfg.state_root is None
    assert cfg.scratch_root is None
    assert cfg.state_dir == cfg.workspace / ".kingfisher"
    assert cfg.scratch_dir == cfg.workspace / ".kingfisher" / "tmp"


def test_state_and_scratch_can_be_pointed_elsewhere(env, tmp_path):
    """Host-side state is relocatable; the agent addresses none of it by path."""
    cfg = from_env(
        {
            **env,
            "KINGFISHER_STATE_DIR": str(tmp_path / "state"),
            "KINGFISHER_SCRATCH_DIR": str(tmp_path / "scratch"),
        }
    )

    assert cfg.state_dir == tmp_path / "state"
    assert cfg.scratch_dir == tmp_path / "scratch"


def test_the_catalogue_defaults_inside_the_workspace(env):
    """Unset changes nothing: definitions stay where they have always been."""
    cfg = from_env(env)

    assert cfg.skills_root is None
    assert cfg.subagents_root is None
    assert cfg.skills_dir == cfg.workspace / "skills"
    assert cfg.subagents_dir == cfg.workspace / "subagents"


def test_the_catalogue_can_be_shared_between_workspaces(env, tmp_path):
    """The point of the phase: one reviewed set of definitions, deployed once,
    rather than a copy per workspace that nobody can audit centrally."""
    cfg = from_env(
        {
            **env,
            "KINGFISHER_SKILLS_DIR": str(tmp_path / "catalogue" / "skills"),
            "KINGFISHER_SUBAGENTS_DIR": str(tmp_path / "catalogue" / "subagents"),
        }
    )

    assert cfg.skills_dir == tmp_path / "catalogue" / "skills"
    assert cfg.subagents_dir == tmp_path / "catalogue" / "subagents"


def test_every_variable_read_is_documented():
    """`.env.example` is the only place a deployment learns a knob exists.

    Both KINGFISHER_TOOLS_DIR and KINGFISHER_SHELL_PATH_EXTRA shipped without a
    line here, and the second one is why an agent could not find `pdftotext`:
    the shell PATH is an allowlist, so an unnamed directory looks like the tool
    not existing rather than like configuration.
    """
    import re
    from pathlib import Path as _Path

    from kingfisher.application import config as config_module

    root = _Path(__file__).resolve().parent.parent
    # Asked of the module rather than spelled as a path: this test shipped
    # naming `src/kingfisher/app/config.py`, one rename after that directory
    # stopped existing, and went red on main rather than at review.
    source = _Path(config_module.__file__).read_text()
    read = set(re.findall(r"KINGFISHER_[A-Z_]+", source))
    documented = set(re.findall(r"KINGFISHER_[A-Z_]+", (root / ".env.example").read_text()))

    missing = read - documented

    assert not missing, f"read by config.py but absent from .env.example: {sorted(missing)}"


def test_the_variables_that_chose_a_model_are_gone(env):
    """`KINGFISHER_MODEL`, `KINGFISHER_API_STYLE` and `KINGFISHER_MAX_TOKENS`
    are the catalogue's job now, and the per-role pair went before them.

    Asserted as absence, because the failure mode is a variable that reads as
    configuration and is not -- someone sets it, sees no error, and believes it.
    """
    cfg = from_env(
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
    assert cfg.models.resolve()[1].name == "gateway"
    assert cfg.models.models["MiniMax-M3"].max_tokens == 4096
    assert not [f for f in fields(cfg) if f.name in {"model", "api_style", "max_tokens"}]
    assert "ignored" not in repr(cfg)


def test_the_execution_timeout_is_named_for_what_it_bounds(env):
    """It was `KINGFISHER_TIMEOUT_S` and bounded a model call as well as the
    shell and the interpreter -- three unrelated jobs for one number. The model
    half is per-model in the catalogue now."""
    cfg = from_env({**env, "KINGFISHER_EXECUTION_TIMEOUT_S": "45"})

    assert cfg.execution_timeout_s == 45
    assert not [f for f in fields(cfg) if f.name == "timeout_s"]
