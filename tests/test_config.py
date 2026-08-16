from __future__ import annotations

import os

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


def test_role_models_fall_back_to_the_main_model():
    cfg = from_env(
        {
            **BASE_ENV,
            "KINGFISHER_API_STYLE": "anthropic",
            "KINGFISHER_MODEL_SUBAGENT": "MiniMax-M2.5",
        }
    )
    assert cfg.model_for("subagent") == "MiniMax-M2.5"
    assert cfg.model_for("main") == "MiniMax-M3"
    assert cfg.model_for("summarizer") == "MiniMax-M3"


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

    missing = {
        name
        for name in read
        # A trailing underscore is an f-string prefix -- KINGFISHER_MODEL_{role}
        # -- so any documented variable starting with it counts.
        if not (name in documented or any(d.startswith(name) for d in documented))
    }

    assert not missing, f"read by config.py but absent from .env.example: {sorted(missing)}"


def test_every_role_has_a_reader():
    """`from_env` accepts `KINGFISHER_MODEL_<ROLE>` and `KINGFISHER_PROVIDER_<ROLE>`
    for every entry here, so an entry nothing looks up is a variable that reads
    as configuration and is not.

    Three were: `PROVIDER_MAIN` and both `_SUMMARIZER` forms were parsed and
    never read -- nothing builds a summarizer at all. This holds the tuple to
    the one role that has a reader, so adding another means adding its lookup in
    the same change.
    """
    from kingfisher.config import ROLES
    from kingfisher.infrastructure.delegation import SUBAGENT_ROLE

    assert set(ROLES) == {SUBAGENT_ROLE}


def test_the_main_model_has_one_name():
    """`KINGFISHER_MODEL_MAIN` used to shadow `KINGFISHER_MODEL`: `model_for`
    consults `role_models` first, so the undocumented variable beat the
    documented, required one and `build_model` used it.
    """
    environ = {
        "KINGFISHER_WORKSPACE": "/tmp/kf-role-probe",
        "KINGFISHER_API_STYLE": "anthropic",
        "ANTHROPIC_BASE_URL": "http://127.0.0.1:9/a",
        "ANTHROPIC_API_KEY": "k",
        "KINGFISHER_MODEL": "the-one-that-counts",
        "KINGFISHER_MODEL_MAIN": "the-one-that-used-to-win",
    }

    cfg = from_env(environ)

    assert cfg.model == "the-one-that-counts"
    assert cfg.model_for("main") == "the-one-that-counts"


def test_the_subagent_override_still_works():
    """The one role that survives, and the reason the mechanism exists: an
    operator routes a delegate without editing a definition they may not own."""
    environ = {
        "KINGFISHER_WORKSPACE": "/tmp/kf-role-probe",
        "KINGFISHER_API_STYLE": "anthropic",
        "ANTHROPIC_BASE_URL": "http://127.0.0.1:9/a",
        "ANTHROPIC_API_KEY": "k",
        "KINGFISHER_MODEL": "main-model",
        "KINGFISHER_MODEL_SUBAGENT": "cheap-model",
        "KINGFISHER_PROVIDER_SUBAGENT": "anthropic",
    }

    cfg = from_env(environ)

    assert cfg.model_for("subagent") == "cheap-model"
    assert cfg.role_providers["subagent"] == "anthropic"
