"""Parsing `/subagents/<name>.md`."""

from __future__ import annotations

from pathlib import Path

import pytest

from kingfisher.domain.subagent import KNOWN, REFUSED, SubagentError, SubagentSpec
from kingfisher.infrastructure.definitions import read_subagent, skill_name
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


# -- fields this format does not define ------------------------------------


def _definition(*frontmatter_lines: str) -> str:
    header = "\n".join(("name: reviewer", "description: d", *frontmatter_lines))
    return f"---\n{header}\n---\nYou review analyses.\n"


def test_a_typo_of_an_optional_field_is_refused_not_ignored(tmp_path):
    """The bug this closes. `tolls:` was dropped in silence, and dropping it is
    indistinguishable from honouring it: a missing `tools` means *inherit*, so
    the delegate came out holding every tool its parent had.
    """
    with pytest.raises(SubagentError, match="tolls") as raised:
        read_subagent(_definition("tolls: [read_file]"), tmp_path / "reviewer.md")

    assert "did you mean 'tools'?" in str(raised.value)


def test_a_typo_of_a_required_field_names_the_typo(tmp_path):
    """Not "missing required field 'name'", which sends the author looking for
    something they can plainly see they wrote."""
    body = "---\nnmae: reviewer\ndescription: d\n---\nYou review.\n"

    with pytest.raises(SubagentError, match="nmae") as raised:
        read_subagent(body, tmp_path / "reviewer.md")

    assert "did you mean 'name'?" in str(raised.value)


def test_an_unrecognisable_field_is_refused_and_lists_what_is_allowed(tmp_path):
    """No near match, so no guess -- just the field set, which is the only
    honest thing to offer. There is nowhere to put your own keys yet, and the
    message does not pretend otherwise.
    """
    with pytest.raises(SubagentError, match="additional_abc") as raised:
        read_subagent(_definition("additional_abc: 1"), tmp_path / "reviewer.md")

    message = str(raised.value)
    assert "did you mean" not in message
    for field in ("name", "description", "tools", "skills", "middleware", "provider", "model"):
        assert field in message


@pytest.mark.parametrize("field", sorted(REFUSED))
def test_a_deliberately_unexposed_field_says_why(tmp_path, field):
    """These are not "not yet". Honouring them would be wrong, and the generic
    message reads as an omission someone might work around."""
    with pytest.raises(SubagentError, match=field) as raised:
        read_subagent(_definition(f"{field}: something"), tmp_path / "reviewer.md")

    message = str(raised.value)
    assert "did you mean" not in message
    assert REFUSED[field].split()[0] in message


def test_permissions_explains_the_direction_it_gets_wrong(tmp_path):
    """The one worth a test of its own: it is written to *tighten* a delegate
    and silently did nothing, so the definition read stricter than the agent it
    produced."""
    with pytest.raises(SubagentError) as raised:
        read_subagent(_definition("permissions: [deny]"), tmp_path / "reviewer.md")

    message = str(raised.value)
    assert "replace" in message
    assert "read-only" in message


def test_every_known_field_still_parses(tmp_path):
    """The negative control: strictness that rejected a valid definition would
    be a worse bug than the one it fixes."""
    body = (
        "---\n"
        "name: reviewer\n"
        "description: d\n"
        "tools: [read_file]\n"
        "skills: [tabular-qa]\n"
        "middleware: [audit]\n"
        "provider: openai\n"
        "model: gpt-5\n"
        "---\n"
        "You review.\n"
    )
    spec = read_subagent(body, tmp_path / "reviewer.md")

    assert spec.tools == ("read_file",)
    assert spec.middleware == ("audit",)
    assert spec.provider == "openai"


def test_the_known_set_matches_the_spec_it_builds():
    """Two lists that must agree: a field added to the dataclass but not to
    KNOWN would be refused as unknown the moment anyone used it."""
    read = set(SubagentSpec.__dataclass_fields__) - {"system_prompt"}

    assert read == KNOWN


def test_a_skill_may_carry_fields_kingfisher_does_not_know(tmp_path):
    """Deliberately the opposite rule. Kingfisher does not own the skill format,
    so refusing keys there would reject what deepagents considers valid."""
    body = "---\nname: code-review\nallowed-tools: [read_file]\nlicense: MIT\n---\nBody.\n"

    assert skill_name(body) == "code-review"
