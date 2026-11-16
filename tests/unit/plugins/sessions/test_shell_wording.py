"""Kingfisher's own shell says itself in the wording `prompts/sessions/` gives it."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from kingfisher import Kingfisher
from kingfisher.cli.health import examine
from kingfisher.core.definitions.wording import WordingError
from kingfisher.core.request import Request
from kingfisher.plugins import LocalSessions
from tests.conftest import EVERY_TOOL, an_agent, start
from tests.unit.scripted import Scripted


def _replace(cfg, name: str, text: str) -> None:
    path = cfg.definitions / "prompts" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_a_replacement_reaches_the_shell_section_the_agent_is_sent(scripted):
    """The plugin builds its shell's wording itself, where the core cannot see which piece
    each part came from; built outside the turn's wording, it stayed in English."""
    an_agent(scripted, **EVERY_TOOL)
    _replace(scripted, "sessions/shell_here", "外壳 /{{inputs}} /{{outputs}} /{{scratchpad}}")
    kf = Kingfisher(scripted, sessions=LocalSessions())
    Scripted.script.append(AIMessage("done"))

    kf.run(Request("go", agent="only", session_id=start(scripted, "s")))

    system = Scripted.heard[-1][0].text
    assert "外壳 /inputs /outputs /scratchpad" in system
    assert "runs on the host" not in system


def test_a_misspelt_shell_piece_stops_the_deployment(cfg):
    """The core cannot name the plugin's pieces, so it takes their names from the plugin;
    a name it never asked for would have been ignored, the English kept."""
    an_agent(cfg)
    _replace(cfg, "sessions/shell_hear", "外壳")
    with pytest.raises(WordingError, match="names no piece"):
        Kingfisher(cfg, sessions=LocalSessions())


def test_doctor_passes_a_deployment_rewording_its_shell(cfg):
    """`doctor` runs startup's own check; asked without the plugin's names, it failed a
    deployment that starts, on a replacement it took for a misspelling."""
    an_agent(cfg)
    _replace(cfg, "sessions/shell_here", "外壳 /{{inputs}} /{{outputs}} /{{scratchpad}}")

    checks = examine(cfg, sessions=LocalSessions())

    assert [one.detail for one in checks if one.name == "startup"] == []
