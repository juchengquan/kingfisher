"""A deployment's replacements for kingfisher's prompt pieces, refused where one would
drop what a piece says."""

from __future__ import annotations

import pytest

from kingfisher import Kingfisher
from kingfisher.core.definitions.wording import (
    FIXED,
    PROMPTS_FOLDER,
    WordingError,
    blanks,
    read_wording,
    shipped,
)
from kingfisher.plugins import LocalSessions
from tests.conftest import an_agent


def _replace(folder, name: str, text: str):
    path = folder / PROMPTS_FOLDER / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return folder / PROMPTS_FOLDER


@pytest.mark.parametrize(
    ("name", "text", "said"),
    [
        ("turn/inputs_new", "有新文件。", "leaves out {{files}}"),
        ("turn/inputs_new", "New: {{files}} in {{inputs}}.", "adds {{inputs}}"),
        # deepagents fills this one, and a template without its blank shows no memory.
        ("memory", "{{kept}} {{saving}} {{evidence}} /{{memory}}", "leaves out {agent_memory}"),
    ],
    ids=["dropped", "added", "deepagents-blank"],
)
def test_a_replacement_that_changes_the_blanks_is_refused(tmp_path, name, text, said):
    """Accepted, a translation leaving out `{{files}}` told the agent new files had
    arrived and never which; the control, every blank kept, is taken."""
    with pytest.raises(WordingError, match=f"prompts/{name}.md .*{said}"):
        read_wording(_replace(tmp_path, name, text))

    kept = " ".join(sorted(blanks(shipped()[name])))
    assert read_wording(_replace(tmp_path, name, f"译文 {kept}")).text(name) == f"译文 {kept}"


def test_an_empty_replacement_says_nothing_rather_than_being_refused(tmp_path):
    """Empty is how a deployment drops a sentence; refused for its missing blanks, there
    would be no way to say nothing."""
    assert read_wording(_replace(tmp_path, "tool_results/reading_grep", "\n")).text(
        "tool_results/reading_grep"
    ) == ""


@pytest.mark.parametrize("name", sorted(FIXED))
def test_a_fixed_piece_cannot_be_replaced(tmp_path, name):
    """Replaced, the credentials rule or the interrupted-turn warning could be worded away
    with nothing said; each must also still be a piece kingfisher ships, or renaming it
    would leave the new name replaceable."""
    assert name in shipped()
    with pytest.raises(WordingError, match="always sends as it ships"):
        read_wording(_replace(tmp_path, name, shipped()[name]))


@pytest.mark.parametrize(
    ("file", "said"),
    [("turn/inputs_neww.md", "names no piece"), ("session.txt", "not a prompt piece")],
    ids=["misspelt", "not-markdown"],
)
def test_a_file_that_replaces_nothing_is_refused(tmp_path, file, said):
    """Ignored, a misspelt name leaves kingfisher's English in place while the deployment
    believes it replaced it."""
    path = tmp_path / PROMPTS_FOLDER / file
    path.parent.mkdir(parents=True)
    path.write_text("x", encoding="utf-8")
    with pytest.raises(WordingError, match=said):
        read_wording(tmp_path / PROMPTS_FOLDER)


def test_a_deployment_with_a_broken_replacement_does_not_start(cfg):
    """Started, it would fail on the first turn whose prompt holds the piece, hours later."""
    an_agent(cfg)
    _replace(cfg.definitions, "session", "No folders named here.")
    with pytest.raises(WordingError, match="leaves out"):
        Kingfisher(cfg, sessions=LocalSessions())
