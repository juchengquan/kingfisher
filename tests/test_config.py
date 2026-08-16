from __future__ import annotations

import os
from dataclasses import fields

import pytest

from kingfisher.application import config as config_module
from kingfisher.application.config import from_env
from kingfisher.config import ConfigError

BASE_ENV = {
    "KINGFISHER_WORKSPACE": "/tmp/kf-test-ws",
    "KINGFISHER_MODEL": "MiniMax-M3",
    "ANTHROPIC_BASE_URL": "https://example.invalid/anthropic",
    "ANTHROPIC_API_KEY": "sk-anthropic",
    "OPENAI_BASE_URL": "https://example.invalid/v1",
    "OPENAI_API_KEY": "sk-openai",
}


def test_api_style_is_required_with_no_default():
    """Q25: the two styles are not equivalent, so there is no safe default."""
    with pytest.raises(ConfigError, match="KINGFISHER_API_STYLE"):
        from_env(BASE_ENV)


def test_unknown_api_style_is_rejected():
    with pytest.raises(ConfigError, match="must be one of"):
        from_env({**BASE_ENV, "KINGFISHER_API_STYLE": "azure"})


@pytest.mark.parametrize(
    ("style", "expected_url", "expected_key"),
    [
        ("anthropic", "https://example.invalid/anthropic", "sk-anthropic"),
        ("openai", "https://example.invalid/v1", "sk-openai"),
    ],
)
def test_credentials_are_selected_by_style(style, expected_url, expected_key):
    cfg = from_env({**BASE_ENV, "KINGFISHER_API_STYLE": style})
    assert cfg.api_style == style
    assert cfg.base_url == expected_url
    assert cfg.api_key == expected_key


def test_missing_credentials_for_the_chosen_style_fail_loudly():
    env = {k: v for k, v in BASE_ENV.items() if k != "OPENAI_API_KEY"}
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        from_env({**env, "KINGFISHER_API_STYLE": "openai"})


def test_hosted_tracing_is_disabled_explicitly(monkeypatch):
    """Q13: a stray var from another project must not start exporting."""
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    config_module.enforce_local_only_tracing()
    assert os.environ["LANGSMITH_TRACING"] == "false"
    assert os.environ["LANGCHAIN_TRACING_V2"] == "false"


def test_state_and_scratch_default_to_the_workspace():
    """Unset means self-contained: nothing is written outside the workspace."""
    cfg = from_env({**BASE_ENV, "KINGFISHER_API_STYLE": "anthropic"})

    assert cfg.state_root is None
    assert cfg.scratch_root is None
    assert cfg.state_dir == cfg.workspace / ".kingfisher"
    assert cfg.scratch_dir == cfg.workspace / ".kingfisher" / "tmp"


def test_state_and_scratch_can_be_pointed_elsewhere(tmp_path):
    """Host-side state is relocatable; the agent addresses none of it by path."""
    cfg = from_env(
        {
            **BASE_ENV,
            "KINGFISHER_API_STYLE": "anthropic",
            "KINGFISHER_STATE_DIR": str(tmp_path / "state"),
            "KINGFISHER_SCRATCH_DIR": str(tmp_path / "scratch"),
        }
    )

    assert cfg.state_dir == tmp_path / "state"
    assert cfg.scratch_dir == tmp_path / "scratch"


def test_the_catalogue_defaults_inside_the_workspace():
    """Unset changes nothing: definitions stay where they have always been."""
    cfg = from_env({**BASE_ENV, "KINGFISHER_API_STYLE": "anthropic"})

    assert cfg.skills_root is None
    assert cfg.subagents_root is None
    assert cfg.skills_dir == cfg.workspace / "skills"
    assert cfg.subagents_dir == cfg.workspace / "subagents"


def test_the_catalogue_can_be_shared_between_workspaces(tmp_path):
    """The point of the phase: one reviewed set of definitions, deployed once,
    rather than a copy per workspace that nobody can audit centrally."""
    cfg = from_env(
        {
            **BASE_ENV,
            "KINGFISHER_API_STYLE": "anthropic",
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

    # An exact match, now that nothing builds a variable name at runtime. The
    # prefix allowance this used to make existed for `KINGFISHER_MODEL_{role}`,
    # and it let documenting `KINGFISHER_MODEL_SUBAGENT` satisfy a read of
    # `KINGFISHER_MODEL` -- which is not what the check is for.
    missing = read - documented

    assert not missing, f"read by config.py but absent from .env.example: {sorted(missing)}"


def test_the_per_role_variables_are_gone():
    """`KINGFISHER_MODEL_SUBAGENT` and `KINGFISHER_PROVIDER_SUBAGENT` used to
    override every delegate at once, and nothing reads them now.

    A blanket override is the wrong shape for the decision: `second-opinion`
    exists in order *not* to be the model beside it, so one variable saying
    "all delegates" silently defeats the one delegate that most needs its own.
    A definition says where it runs; see `presets/README.md`.

    Asserted as absence, because the failure mode is a variable that reads as
    configuration and is not -- someone sets it, sees no error, and believes it.
    """
    environ = {
        "KINGFISHER_WORKSPACE": "/tmp/kf-role-probe",
        "KINGFISHER_API_STYLE": "anthropic",
        "ANTHROPIC_BASE_URL": "http://127.0.0.1:9/a",
        "ANTHROPIC_API_KEY": "k",
        "KINGFISHER_MODEL": "the-one-that-counts",
        "KINGFISHER_MODEL_SUBAGENT": "cheap-model",
        "KINGFISHER_PROVIDER_SUBAGENT": "openai",
    }

    cfg = from_env(environ)

    assert cfg.model == "the-one-that-counts"
    assert not [f for f in fields(cfg) if "role" in f.name]
    assert "cheap-model" not in repr(cfg)


def test_the_main_model_has_one_name():
    """`KINGFISHER_MODEL_MAIN` was read too, and beat the documented, required
    `KINGFISHER_MODEL` -- `model_for("main")` consulted the role map first."""
    environ = {
        "KINGFISHER_WORKSPACE": "/tmp/kf-role-probe",
        "KINGFISHER_API_STYLE": "anthropic",
        "ANTHROPIC_BASE_URL": "http://127.0.0.1:9/a",
        "ANTHROPIC_API_KEY": "k",
        "KINGFISHER_MODEL": "the-one-that-counts",
        "KINGFISHER_MODEL_MAIN": "the-one-that-used-to-win",
    }

    assert from_env(environ).model == "the-one-that-counts"
