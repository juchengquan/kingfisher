"""A definitions folder the command cannot start on is refused in one line, not a
traceback."""

from __future__ import annotations

import pytest

from kingfisher.cli import __main__ as cli
from tests.conftest import an_agent, middlewares_dir, tools_dir


def _broken_tool(cfg) -> str:
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    return "broken.py"


def _broken_middleware(cfg) -> str:
    middlewares_dir(cfg).mkdir(parents=True, exist_ok=True)
    (middlewares_dir(cfg) / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    return "broken.py"


def _broken_locks(cfg) -> str:
    (cfg.definitions / "skill_locks.yaml").write_text("locks: [\n", encoding="utf-8")
    return "skill_locks.yaml"


def _broken_wording(cfg) -> str:
    (cfg.definitions / "prompts").mkdir(exist_ok=True)
    (cfg.definitions / "prompts" / "session.md").write_text("No folders.\n", encoding="utf-8")
    return "prompts/session.md"


@pytest.mark.parametrize(
    "breaking",
    [_broken_tool, _broken_middleware, _broken_locks, _broken_wording],
    ids=["tool", "middleware", "skill-locks", "prompts"],
)
def test_a_broken_definition_is_refused_in_one_line(cfg, at_the_command_line, capsys, breaking):
    """Each gave a traceback and exit 1, which the help gives a turn that ran and stopped,
    while a broken agent file was refused in one line and exit 2."""
    an_agent(cfg)
    named = breaking(cfg)

    code = cli.main(["prompt", "--agent", "only"])

    said = capsys.readouterr().err
    assert code == 2
    assert named in said
    assert "Traceback" not in said, "the refusal reached the terminal as a traceback"
