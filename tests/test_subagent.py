"""Parsing `/subagents/<name>.md`."""

from __future__ import annotations

from pathlib import Path

import pytest

from kingfisher.domain.subagent import SubagentError
from kingfisher.infrastructure.definitions import read_subagent
from kingfisher.infrastructure.subagent_store import load_all

MINIMAL = """---
name: reviewer
description: Checks an analysis for arithmetic errors.
---
You review analyses. Be specific about what is wrong.
"""

FULL = """---
name: reviewer
description: "Checks an analysis for arithmetic errors."
tools: [read_file, glob, grep]
model: MiniMax-M2.5
---
You review analyses.
"""


def test_minimal_definition_parses():
    spec = read_subagent(MINIMAL, Path("reviewer.md"))

    assert spec.name == "reviewer"
    assert spec.description == "Checks an analysis for arithmetic errors."
    assert spec.system_prompt.startswith("You review analyses.")
    # Unset, not empty: the subagent inherits the parent's tools.
    assert spec.tools is None
    assert spec.model is None


def test_optional_fields_and_quoting():
    spec = read_subagent(FULL, Path("reviewer.md"))

    assert spec.tools == ("read_file", "glob", "grep")
    assert spec.model == "MiniMax-M2.5"
    assert spec.description == "Checks an analysis for arithmetic errors."  # unquoted


@pytest.mark.parametrize(
    ("text", "because"),
    [
        ("no frontmatter at all", "expected YAML frontmatter"),
        ("---\ndescription: x\n---\nbody\n", "missing required field 'name'"),
        ("---\nname: x\n---\nbody\n", "missing required field 'description'"),
        ("---\nname: x\ndescription: y\n---\n\n", "must not be empty"),
        # YAML says why; we say which file. Rejected either way.
        ("---\nname x\ndescription: y\n---\nbody\n", "cannot read frontmatter"),
        ("---\n- not\n- a mapping\n---\nbody\n", "expected a mapping of fields"),
    ],
)
def test_malformed_definitions_are_rejected(text, because):
    """Loudly, at build time — a subagent that silently loses its prompt would
    fail much later and much less legibly."""
    with pytest.raises(SubagentError, match=because):
        read_subagent(text, Path("broken.md"))


def test_load_all_is_empty_when_the_directory_is_absent(tmp_path):
    assert load_all(tmp_path / "subagents") == {}


def test_load_all_keys_on_the_frontmatter_name_not_the_filename(tmp_path):
    directory = tmp_path / "subagents"
    directory.mkdir()
    (directory / "misnamed.md").write_text(MINIMAL, encoding="utf-8")

    specs = load_all(tmp_path / "subagents")
    assert set(specs) == {"reviewer"}


def test_load_all_rejects_two_files_claiming_one_name(tmp_path):
    """Otherwise one silently shadows the other depending on sort order."""
    directory = tmp_path / "subagents"
    directory.mkdir()
    (directory / "a.md").write_text(MINIMAL, encoding="utf-8")
    (directory / "b.md").write_text(MINIMAL, encoding="utf-8")

    with pytest.raises(SubagentError, match="duplicate subagent name"):
        load_all(tmp_path / "subagents")


def test_frontmatter_accepts_what_the_skill_spec_documents(tmp_path):
    """Two parsers read one format, and ours was the stricter.

    deepagents reads skill frontmatter with `yaml.safe_load`. A block list is
    the Agent Skills spec's documented form for `allowed-tools`, and a folded
    scalar is how anyone writes a description longer than a line. Rejecting
    them made a skill that loads from the catalogue impossible to upload.
    """
    definition = (
        "---\n"
        "name: extractor\n"
        "description: >-\n"
        "  Pulls fields out of documents,\n"
        "  one record at a time.\n"
        "tools:\n"
        "  - read_file\n"
        "  - grep\n"
        "---\n"
        "You extract.\n"
    )

    spec = read_subagent(definition, tmp_path / "extractor.md")

    assert spec.name == "extractor"
    assert spec.tools == ("read_file", "grep")
    assert "one record at a time" in spec.description
