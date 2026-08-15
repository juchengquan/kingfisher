"""The shipped examples have to work.

An example that does not parse is worse than no example: it is copied, it
fails, and the format gets blamed. These run against the real loaders.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from kingfisher.adapters.agent import _available_skills, build_agent
from kingfisher.adapters.subagent_store import load_all
from kingfisher.domain.capabilities import Capabilities

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def test_every_example_subagent_parses():
    specs = load_all(EXAMPLES)

    assert set(specs) == {"reviewer", "extractor"}
    for spec in specs.values():
        assert spec.description.strip()
        assert len(spec.system_prompt) > 200  # a real prompt, not a stub


def test_the_extractor_example_demonstrates_the_optional_fields():
    """Both optional fields appear in at least one example, or they are
    documented in the README and shown nowhere."""
    extractor = load_all(EXAMPLES)["extractor"]

    assert extractor.tools is not None
    assert "write_file" not in extractor.tools  # read-only, as its body claims
    assert extractor.model


@pytest.fixture
def workspace_with_examples(cfg):
    for kind in ("skills", "subagents"):
        shutil.copytree(EXAMPLES / kind, cfg.workspace / kind, dirs_exist_ok=True)
    return cfg


def test_example_skills_are_discovered(workspace_with_examples):
    assert set(_available_skills(workspace_with_examples.workspace)) >= {
        "code-review",
        "release-notes",
    }


def test_the_readme_tool_table_matches_the_real_tool_surface(cfg):
    """The table is the reference a caller builds an allowlist from, so a stale
    row is a CapabilityError someone has to debug."""
    from langchain_core.messages import AIMessage

    from kingfisher.adapters.agent import registered_tools
    from tests.conftest import FakeToolCallingModel

    graph = build_agent(cfg, model=FakeToolCallingModel(responses=[AIMessage(content="ok")]))
    # Only the tools table -- the file has other tables, and scooping up their
    # first columns too is how the first draft of this test "passed" nothing.
    readme = (EXAMPLES / "README.md").read_text(encoding="utf-8")
    table = readme.split("## Tools")[1].split("\n---")[0]
    documented = {
        line.split("|")[1].strip().strip("`")
        for line in table.splitlines()
        if line.startswith("| `")
    }

    assert documented == set(registered_tools(graph))


def test_the_readme_example_call_is_valid(workspace_with_examples):
    """Exactly the capabilities the README shows, built for real."""
    from dataclasses import replace

    from langchain_core.messages import AIMessage

    from tests.conftest import FakeToolCallingModel

    build_agent(
        replace(workspace_with_examples, skills_enabled=True),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(
            tools=("read_file", "ls", "glob", "grep", "execute", "task"),
            skills=("code-review",),
            subagents=("reviewer",),
        ),
    )
