"""Fixtures for the pack's own tests.

Self-contained on purpose. These build against kingfisher's public
configuration and workspace API and nothing from its test tree, so they say
what they mean: this pack's files work with an *installed* kingfisher, not with
a checkout of one.
"""

from __future__ import annotations

import shutil
from importlib import resources

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from kingfisher.config import Config, Endpoint, ModelProfile, Models
from kingfisher.infrastructure.workspace_fs import ensure_layout, ensure_session_layout


class FakeToolCallingModel(FakeMessagesListChatModel):
    """A model that can be handed to the agent builder.

    `FakeMessagesListChatModel` has no `bind_tools`, and building binds tools.
    Returning `self` is enough -- nothing here runs a turn.
    """

    def bind_tools(self, tools, **kwargs):
        return self


#: Port 9 is discard: a test that accidentally makes a real call hangs on
#: connect rather than reaching anyone.
#:
#: `alternate` is bound because `second-opinion` names it. That is not a
#: workaround -- it is the arrangement the presets exist to demonstrate: a
#: definition names a *role* and a deployment decides which model fills it. This
#: file is this pack's deployment, so it has to answer the same question a real
#: `models.yaml` does. An unbound alias refuses the build, which would make
#: every test below a test of that refusal instead.
FAKE_CATALOGUE = Models(
    models={"fake-model": ModelProfile(model="fake-model", endpoint="fake")},
    endpoints={
        "fake": Endpoint(
            api="anthropic",
            base_url="http://127.0.0.1:9/never-called",
            api_key="test-key-not-real",
        )
    },
    default="fake-model",
    aliases={"alternate": "fake-model"},
)


@pytest.fixture(scope="session")
def shipped():
    """This pack's files, reached the way kingfisher reaches an installed pack.

    A fixture rather than a module constant because `importlib.resources` does
    not promise the files sit on disk -- a zip-imported package materialises
    them for the duration of the context and cleans up after.
    """
    with resources.as_file(resources.files("kingfisher_assets")) as root:
        yield root


@pytest.fixture
def cfg(tmp_path):
    return Config(
        workspace=ensure_layout(tmp_path / "ws"),
        models=FAKE_CATALOGUE,
        turn_timeout_s=3600,
        execution_timeout_s=30,
    )


@pytest.fixture
def session_dir(cfg):
    return ensure_session_layout(cfg.workspace / "sessions" / "test-session")


@pytest.fixture
def workspace_with_presets(cfg, shipped):
    """A workspace seeded with this pack, the way `--seed-presets` leaves one."""
    for kind in ("skills", "subagents"):
        shutil.copytree(shipped / kind, cfg.workspace / kind, dirs_exist_ok=True)
    return cfg


@pytest.fixture
def fake_model():
    return FakeToolCallingModel(responses=[AIMessage(content="ok")])
