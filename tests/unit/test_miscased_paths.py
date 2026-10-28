"""A path spelling one of the session's folders in other capitals, refused wherever the
model points it.

Each test asserts the refusal itself, not what the disk did, so it holds on a
case-sensitive volume too, where the same path would have named a folder that is not
there.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest
from deepagents.backends.protocol import INVALID_PATH
from langchain_core.messages import AIMessage, ToolMessage

from kingfisher import Kingfisher, Request
from kingfisher.core.agent.assembly import read_only_permissions
from kingfisher.core.agent.backend import miscased
from kingfisher.core.agent.permitted_backend import PermittedBackend
from kingfisher.core.agent.prompting import miscased_path, stray_save
from kingfisher.core.agent.references import UnsafeReferenceError
from kingfisher.core.layout import DEFAULT_LAYOUT, Layout
from kingfisher.plugins import LocalSessions
from tests.conftest import EVERY_TOOL, an_agent, start
from tests.unit.scripted import Scripted

HELD = "HARNESS-HELD"
KEPT = "as written\n"


@pytest.fixture
def session(scripted):
    """A session holding something in `.harness` and a saved result, both of which the
    file tools are refused. The result's name is led by no id, so the turn's sweep of
    results no row names leaves it."""
    an_agent(scripted, **EVERY_TOOL)
    start(scripted, "s")
    made = scripted.workspace / "sessions" / "s"
    (made / ".harness" / "secret.txt").write_text(HELD)
    (made / "scratchpad" / "tool_results" / "kept.txt").write_text(KEPT)
    return made


def _turn(cfg: Any, *calls: tuple[str, dict[str, Any]]) -> list[ToolMessage]:
    Scripted.script.extend(
        [
            *(
                AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"c{n}"}])
                for n, (name, args) in enumerate(calls)
            ),
            AIMessage("done"),
        ]
    )
    service = Kingfisher(cfg, sessions=LocalSessions())
    request = Request("go", agent="only", session_id="s")
    service.run(request)
    answers = {
        one.tool_call_id: one
        for heard in Scripted.heard
        for one in heard
        if isinstance(one, ToolMessage)
    }
    return [answers[f"c{n}"] for n in range(len(calls))]


#: Every file tool, each on a folder it is refused or a route it would have got past.
CALLS = [
    ("read_file", {"file_path": "/.HARNESS/secret.txt"}, "/.HARNESS/secret.txt",
     "/.harness/secret.txt"),
    ("ls", {"path": "/.Harness"}, "/.Harness", "/.harness"),
    ("grep", {"pattern": HELD, "path": "/.HARNESS"}, "/.HARNESS", "/.harness"),
    ("glob", {"pattern": "*", "path": "/scratchpad/TOOL_RESULTS"},
     "/scratchpad/TOOL_RESULTS", "/scratchpad/tool_results"),
    ("write_file", {"file_path": "/scratchpad/TOOL_RESULTS/forged.txt", "content": "F"},
     "/scratchpad/TOOL_RESULTS/forged.txt", "/scratchpad/tool_results/forged.txt"),
    ("edit_file",
     {"file_path": "/scratchpad/Tool_Results/kept.txt", "old_string": "as",
      "new_string": "FORGED"},
     "/scratchpad/Tool_Results/kept.txt", "/scratchpad/tool_results/kept.txt"),
    ("delete", {"file_path": "/.HARNESS/secret.txt"}, "/.HARNESS/secret.txt",
     "/.harness/secret.txt"),
]


@pytest.mark.parametrize("call", CALLS, ids=[c[0] for c in CALLS])
def test_a_folder_spelled_in_other_capitals_is_refused_by_every_file_tool(
    scripted, session, call
):
    """On a case-insensitive disk `/.HARNESS/secret.txt` matched no deny rule and no route,
    so the default backend opened the real `.harness`, and `/scratchpad/TOOL_RESULTS/`
    wrote and rewrote saved results the rules refuse."""
    tool, args, where, meant = call
    (answer,) = _turn(scripted, (tool, args))

    assert answer.text == f"Error: {miscased_path(where, meant)}"
    assert answer.status == "error"
    assert (session / ".harness" / "secret.txt").read_text() == HELD
    assert (session / "scratchpad" / "tool_results" / "kept.txt").read_text() == KEPT
    assert sorted(p.name for p in (session / "scratchpad" / "tool_results").iterdir()) == [
        "kept.txt"
    ]


def test_the_folders_spelled_as_they_are_are_untouched(scripted, session):
    """The control: a check refusing every path would pass the test above, and one judging
    a whole path rather than its folders would refuse a file the agent named in capitals,
    or `/scratchpad` itself for being the start of a nested route."""
    answers = _turn(
        scripted,
        ("write_file", {"file_path": "/scratchpad/Notes.md", "content": "n"}),
        ("read_file", {"file_path": "/scratchpad/Notes.md"}),
        ("ls", {"path": "/scratchpad"}),
        ("read_file", {"file_path": "/.harness/secret.txt"}),
    )

    assert [one.status for one in answers[:3]] == ["success"] * 3
    assert "spells a folder" not in answers[3].text
    assert (session / "scratchpad" / "Notes.md").read_text() == "n"


def test_a_renamed_layout_is_spelled_by_its_own_names(scripted):
    """The names come from the layout: `/Results/` is the renamed `/results/`, and
    `/Outputs/` names no folder of this layout, so it is the save outside every folder."""
    cfg = replace(scripted, layout=Layout(outputs="results"))
    an_agent(cfg, **EVERY_TOOL)
    start(cfg, "s")

    told, stray = _turn(
        cfg,
        ("write_file", {"file_path": "/Results/t.csv", "content": "a"}),
        ("write_file", {"file_path": "/Outputs/t.csv", "content": "a"}),
    )

    assert told.text == f"Error: {miscased_path('/Results/t.csv', '/results/t.csv')}"
    assert stray.text == f"Error: {stray_save('/Outputs/t.csv', cfg.layout)}"


def test_a_tool_handed_the_backend_is_refused_the_same_spellings(scripted, session):
    """A tool reads and saves wherever the model pointed it, so the file tools refusing
    alone would leave every offered tool reading `.harness` and forging saved results."""
    backend = LocalSessions().open(scripted, "s")
    handed = PermittedBackend(backend, read_only_permissions(scripted.layout), scripted.layout)
    said = miscased_path("/.HARNESS/secret.txt", "/.harness/secret.txt")

    assert handed.read("/.HARNESS/secret.txt").error == said
    assert handed.delete("/.HARNESS/secret.txt").error == said
    assert asyncio.run(handed.adelete("/.HARNESS/secret.txt")).error == said
    with pytest.raises(UnsafeReferenceError, match="other capitals"):
        handed.on_this_host("/.HARNESS/secret.txt")
    (downloaded,) = handed.download_files(["/.HARNESS/secret.txt"])
    (uploaded,) = handed.upload_files([("/scratchpad/TOOL_RESULTS/A-0009-x.txt", b"FORGED")])
    assert (downloaded.error, downloaded.content, uploaded.error) == (
        INVALID_PATH, None, INVALID_PATH
    )
    assert (session / ".harness" / "secret.txt").read_text() == HELD
    assert not (session / "scratchpad" / "tool_results" / "A-0009-x.txt").exists()


@pytest.mark.parametrize(
    ("where", "meant"),
    [
        ("/.HARNESS", "/.harness"),
        ("/Scratchpad/TOOL_results/a", "/scratchpad/tool_results/a"),
        ("/SCRATCHPAD/Notes.md", "/scratchpad/Notes.md"),
        ("/Shared/a", "/shared/a"),
        ("/scratchpad/tool_results/a", None),
        ("/scratchpad", None),
        ("/outputs/Report.csv", None),
        ("/elsewhere/a", None),
        ("/", None),
    ],
)
def test_only_the_session_s_own_folders_are_respelled(where, meant):
    """Folders a deployment's backend mounts count as the session's, and a file or folder
    the agent names inside one is its own to spell."""
    folders = frozenset({*DEFAULT_LAYOUT.session_dirs, "shared"})

    assert miscased(where, DEFAULT_LAYOUT, folders) == meant
