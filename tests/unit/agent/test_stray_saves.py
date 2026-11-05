"""A save outside every folder of the session, refused where the model points it."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import PERMISSION_DENIED
from langchain_core.messages import AIMessage, ToolMessage

from kingfisher import Kingfisher, Request
from kingfisher.core.agent.permitted_backend import PermittedBackend
from kingfisher.core.agent.prompting import EDIT_FILE, WRITE_FILE, stray_save
from kingfisher.core.layout import Layout
from kingfisher.plugins import LocalSessions, backend_at
from kingfisher.plugins.sessions.backend import WorkspaceScopedBackend
from tests.conftest import EVERY_TOOL, an_agent, start, subagents_dir
from tests.unit.scripted import Scripted

#: Renamed, because that is where models strayed: three subagents in four saved to an
#: `/outputs` this layout does not have.
RENAMED = Layout(outputs="results")

HELPER = (
    "name: helper\ndescription: Helps.\n"
    'builtin_tools: ["*"]\ntools: ["*"]\n'
    "system_prompt: |\n  You help.\n"
)


@pytest.fixture
def renamed(scripted):
    """A deployment whose `outputs` is `results`, an agent holding every tool and a
    subagent, and one session made under that layout."""
    cfg = replace(scripted, layout=RENAMED)
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "helper.yaml").write_text(HELPER, encoding="utf-8")
    an_agent(cfg, subagents="[helper]", **EVERY_TOOL)
    start(cfg, "s")
    return cfg


def _turn(cfg: Any, *script: AIMessage, sessions: Any = None) -> None:
    Scripted.script.extend(script)
    service = Kingfisher(cfg, sessions=sessions or LocalSessions())
    request = Request("go", agent="only", session_id="s")
    service.run(request)


def _saving(at: str, tool: str, path: str) -> AIMessage:
    args = (
        {"file_path": path, "content": "a,b\n"}
        if tool == WRITE_FILE
        else {"file_path": path, "old_string": "a,b", "new_string": "c,d"}
    )
    return AIMessage(content="", tool_calls=[{"name": tool, "args": args, "id": at}])


def _answer(at: str) -> ToolMessage:
    """The result of call `at`, from whichever model call heard it."""
    (said,) = {
        one.tool_call_id: one
        for heard in Scripted.heard
        for one in heard
        if isinstance(one, ToolMessage) and one.tool_call_id == at
    }.values()
    return said


@pytest.mark.parametrize("tool", [WRITE_FILE, EDIT_FILE])
@pytest.mark.parametrize(
    ("path", "where"),
    [
        ("/outputs/table.csv", "/outputs/table.csv"),
        ("/table.csv", "/table.csv"),
        ("table.csv", "/table.csv"),
    ],
)
def test_a_save_outside_every_folder_is_refused_and_told_where_it_belongs(
    renamed, tool, path, where
):
    """Saved to an `/outputs` the layout did not have, three deliverables in four were
    kept nowhere and handed back to nobody, and each tool said it had succeeded."""
    session = renamed.workspace / "sessions" / "s"
    stray = session / where.lstrip("/")
    if tool == EDIT_FILE:
        # As a shell that was not fenced could have left it.
        stray.parent.mkdir(parents=True, exist_ok=True)
        stray.write_text("a,b\n")

    _turn(renamed, _saving("w1", tool, path), AIMessage("done"))

    answer = _answer("w1")
    assert answer.text == f"Error: {stray_save(where, RENAMED)}"
    assert answer.status == "error"
    assert "/results/" in answer.text
    if tool == EDIT_FILE:
        assert stray.read_text() == "a,b\n"
    else:
        assert not stray.exists()


@pytest.mark.parametrize("path", ["/results/table.csv", "/scratchpad/table.csv"])
def test_a_save_in_the_sessions_folders_goes_through(renamed, path):
    """The control beside the refusal: one refusing every save would pass the test above."""
    _turn(renamed, _saving("w1", WRITE_FILE, path), AIMessage("done"))

    assert _answer("w1").status != "error"
    assert (renamed.workspace / "sessions" / "s" / path.lstrip("/")).read_text() == "a,b\n"


@pytest.mark.parametrize("subagent", ["helper", "general-purpose"])
def test_a_subagents_save_outside_every_folder_is_refused(renamed, subagent):
    """The subagents were the ones that strayed: a parent names the folder in its handoff,
    and a subagent handed the request word for word guessed it."""
    asked = {"description": "save the table", "subagent_type": subagent}
    consulting = AIMessage(content="", tool_calls=[{"name": "task", "args": asked, "id": "t1"}])

    _turn(
        renamed,
        consulting,
        _saving("w1", WRITE_FILE, "/outputs/table.csv"),
        AIMessage("saved"),
        AIMessage("done"),
    )

    assert _answer("w1").text == f"Error: {stray_save('/outputs/table.csv', RENAMED)}"
    assert not (renamed.workspace / "sessions" / "s" / "outputs").exists()


def test_a_folder_the_deployments_backend_mounts_is_saved_in(renamed, tmp_path):
    """The session text says anywhere else the agent may write is said where it is given,
    and a deployment gives one by mounting it: refused, its own prompt would be wrong."""
    shared = tmp_path / "shared"
    shared.mkdir()

    class Mounting(LocalSessions):
        def open(self, cfg, session_id, /, *, catalogue=None):
            built = super().open(cfg, session_id, catalogue=catalogue)
            return WorkspaceScopedBackend(
                default=built.default,
                routes={**built.routes, "/shared/": FilesystemBackend(shared, virtual_mode=True)},
                session_dir=cfg.workspace / "sessions" / session_id,
                workspace=cfg.workspace,
                layout=cfg.layout,
            )

    _turn(
        renamed,
        _saving("w1", WRITE_FILE, "/shared/table.csv"),
        _saving("w2", WRITE_FILE, "/elsewhere/table.csv"),
        AIMessage("done"),
        sessions=Mounting(),
    )

    assert (shared / "table.csv").read_text() == "a,b\n"
    # The control: the mount is what admits it, not a refusal that has stopped working.
    assert _answer("w2").text == f"Error: {stray_save('/elsewhere/table.csv', RENAMED)}"


def test_a_tool_handed_the_backend_is_refused_the_same_saves(renamed):
    """A tool saves wherever the model pointed it, so `write_file` refusing alone would
    leave every offered tool that writes as the way to lose a file."""
    session = renamed.workspace / "sessions" / "s"
    handed = PermittedBackend(backend_at(renamed, session), [], RENAMED)

    assert handed.write("/outputs/table.csv", "a,b\n").error == stray_save(
        "/outputs/table.csv", RENAMED
    )
    assert handed.edit("/table.csv", "a", "b").error == stray_save("/table.csv", RENAMED)
    uploaded = handed.upload_files([("/table.csv", b"a"), ("/results/table.csv", b"b")])
    assert [one.error for one in uploaded] == [PERMISSION_DENIED, None]
    assert handed.write("/scratchpad/table.csv", "a,b\n").error is None
    assert sorted(path.name for path in session.iterdir()) == sorted(
        [*RENAMED.session_dirs, RENAMED.harness]
    )
