"""The shipped `agent-builder`, driven as a deployment that names it runs it."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml
from langchain_core.messages import AIMessage, ToolMessage

from kingfisher import Kingfisher
from kingfisher.core.request import Request
from kingfisher.plugins import LocalSessions
from tests.conftest import an_agent, repository_root, start, subagents_dir
from tests.unit.scripted import Scripted
from tests.unit.test_reachable import AUDIT, _deployment

BUNDLE = repository_root() / "assets" / "subagents" / "agent-builder"

#: A prompt PyYAML would write quoted: a dash it escapes, a line ending in a space, a tab.
ACCENTED = "|\n  Answer plainly — and briefly. \n  \tThen stop."


def _with_builder(cfg) -> str:
    """`_deployment`, an agent naming the shipped builder, and a session to run it in.

    `drafter` holds every built-in, so the builder's own line is all that keeps a writer
    from it: an agent's built-ins cap its subagents', and a narrower one would refuse
    `write_file` here whatever the builder asked for.
    """
    _deployment(cfg)
    shutil.copytree(BUNDLE, subagents_dir(cfg) / "agent-builder")
    an_agent(cfg, "drafter", builtin_tools='["*"]', subagents="[agent-builder]")
    an_agent(cfg, "accented", system_prompt=ACCENTED)
    return start(cfg, "drafting")


def _folder(cfg, session: str) -> Path:
    return cfg.workspace / "sessions" / session / cfg.layout.outputs


def _outputs(cfg, session: str) -> dict[str, str]:
    return {one.name: one.read_text(encoding="utf-8") for one in _folder(cfg, session).iterdir()}


def _builder_ran(cfg, session: str, *calls: dict[str, Any]) -> list[str]:
    """What the builder was answered, making `calls` in turn under `drafter`."""
    asked = {"description": "draft an agent", "subagent_type": "agent-builder"}
    Scripted.script.extend(
        [
            AIMessage(content="", tool_calls=[{"name": "task", "args": asked, "id": "t"}]),
            *(
                AIMessage(content="", tool_calls=[{**call, "id": f"c{at}"}])
                for at, call in enumerate(calls)
            ),
            AIMessage(content="drafted"),
            AIMessage(content="done"),
        ]
    )
    Kingfisher(cfg, sessions=LocalSessions()).run(
        Request("go", agent="drafter", session_id=session), source_ids=AUDIT
    )
    # The builder's last call, which heard every answer it was given; `drafter`'s follows.
    return [one.text for one in Scripted.heard[-2] if isinstance(one, ToolMessage)]


def _saved(draft: str) -> dict[str, Any]:
    return {"name": "save_agent_draft", "args": {"draft": draft}}


@pytest.mark.parametrize("copied", ["pattern", "accented"], ids=["every-field", "escapable"])
def test_a_copy_is_saved_only_once_it_passes(scripted, copied):
    """The builder's surest start, an agent the user already has, must save once renamed,
    or a deployment's own prompt with a `—` in it is refused by the builder's own
    check; and saved unrenamed it must be refused and leave nothing in the outputs."""
    session = _with_builder(scripted)
    listed, copy = _builder_ran(
        scripted,
        session,
        {"name": "usable_definitions", "args": {}},
        {"name": "copy_agent", "args": {"name": copied}},
    )
    assert copied in yaml.safe_load(listed.rsplit("\n", 1)[0])["agents"]
    copy = copy.rsplit("\n", 1)[0]
    renamed = copy.replace(f"name: {copied}\n", "name: invoices\n", 1)
    assert yaml.safe_load(renamed)["name"] == "invoices"

    clash, saved = _builder_ran(scripted, session, _saved(copy), _saved(renamed))

    assert f"an agent called {copied!r} already exists" in clash
    assert saved.startswith("Saved invoices.yaml (YAML, ")
    assert _outputs(scripted, session) == {"invoices.yaml": renamed}


def test_the_builder_has_no_other_way_to_write(scripted):
    """A builder that could write a file itself, or hand the writing to a helper, could
    give the user a draft no check ever read, or change one it saved after the check; and
    a check that saved would replace the user's file with each draft tried against it."""
    session = _with_builder(scripted)
    (_folder(scripted, session) / "kept.yaml").write_text("name: kept\n", encoding="utf-8")
    (_folder(scripted, session) / "blocked.yaml").mkdir()
    delegated = {"description": "write it", "subagent_type": "general-purpose"}
    written = {"file_path": "/outputs/drafted.yaml", "content": "name: drafted\n"}
    climbing = "name: ../escaped\ndescription: Climbs.\nsystem_prompt: |\n  Hi.\n"
    tried = "name: kept\ndescription: Tried.\nsystem_prompt: |\n  Hi.\n"
    blocked = "name: blocked\ndescription: In the way.\nsystem_prompt: |\n  Hi.\n"

    *_, checked, escaped, unwritten = _builder_ran(
        scripted,
        session,
        {"name": "write_file", "args": written},
        {
            "name": "edit_file",
            "args": {"file_path": "/outputs/kept.yaml", "old_string": "kept", "new_string": "x"},
        },
        {"name": "delete", "args": {"file_path": "/outputs/kept.yaml"}},
        # Refused, this and the call after it are the builder's own; granted, they are
        # a helper writing.
        {"name": "task", "args": delegated},
        {"name": "write_file", "args": written},
        {"name": "check_agent_draft", "args": {"draft": tried}},
        # A name climbing out of the outputs, which the check refuses.
        _saved(climbing),
        # A draft the check passes whose file the backend cannot write: said to be, rather
        # than reported saved.
        _saved(blocked),
    )

    # Passed, or the file left alone below proves nothing about the check.
    assert checked.rsplit("\n", 1)[0] == (
        "passes: save_agent_draft would save it to /outputs/kept.yaml"
    )
    assert "name '../escaped': " in escaped
    assert "not saved to /outputs/blocked.yaml" in unwritten
    (_folder(scripted, session) / "blocked.yaml").rmdir()
    assert _outputs(scripted, session) == {"kept.yaml": "name: kept\n"}
    assert not list(scripted.workspace.rglob("escaped.yaml"))
