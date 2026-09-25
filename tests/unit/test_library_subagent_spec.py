"""A subagent imported from an installed package, written to deepagents' `SubAgent`.

The package lists its skills the way that TypedDict does -- `skills: list[str]` --
but as directories on the host, since it cannot know the routes of whatever
deployment imports it. Kingfisher mounts each one and tells the delegate where.
"""

from __future__ import annotations

import importlib
import sys
import typing

import pytest
from deepagents import SubAgent
from langchain_core.messages import AIMessage

from kingfisher.domain.capabilities import Capabilities
from kingfisher.infrastructure.catalogue import Definitions
from kingfisher.infrastructure.harness.agent import build_agent
from kingfisher.infrastructure.harness.backend import default_backend
from kingfisher.infrastructure.harness.narrowing import NarrowedSkills
from kingfisher.kinds.subagents.catalogue import LocalSubagentRepository
from kingfisher.kinds.subagents.spec import SubagentError
from kingfisher.layout import BUNDLED_SKILLS_ROUTE
from tests.conftest import FakeToolCallingModel, delegate

#: The installed package, as a library author would write it: a `SubAgent`, typed
#: as one, its skills in two directories found from its own `__file__`.
PACKAGE = '''\
from pathlib import Path

from deepagents import SubAgent

HERE = Path(__file__).parent

subagent_spec: SubAgent = {{
    "name": "clerk",
    "description": "Looks customers up and redacts what it quotes.",
    "system_prompt": "You look things up.",
    "skills": {skills},
}}
'''

SKILL = "---\nname: {name}\ndescription: {desc}\n---\nBody of {name}.\n"


def _skill(directory, name, desc):
    folder = directory / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(SKILL.format(name=name, desc=desc), encoding="utf-8")


@pytest.fixture
def installed(tmp_path, monkeypatch):
    """Write `agent_tools_fixture` somewhere importable, the way site-packages is."""

    def install(skills: str = '[str(HERE / "skills"), str(HERE / "extra" / "skills")]'):
        package = tmp_path / "site" / "agent_tools_fixture"
        package.mkdir(parents=True, exist_ok=True)
        (package / "__init__.py").write_text(PACKAGE.format(skills=skills), encoding="utf-8")
        _skill(package / "skills", "lookup", "Look a customer up.")
        _skill(package / "extra" / "skills", "redact", "Redact before quoting.")
        monkeypatch.syspath_prepend(str(tmp_path / "site"))
        # Each test writes its own copy, so a cached import would hand one test the
        # package another wrote.
        monkeypatch.delitem(sys.modules, "agent_tools_fixture", raising=False)
        return package

    return install


def _deploy(cfg):
    """The deployment's whole opt-in: one module re-exporting the package's spec."""
    for kind in ("skills", "subagents", "tools"):
        cfg.catalogue_roots[kind].mkdir(parents=True, exist_ok=True)
    (cfg.catalogue_roots["subagents"] / "agent_tools.py").write_text(
        "from agent_tools_fixture import subagent_spec\n\nSUBAGENTS = [subagent_spec]\n",
        encoding="utf-8",
    )
    return cfg.catalogue_roots["subagents"]


@pytest.fixture
def deployed(cfg, installed):
    """The package, installed with two skills directories and imported."""
    package = installed()
    _deploy(cfg)
    return package


def test_the_fixture_is_a_subagent_as_deepagents_types_it(deployed):
    """Checked, because a fixture that drifted from the TypedDict would test a shape
    no library writes.
    """
    subagent_spec = importlib.import_module("agent_tools_fixture").subagent_spec

    hints = typing.get_type_hints(SubAgent)
    assert set(subagent_spec) <= set(hints)
    assert SubAgent.__required_keys__ <= set(subagent_spec)
    assert hints["skills"] == list[str]
    assert all(isinstance(one, str) for one in subagent_spec["skills"])


def test_every_listed_directory_is_read(cfg, deployed):
    """Both directories' skills belong to the delegate, under its one label."""
    definitions = Definitions.from_config(cfg).warm()

    assert set(definitions.bundled_skills["clerk"].names) == {"lookup", "redact"}


def test_each_directory_gets_a_route_of_its_own(cfg, deployed, session_dir):
    """Numbered beneath the bundle's route, in the order the spec listed them."""
    where = Definitions.from_config(cfg).subagents.bundles["clerk"].where
    routes = default_backend(cfg, session_dir).routes

    assert f"{BUNDLED_SKILLS_ROUTE}{where}/1/" in routes
    assert f"{BUNDLED_SKILLS_ROUTE}{where}/2/" in routes
    assert f"{BUNDLED_SKILLS_ROUTE}{where}/" not in routes


def test_the_delegate_is_shown_the_skills_from_both(cfg, deployed, session_dir):
    """Listed through the delegate's own index and the backend it shares, so a route
    or a source that went missing shows as a skill that never loads.
    """
    built = build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=("clerk",), tools=()),
    )
    (index,) = [
        m for m in delegate(built, "clerk")["middleware"] if isinstance(m, NarrowedSkills)
    ]

    shown = index._format_skills_list(index.before_agent({}, None, {})["skills_metadata"])

    assert "Look a customer up." in shown
    assert "Redact before quoting." in shown


def test_a_single_path_keeps_the_bundles_own_route(cfg, installed, session_dir):
    """The form kingfisher took before a list: one directory, mounted where it was."""
    installed(skills='str(HERE / "skills")')
    _deploy(cfg)
    where = Definitions.from_config(cfg).subagents.bundles["clerk"].where

    assert f"{BUNDLED_SKILLS_ROUTE}{where}/" in default_backend(cfg, session_dir).routes


def test_a_backend_path_is_refused_saying_what_it_is(cfg, installed):
    """deepagents' own examples write `/skills/...`, a path inside the backend. A
    package cannot know this deployment's routes, so kingfisher refuses it by name
    rather than mounting nothing.
    """
    installed(skills='["/skills/user/"]')
    subagents = _deploy(cfg)

    with pytest.raises(SubagentError, match="not by a path inside the agent's backend"):
        _ = LocalSubagentRepository(subagents).specs
