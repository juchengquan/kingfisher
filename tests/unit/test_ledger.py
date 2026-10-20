"""The ledger: what a turn's tools returned, recorded, kept as files, listed, and shown
to the model.

Through real turns wherever the recorder is involved -- a scripted model, kingfisher's
own build and session backend -- because the recorder's position in the stack is most
of what it is, and a stack assembled by hand would put it wherever the test did.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from dataclasses import asdict, replace
from types import SimpleNamespace
from typing import Any

import pytest
from deepagents.backends.protocol import FileUploadResponse
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command

from assets.middlewares.tool_note import ToolNote
from kingfisher import Kingfisher, Request
from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.agent.middlewares.recorded import summary
from kingfisher.core.agent.middlewares.recorder import RecorderMiddleware
from kingfisher.core.agent.prompting import render_system_prompt
from kingfisher.core.layout import (
    DEFAULT_LAYOUT,
    HARNESS,
    LEDGER_FILE,
    PAUSED_STATE,
    SCRATCH,
    SCRATCH_ROUTE,
    TOOL_RESULTS,
    TRANSCRIPT_FILE,
    Layout,
)
from kingfisher.core.request import Decision, Resume
from kingfisher.core.result import AWAITING, MAIN, WORKING, LedgerEntry
from kingfisher.core.session.ledger import Ledger
from kingfisher.core.session.store import KeptLedger, entry_id
from kingfisher.core.session.transcript import from_json
from kingfisher.core.session.turn import ledger_delta, ledger_index, turn_message
from kingfisher.plugins import LocalSessions, ShellSettings
from kingfisher.plugins.sessions.backend import backend_at
from kingfisher.plugins.sessions.folders import ensure_session_layout
from kingfisher.plugins.sessions.sandbox.confinement import EXTERNAL
from tests.conftest import (
    EVERY_TOOL,
    FakeToolCallingModel,
    an_agent,
    start,
    subagents_dir,
    tools_dir,
)
from tests.unit.scripted import Scripted

TOOL_RESULTS_ROUTE = DEFAULT_LAYOUT.tool_results_route

TOOLS = '''
def web_search(query: str) -> str:
    """Search the web."""
    return f"Ten results for {query}\\n1. one\\n2. two"


def sql_query(query: str) -> str:
    """Run a query."""
    return '{"rows": [[1, "a"], [2, "b"]]}'


def broken(query: str) -> str:
    """Fail, always."""
    raise ValueError(f"no table called {query}")


def loud() -> str:
    """Return about 101,000 characters, past what deepagents shows a model whole."""
    return "\\n".join(f"row {n:05d} " + "x" * 90 for n in range(1_000))


def line_count(path: str) -> str:
    """Count the lines in a text file."""
    with open(path, encoding="utf-8") as handle:
        return str(sum(1 for _ in handle))


def loud_about(subject: str) -> str:
    """As `loud`, naming its subject on every row. For telling two long results apart
    when they are the same length, which is the one thing `loud` twice cannot do.
    """
    return "\\n".join(f"{subject} {n:05d} " + "x" * 90 for n in range(1_000))


TOOLS = [web_search, sql_query, broken, loud, line_count, loud_about]
'''

RESEARCHER = """name: researcher
description: Researches.
tools: [web_search, sql_query]
system_prompt: |
  You research.
"""

#: The same, consulting a helper of its own.
DELEGATING = """name: researcher
description: Researches.
tools: [web_search, sql_query]
builtin_tools: [task]
subagents: [checker]
system_prompt: |
  You research.
"""

CHECKER = """name: checker
description: Checks.
tools: [sql_query]
system_prompt: |
  You check.
"""


@pytest.fixture
def ledgered(scripted):
    """A workspace with the tools above, `researcher`, and an agent that consults it."""
    tools_dir(scripted).mkdir(parents=True, exist_ok=True)
    (tools_dir(scripted) / "probe.py").write_text(TOOLS, encoding="utf-8")
    subagents_dir(scripted).mkdir(parents=True, exist_ok=True)
    (subagents_dir(scripted) / "researcher.yaml").write_text(RESEARCHER, encoding="utf-8")
    an_agent(scripted, subagents="[researcher]", **EVERY_TOOL)
    start(scripted, "s")
    return scripted


def _calls(*made: tuple[str, dict[str, Any], str]) -> AIMessage:
    """One model turn asking for these calls together."""
    return AIMessage(
        content="", tool_calls=[{"name": name, "args": args, "id": at} for name, args, at in made]
    )


def _task(at: str, subagent: str = "researcher") -> AIMessage:
    return _calls(("task", {"description": "dig into it", "subagent_type": subagent}, at))


def _run(cfg, *script: AIMessage, kf: Kingfisher | None = None, **request: Any):
    Scripted.script.extend(script)
    service = kf or Kingfisher(cfg, sessions=LocalSessions())
    return service.run(Request("go", agent="only", session_id="s", **request))


def _session(cfg):
    return cfg.workspace / "sessions" / "s"


def _rows(cfg) -> list[dict[str, Any]]:
    """The ledger as the session keeps it, one decoded line each."""
    text = (_session(cfg) / HARNESS / LEDGER_FILE).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def _kept(cfg) -> list[str]:
    """What the tool results folder holds, by name."""
    folder = _session(cfg) / SCRATCH / TOOL_RESULTS
    return sorted(one.name for one in folder.iterdir()) if folder.exists() else []


def _read(cfg, virtual: str) -> str:
    return (_session(cfg) / virtual.lstrip("/")).read_text(encoding="utf-8")


# -- what is recorded ----------------------------------------------------------


def test_a_result_is_recorded_kept_and_handed_to_the_caller(ledgered):
    """The whole of what a turn records: a result the model was handed has a row, a file
    holding it, and the same row on the caller's result, serialisable as JSON.
    """
    result = _run(
        ledgered,
        _calls(("web_search", {"query": "EV price war"}, "c1")),
        AIMessage(content="done"),
    )

    (entry,) = result.ledger
    assert (entry.id, entry.call, entry.run, entry.role, entry.tool) == (
        "A-0001", "c1", None, "main", "web_search",
    )
    assert entry.turn == result.turn_id
    assert entry.args == "query='EV price war'"
    assert entry.summary == "web_search query='EV price war' -- Ten results for EV price war"
    assert entry.path == "/scratchpad/tool_results/A-0001-web_search.txt"
    assert _read(ledgered, entry.path) == "Ten results for EV price war\n1. one\n2. two"
    assert entry.size == len(_read(ledgered, entry.path).encode("utf-8"))
    assert entry.access is None, "no definition carries a lock"
    # What the caller holds is the row, through the JSON a service would send.
    sent = json.loads(json.dumps(asdict(result)))
    assert sent["ledger"] == _rows(ledgered)[:1]
    assert _rows(ledgered)[1:] == [{"turn": result.turn_id}]


def test_a_renamed_layout_records_under_its_own_scratchpad(ledgered):
    """The recorder, the row, and the sweep all follow a renamed layout.

    A result lands under the configured `tool_results` folder, the row names it there,
    and the default spelling is written nowhere -- the one end-to-end proof that the
    leaf names reach all the way from `Config.layout` to a file on disk and back.
    """
    moved = replace(
        ledgered,
        layout=Layout(
            inputs="in", outputs="out", memory="mem", scratchpad="scratch", tool_results="results"
        ),
    )
    session = ensure_session_layout(moved.workspace / "sessions" / "s2", moved.layout)
    Scripted.script.extend(
        [_calls(("web_search", {"query": "EV price war"}, "c1")), AIMessage(content="done")]
    )
    service = Kingfisher(moved, sessions=LocalSessions())
    result = service.run(Request("go", agent="only", session_id="s2"))

    (entry,) = result.ledger
    assert entry.path == "/scratch/results/A-0001-web_search.txt"
    kept = session / "scratch" / "results" / "A-0001-web_search.txt"
    assert kept.read_text(encoding="utf-8") == "Ten results for EV price war\n1. one\n2. two"
    # The default spellings are written nowhere: the whole path moved, not a copy of it.
    assert not (session / "scratchpad").exists()
    assert (session / "mem" / "AGENTS.md").exists()
    assert not (session / "memory").exists()


def test_a_result_that_is_json_is_kept_as_json(ledgered):
    """The suffix is what tells the agent it can parse the file rather than read it."""
    result = _run(
        ledgered, _calls(("sql_query", {"query": "select 1"}, "c1")), AIMessage(content="done")
    )

    (entry,) = result.ledger
    assert entry.path == "/scratchpad/tool_results/A-0001-sql_query.json"
    assert json.loads(_read(ledgered, entry.path)) == {"rows": [[1, "a"], [2, "b"]]}


#: An image and a PDF of 400 KB each, and an image held by its URL, whose size is unknown.
_IMAGE = {"type": "image", "base64": "A" * 546_134, "mime_type": "image/png"}
_PDF = {"type": "file", "base64": "A" * 546_134, "mime_type": "application/pdf"}
_LINKED = {"type": "image", "url": "https://example.com/chart.png"}


@pytest.mark.parametrize(
    ("blocks", "said"),
    [
        ([_PDF], "a PDF of 400 KB was not kept"),
        ([_IMAGE, _IMAGE], "2 images of 800 KB in all were not kept"),
        ([_IMAGE, _PDF], "2 attachments of 800 KB in all were not kept"),
        ([{"type": "audio", "base64": "A" * 4_096}], "a file of 3 KB was not kept"),
        ([_IMAGE, _LINKED], "2 images were not kept"),
    ],
    ids=["a PDF", "two images", "two kinds", "another kind", "one held by URL"],
)
def test_a_row_names_what_its_saved_copy_left_out_by_kind_count_and_size(blocks, said):
    """A row that names several attachments by one's kind, or totals only those it can
    measure, tells its reader the wrong thing was lost."""
    assert summary("export", "", "", blocks) == f"export (text only; {said})"


def test_the_next_turn_numbers_on_from_the_ledger_it_found(ledgered):
    """Each turn builds a fresh graph and collector, so a number not read back from
    the ledger starts again at A-0001 and the second turn's file replaces the first's.
    """
    first = _run(
        ledgered, _calls(("web_search", {"query": "one"}, "c1")), AIMessage(content="done")
    )
    second = _run(
        ledgered, _calls(("web_search", {"query": "two"}, "c2")), AIMessage(content="done")
    )

    assert [e.id for e in first.ledger] == ["A-0001"]
    assert [e.id for e in second.ledger] == ["A-0002"]
    assert [row.get("id") or row["turn"] for row in _rows(ledgered)] == [
        "A-0001", first.turn_id, "A-0002", second.turn_id,
    ]
    assert _kept(ledgered) == ["A-0001-web_search.txt", "A-0002-web_search.txt"]
    assert "one" in _read(ledgered, first.ledger[0].path)


def test_a_ledger_cut_short_by_a_crash_does_not_stop_the_session(ledgered):
    """A backend has no rename, so a turn that died writing the ledger can leave half a
    line; refused, every later turn in the session would be refused with it.
    """
    first = _run(
        ledgered, _calls(("web_search", {"query": "one"}, "c1")), AIMessage(content="done")
    )
    ledger = _session(ledgered) / HARNESS / LEDGER_FILE
    ledger.write_text(ledger.read_text() + '{"id": "A-0002", "turn": "t-died", "ca')

    second = _run(
        ledgered, _calls(("web_search", {"query": "two"}, "c2")), AIMessage(content="done")
    )

    assert [e.id for e in second.ledger] == ["A-0002"]
    assert [row.get("id") or row["turn"] for row in _rows(ledgered)] == [
        "A-0001", first.turn_id, "A-0002", second.turn_id,
    ]


def test_a_turn_that_records_nothing_still_closes_in_the_ledger(ledgered):
    """Turn ids are not a sequence, so the closing line is the only record of a turn's
    place among the others -- a turn that called nothing would vanish from it.
    """
    result = _run(ledgered, AIMessage(content="nothing to call"))

    assert result.ledger == ()
    assert _rows(ledgered) == [{"turn": result.turn_id}]


def test_the_file_tools_are_not_recorded_and_the_shell_is(ledgered):
    """What a file tool reads or writes is a file already, and a row for each would
    bury the rest -- but `execute` is not one of them, and its output is nowhere else.
    """
    (_session(ledgered) / "outputs" / "notes.txt").write_text("alpha\n", encoding="utf-8")

    result = _run(
        ledgered,
        _calls(
            ("write_file", {"file_path": "/outputs/new.txt", "content": "beta\n"}, "c1"),
            ("read_file", {"file_path": "/outputs/notes.txt"}, "c2"),
            ("ls", {"path": "/outputs"}, "c3"),
            ("glob", {"pattern": "*.txt", "path": "/outputs"}, "c4"),
            ("grep", {"pattern": "alpha", "path": "/outputs"}, "c5"),
        ),
        _calls(
            ("edit_file", {"file_path": "/outputs/new.txt", "old_string": "beta",
                           "new_string": "gamma"}, "c6"),
            ("delete", {"file_path": "/outputs/notes.txt"}, "c7"),
            ("execute", {"command": "echo recorded"}, "c8"),
            ("web_search", {"query": "EV"}, "c9"),
        ),
        AIMessage(content="done"),
        kf=Kingfisher(ledgered, sessions=LocalSessions(shell=ShellSettings(sandbox=EXTERNAL))),
    )

    # The control: every file tool ran and succeeded, so absent means skipped.
    assert (_session(ledgered) / "outputs" / "new.txt").read_text() == "gamma\n"
    assert not (_session(ledgered) / "outputs" / "notes.txt").exists()
    assert sorted(e.tool for e in result.ledger) == ["execute", "web_search"]
    (shell,) = [e for e in result.ledger if e.tool == "execute"]
    assert "recorded" in _read(ledgered, shell.path)


def test_a_failed_call_is_not_recorded(ledgered):
    """A failure has nothing worth finding again and the conversation shows it; with a
    row, the agent would be handed an id for an error message.
    """
    result = _run(
        ledgered,
        _calls(("broken", {"query": "sales"}, "c1"), ("web_search", {"query": "EV"}, "c2")),
        AIMessage(content="done"),
    )

    assert [e.call for e in result.ledger] == ["c2"]
    assert _kept(ledgered) == ["A-0001-web_search.txt"]


def test_a_tool_name_is_one_path_segment_in_the_tool_results_folder(ledgered):
    """A tool's name is somebody else's spelling, and one written with slashes would
    put its result outside the folder the sweep and the agent look in.
    """
    (tools_dir(ledgered) / "odd.py").write_text(
        "from langchain_core.tools import tool\n\n\n"
        "@tool('../../up.and/away')\n"
        "def odd() -> str:\n"
        '    """Return something."""\n'
        "    return 'climbed'\n\n\n"
        "TOOLS = [odd]\n",
        encoding="utf-8",
    )

    result = _run(ledgered, _calls(("../../up.and/away", {}, "c1")), AIMessage(content="done"))

    (entry,) = result.ledger
    assert entry.path == "/scratchpad/tool_results/A-0001-up_and_away.txt"
    assert _kept(ledgered) == ["A-0001-up_and_away.txt"]


def test_a_tool_whose_name_is_all_punctuation_still_gets_a_named_file(ledgered):
    """Stripping what a path segment cannot carry can leave nothing at all, and
    `A-0001.txt` is not found by the `A-0042-*` glob `system.md` tells the model to use
    -- which is the recipe `test_an_older_results_file_is_found_by_its_id_as_the_prompt_says`
    drives.
    """
    (tools_dir(ledgered) / "odder.py").write_text(
        "from langchain_core.tools import tool\n\n\n"
        "@tool('***')\n"
        "def odder() -> str:\n"
        '    """Return something."""\n'
        "    return 'nothing left'\n\n\n"
        "TOOLS = [odder]\n",
        encoding="utf-8",
    )

    result = _run(ledgered, _calls(("***", {}, "c1")), AIMessage(content="done"))

    (entry,) = result.ledger
    assert entry.path == "/scratchpad/tool_results/A-0001-tool.txt"
    assert _kept(ledgered) == ["A-0001-tool.txt"]


# -- where the recorder sits ---------------------------------------------------


def test_the_arguments_recorded_are_the_ones_the_model_wrote(ledgered):
    """`GuardedCallsMiddleware` rewrites a `path` into a host path, and recorded after it
    the row would show the agent a path it was never told and may not use.
    """
    (_session(ledgered) / "inputs" / "notes.txt").write_text("a\nb\n", encoding="utf-8")

    result = _run(
        ledgered,
        _calls(("line_count", {"path": "/inputs/notes.txt"}, "c1")),
        AIMessage(content="done"),
    )

    (entry,) = result.ledger
    assert _read(ledgered, entry.path) == "2", "the tool never reached the file"
    assert entry.args == "path='/inputs/notes.txt'"


def test_what_a_deployments_middleware_left_is_recorded(ledgered):
    """The model reads the result with a deployment's reminder on it, and a row of
    anything else would be a record of a result nobody was shown.
    """
    an_agent(ledgered, subagents="[researcher]", middlewares="[tool-note]", **EVERY_TOOL)
    kf = Kingfisher(ledgered, sessions=LocalSessions(), middlewares={"tool-note": ToolNote})

    result = _run(
        ledgered,
        _calls(("web_search", {"query": "EV"}, "c1")),
        AIMessage(content="done"),
        kf=kf,
    )

    (entry,) = result.ledger
    assert _read(ledgered, entry.path).endswith(
        "\n\nSay where this came from before you rely on it."
    )


def test_a_result_too_long_to_show_is_recorded_whole(ledgered):
    """Eviction swaps a long result for a stub, and a recorder outside it would keep
    the stub: a row saying 'a few KB' for a result of a hundred.
    """
    result = _run(ledgered, _calls(("loud", {}, "c1")), AIMessage(content="done"))

    (entry,) = result.ledger
    assert entry.size > 100_000
    assert entry.summary.startswith("loud -- row 00000 ")


def test_an_offloaded_result_is_pointed_at_deepagents_copy(ledgered):
    """deepagents keeps an evicted result already; without repointing the session
    holds it twice, once under a name no stub mentions.
    """
    result = _run(ledgered, _calls(("loud", {}, "c1")), AIMessage(content="done"))

    (entry,) = result.ledger
    assert entry.path == "/scratchpad/large_tool_results/c1"
    assert _read(ledgered, entry.path).startswith("row 00000 ")
    # deepagents keeps what it was handed, which is the result with its id on the end.
    assert len(_read(ledgered, entry.path)) == entry.size + len("\n[A-0001]")
    assert _kept(ledgered) == [], "kingfisher's own copy was kept beside deepagents'"
    assert _rows(ledgered)[0]["path"] == entry.path


# -- when and where it is kept -------------------------------------------------


class _Ordered(LocalSessions):
    """Kingfisher's own backends, noting every file written through one, in order."""

    def __init__(self) -> None:
        super().__init__()
        self.written: list[str] = []

    def open(self, cfg, session_id, /, *, catalogue=None):
        opened = super().open(cfg, session_id, catalogue=catalogue)
        upload = opened.upload_files

        def noted(files):
            self.written.extend(path for path, _ in files)
            return upload(files)

        opened.upload_files = noted
        return opened

    def at(self, name: str) -> int:
        """Where the first write of a path ending in `name` came."""
        return next(i for i, path in enumerate(self.written) if path.endswith(name))


def test_results_are_written_before_the_ledger_and_the_ledger_before_the_transcript(ledgered):
    """In that order a row never names a missing file, and a result the conversation
    shows always has a row -- the other way round, a crash between the two leaves the
    next turn reading a result whose id the ledger never gave out, and giving it again.
    """
    backends = _Ordered()
    _run(
        ledgered,
        _calls(("web_search", {"query": "EV"}, "c1")),
        AIMessage(content="done"),
        kf=Kingfisher(ledgered, sessions=backends),
    )

    assert backends.at("A-0001-web_search.txt") < backends.at(LEDGER_FILE)
    assert backends.at(LEDGER_FILE) < backends.at(TRANSCRIPT_FILE)


def test_a_paused_turn_keeps_its_rows_before_its_state(ledgered):
    """A turn that stops at a gate has run real calls, and its resume carries on from
    the state it left: unkept, their rows are lost and the resume numbers over them.
    """
    an_agent(ledgered, subagents="[researcher]", interrupt_on="[sql_query]", **EVERY_TOOL)
    backends = _Ordered()
    kf = Kingfisher(ledgered, sessions=backends)

    paused = _run(
        ledgered,
        _calls(("web_search", {"query": "EV"}, "c1")),
        _calls(("sql_query", {"query": "select 1"}, "c2")),
        kf=kf,
    )

    assert paused.stop_reason == AWAITING, "the turn never stopped at the gate"
    assert [e.id for e in paused.ledger] == ["A-0001"]
    assert [row.get("id") for row in _rows(ledgered)] == ["A-0001", None]
    assert backends.at(LEDGER_FILE) < backends.at(PAUSED_STATE)

    Scripted.script.append(AIMessage(content="done"))
    resumed = kf.run(
        Resume(
            session_id="s",
            decisions=(Decision(call_id=paused.pending[0].call_id, action="approve"),),
        )
    )

    assert [(e.id, e.tool) for e in resumed.ledger] == [("A-0002", "sql_query")]


def test_what_the_shell_writes_beside_the_results_is_left_alone(ledgered):
    """The sweep removed what it found in this folder that no row named, and the agent
    writes here too. The file tools are refused the folder; the shell is refused it only
    by `sandbox-exec` and bubblewrap -- filesystem permissions are deepagents' and
    `execute` bypasses them, and Landlock cannot carve the folder out of the scratchpad.

    Driven through `execute` for that reason, with the shell unfenced to stand for
    Landlock and for a deployment's own shell: under the two fences that refuse it the
    write never lands, and this is about what the sweep does with one that did.
    """
    _run(
        ledgered,
        _calls(
            ("web_search", {"query": "EV"}, "c1"),
            ("execute", {"command": "printf mine > scratchpad/tool_results/notes.md"}, "c2"),
        ),
        AIMessage(content="done"),
        kf=Kingfisher(ledgered, sessions=LocalSessions(shell=ShellSettings(sandbox="off"))),
    )

    assert _kept(ledgered) == ["A-0001-web_search.txt", "A-0002-execute.txt", "notes.md"]
    assert _read(ledgered, f"{TOOL_RESULTS_ROUTE}notes.md") == "mine"


def test_the_agent_cannot_rewrite_or_delete_the_file_a_row_names(ledgered):
    """The ledger sits under `.harness` because "a row names its file, and the agent
    must not be able to rewrite what a later turn reads back as true" -- and the file
    the row named sat in the scratchpad, where the agent could write it. `system.md`
    tells the model to read that file back rather than call the tool again, so a later
    turn read a forgery as true: measured, the row stayed at `size: 32` with its
    original summary while the file held `forged`.

    Both verbs, because a deny on writing is not obviously a deny on removing, and a
    row naming a file that is gone is the same lie told the other way round.
    """
    first = _run(
        ledgered, _calls(("web_search", {"query": "EV"}, "c1")), AIMessage(content="done")
    )
    (row,) = first.ledger

    _run(
        ledgered,
        _calls(
            ("write_file", {"file_path": row.path, "content": "forged\n"}, "c2"),
            ("delete", {"file_path": row.path}, "c3"),
        ),
        AIMessage(content="done"),
    )

    assert _read(ledgered, row.path) == "Ten results for EV\n1. one\n2. two"


def test_a_call_id_used_twice_leaves_each_row_on_its_own_result(ledgered):
    """deepagents names an eviction after the call id alone, in a folder that outlasts
    the turn -- so a provider reusing an id pointed the second row at the first
    result's file, and the sweep then took the copy that was right.

    Two turns on one session, the first offloaded and the second not: without the
    check, turn two's row read `/scratchpad/large_tool_results/c1` and `read_file` on
    it returned turn one's 101 KB.
    """
    first = _run(ledgered, _calls(("loud", {}, "c1")), AIMessage(content="done"))
    second = _run(
        ledgered, _calls(("web_search", {"query": "EV"}, "c1")), AIMessage(content="done")
    )

    (was,) = first.ledger
    (now,) = second.ledger
    assert was.path == "/scratchpad/large_tool_results/c1", "the first turn was not offloaded"
    assert now.path != was.path, "two rows name one file, and only one of them is right"
    assert _read(ledgered, now.path).startswith("Ten results for EV")
    # Our own copy, so the result and nothing else: the id goes on what the model is
    # handed, and deepagents' copy is the one that keeps it.
    assert len(_read(ledgered, now.path)) == now.size


def test_two_calls_of_one_id_in_a_turn_each_keep_their_own_result(ledgered):
    """The same collision inside one turn, where neither row is the earlier one: two
    assistant messages reusing an id both wanted the same offloaded path.

    Driven with results that differ in content and not in length, because that is the
    whole of the difficulty -- the listing carries a name and a length, and both rows
    have both. Written with two `loud` calls, whose results are identical, this rule
    passed while the first row pointed at the second call's bytes and the first call's
    result was swept out of the session altogether: measured, a row recording
    `subject='one'` read `two 00000 ...`, and nothing in the session held `one` at all.
    """
    result = _run(
        ledgered,
        _calls(("loud_about", {"subject": "one"}, "c1")),
        _calls(("loud_about", {"subject": "two"}, "c1")),
        AIMessage(content="done"),
    )

    first, again = result.ledger
    assert first.path != again.path, "both rows name one file"
    assert len({first.id, again.id}) == 2
    assert _read(ledgered, first.path).startswith("one 00000 "), "it reads another call's result"
    assert _read(ledgered, again.path).startswith("two 00000 ")
    # Our own copies, so the result and nothing else: on a tie neither row takes
    # deepagents', and its copy carries the id line this recorder put on the end.
    for entry in (first, again):
        assert len(_read(ledgered, entry.path)) == entry.size, entry.id


def test_two_calls_of_one_id_are_still_told_apart_when_their_lengths_differ(ledgered):
    """The bound on the rule above: a tie is a tie in *length*, not in id. Where the
    lengths differ the listing does tell them apart, and the row deepagents' copy
    belongs to still takes it -- keeping ours whenever an id repeats would leave a
    second copy of every long result in the session, which is the thing repointing
    exists to avoid.
    """
    result = _run(
        ledgered,
        _calls(("loud_about", {"subject": "one"}, "c1")),
        _calls(("web_search", {"query": "EV"}, "c1")),
        AIMessage(content="done"),
    )

    long_one, short = result.ledger
    assert long_one.path == f"{SCRATCH_ROUTE}large_tool_results/c1"
    assert _read(ledgered, long_one.path).startswith("one 00000 ")
    assert short.path.startswith(TOOL_RESULTS_ROUTE), "the short result is ours to keep"
    assert _read(ledgered, short.path).startswith("Ten results for EV")


def test_a_todo_rewrite_is_not_a_recorded_result(ledgered):
    """It rewrites one list the conversation already carries, and a row each buries the
    rest: three updates beside one real search made three of the four delta lines a todo
    list, each repeating the list in `args` as well.
    """
    result = _run(
        ledgered,
        _calls(("write_todos", {"todos": [{"content": "step 1", "status": "pending"}]}, "t1")),
        _calls(("write_todos", {"todos": [{"content": "step 1", "status": "completed"}]}, "t2")),
        _calls(("web_search", {"query": "EV"}, "c1")),
        AIMessage(content="done"),
    )

    assert [(e.id, e.tool) for e in result.ledger] == [("A-0001", "web_search")]
    assert _kept(ledgered) == ["A-0001-web_search.txt"]
    # The control: the calls really ran, so this is the recorder skipping them rather
    # than deepagents refusing them -- two in parallel are refused, and a refused call
    # is not recorded either.
    said = [one.content for one in Scripted.heard[-1] if type(one).__name__ == "ToolMessage"]
    assert any("Updated todo list" in str(one) for one in said)


def test_a_row_is_never_pointed_at_another_graphs_result_of_the_same_id(ledgered):
    """A turn holding an agent and a subagent holds two conversations, and a provider
    numbering calls per response gives each a `c1` -- so this needs no provider
    misbehaving and no crash.

    The size is what tells the two apart, and the subagent's small result must keep our
    own copy rather than take the agent's evicted one.
    """
    result = _run(
        ledgered,
        _task("c1"),
        _calls(("web_search", {"query": "EV"}, "c1")),
        AIMessage(content="margins are thin"),
        _calls(("loud", {}, "c1")),
        AIMessage(content="done"),
    )

    by_tool = {e.tool: e for e in result.ledger}
    assert set(by_tool) == {"web_search", "task", "loud"}, [e.tool for e in result.ledger]
    inner = by_tool["web_search"]
    assert inner.path.startswith(TOOL_RESULTS_ROUTE), (
        f"the subagent's row took the agent's offloaded file: {inner.path}"
    )
    assert _read(ledgered, inner.path).startswith("Ten results for EV")


def test_a_result_file_no_row_names_is_swept(ledgered):
    """A turn that died before writing its rows leaves its files behind, and nothing
    else would ever remove them.
    """
    stray = _session(ledgered) / SCRATCH / TOOL_RESULTS / "A-0007-web_search.txt"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_text("from a turn that died", encoding="utf-8")
    _run(ledgered, _calls(("web_search", {"query": "EV"}, "c1")), AIMessage(content="done"))

    assert not stray.exists()
    assert _kept(ledgered) == ["A-0001-web_search.txt"]

    _run(ledgered, AIMessage(content="nothing this time"))

    assert _kept(ledgered) == ["A-0001-web_search.txt"], "an earlier turn's file was swept"


class _Refusing(LocalSessions):
    """Kingfisher's own backends, refusing every file under the tool results folder."""

    def open(self, cfg, session_id, /, *, catalogue=None):
        opened = super().open(cfg, session_id, catalogue=catalogue)
        upload = opened.upload_files

        def refused(files):
            if any(path.startswith(f"/{SCRATCH}/{TOOL_RESULTS}/") for path, _ in files):
                return [FileUploadResponse(path=p, error="permission_denied") for p, _ in files]
            return upload(files)

        opened.upload_files = refused
        return opened


def test_a_result_the_backend_will_not_keep_still_has_its_row(ledgered, caplog):
    """The call has run and the model is reading its answer: a refused file must not
    become a failed call, which the model may make again and run the tool twice.
    """
    with caplog.at_level(logging.WARNING):
        result = _run(
            ledgered,
            _calls(("web_search", {"query": "EV"}, "c1")),
            AIMessage(content="done"),
            kf=Kingfisher(ledgered, sessions=_Refusing()),
        )

    assert result.answer == "done"
    (entry,) = result.ledger
    assert (entry.id, entry.path) == ("A-0001", None)
    assert _rows(ledgered)[0]["path"] is None
    assert "A-0001 was not kept" in caplog.text
    assert "permission_denied" in caplog.text


class _Raising(LocalSessions):
    """Kingfisher's own backends, raising on a save under the tool results folder.

    `_Refusing` above answers with an error, which is one of the two ways a save fails;
    this is the other, and the one the recorder's `except Exception` is for -- a socket
    closing on a remote store does not come back as a response.
    """

    def open(self, cfg, session_id, /, *, catalogue=None):
        opened = super().open(cfg, session_id, catalogue=catalogue)
        upload, aupload = opened.upload_files, opened.aupload_files
        mine = f"/{SCRATCH}/{TOOL_RESULTS}/"

        def raised(files):
            if any(path.startswith(mine) for path, _ in files):
                msg = "the store closed the connection"
                raise RuntimeError(msg)
            return upload(files)

        async def araised(files):
            if any(path.startswith(mine) for path, _ in files):
                msg = "the store closed the connection"
                raise RuntimeError(msg)
            return await aupload(files)

        opened.upload_files = raised
        opened.aupload_files = araised
        return opened


@pytest.mark.parametrize("awaited", [False, True], ids=["run", "arun"])
def test_a_save_that_raises_does_not_fail_the_call_the_model_is_about_to_read(
    ledgered, caplog, awaited
):
    """The note on that `except` reads "a save must never fail the call", and narrowing
    it away left the whole suite green on both halves: `_Refusing` drives the response
    that carries an error, and nothing drove a backend that throws. The call has already
    run, so a save failing the call is a tool the model may run a second time.
    """
    kf = Kingfisher(ledgered, sessions=_Raising())
    request = Request("go", agent="only", session_id="s")
    Scripted.script.extend(
        [_calls(("web_search", {"query": "EV"}, "c1")), AIMessage(content="done")]
    )

    with caplog.at_level(logging.WARNING):
        result = asyncio.run(kf.arun(request)) if awaited else kf.run(request)

    assert result.answer == "done"
    (entry,) = result.ledger
    assert (entry.id, entry.path) == ("A-0001", None)
    assert _rows(ledgered)[0]["path"] is None
    assert "A-0001 was not kept" in caplog.text
    assert "the store closed the connection" in caplog.text


def test_a_graph_the_deployment_supplied_records_nothing(ledgered):
    """Kingfisher did not build it, so nothing of kingfisher's is on its stack to
    record with -- and a ledger of closing lines would claim turns recorded nothing.
    """
    graph = build_agent(
        ledgered,
        backend=backend_at(ledgered, _session(ledgered)),
        model=FakeToolCallingModel(
            responses=[_calls(("web_search", {"query": "EV"}, "c1")), AIMessage(content="done")]
        ),
    ).graph

    result = Kingfisher(ledgered, graph=graph, sessions=LocalSessions()).run(
        Request("go", session_id="s")
    )

    assert result.answer == "done", "the supplied graph never ran its call"
    assert result.ledger == ()
    assert not (_session(ledgered) / HARNESS / LEDGER_FILE).exists()
    assert _kept(ledgered) == []


# -- subagents -------------------------------------------------------------------


def _delegated(cfg, *, awaited: bool = False):
    """One turn: `task` into `researcher`, which makes two calls together."""
    Scripted.script.extend(
        [
            _task("t1"),
            _calls(
                ("web_search", {"query": "BYD margins"}, "c1"),
                ("sql_query", {"query": "select margin"}, "c2"),
            ),
            AIMessage(content="margins are thin"),
            AIMessage(content="done"),
        ]
    )
    kf = Kingfisher(cfg, sessions=LocalSessions())
    request = Request("go", agent="only", session_id="s")
    return asyncio.run(kf.arun(request)) if awaited else kf.run(request)


@pytest.mark.parametrize("awaited", [False, True], ids=["run", "arun"])
def test_a_subagents_results_are_working_entries_of_the_run_that_started_it(ledgered, awaited):
    """The run is told to the subagent's recorders through a context variable, which
    has to reach the threads (sync) and tasks (async) calls made together run on --
    lost, a subagent's results read as the agent's own, under no run at all.
    """
    result = _delegated(ledgered, awaited=awaited)

    assert sorted((e.tool, e.role, e.run) for e in result.ledger) == [
        ("sql_query", "working", "t1"),
        ("task", "main", None),
        ("web_search", "working", "t1"),
    ]
    (answer,) = [e for e in result.ledger if e.tool == "task"]
    assert answer.id == "A-0003", "the answer is numbered after the work it rests on"
    assert _read(ledgered, answer.path) == "margins are thin"
    assert answer.args == "description='dig into it' subagent_type='researcher'"


def test_a_nested_helpers_results_belong_to_the_run_that_started_it(ledgered):
    """A helper runs inside its subagent's run; given a run of its own, the parent sees
    entries under a call it never made.
    """
    (subagents_dir(ledgered) / "researcher.yaml").write_text(DELEGATING, encoding="utf-8")
    (subagents_dir(ledgered) / "checker.yaml").write_text(CHECKER, encoding="utf-8")
    an_agent(ledgered, subagents="[researcher, checker]", **EVERY_TOOL)

    result = _run(
        ledgered,
        _task("t1"),
        _task("t2", subagent="checker"),
        _calls(("sql_query", {"query": "select 1"}, "c1")),
        AIMessage(content="checked"),
        AIMessage(content="researched"),
        AIMessage(content="done"),
    )

    assert [(e.call, e.role, e.run) for e in result.ledger] == [
        ("c1", "working", "t1"),
        ("t2", "working", "t1"),
        ("t1", "main", None),
    ]


def test_general_purpose_records_as_a_subagent(ledgered):
    """deepagents' own subagent is built from a spec of kingfisher's with the agent's
    tools, and is the one graph whose stack is assembled in a different place.
    """
    result = _run(
        ledgered,
        _task("t1", subagent="general-purpose"),
        _calls(("web_search", {"query": "EV"}, "c1")),
        AIMessage(content="searched"),
        AIMessage(content="done"),
    )

    assert [(e.call, e.role, e.run) for e in result.ledger] == [
        ("c1", "working", "t1"),
        ("t1", "main", None),
    ]


#: More than any one recorder's work, so they overlap; few enough that every call
#: has a thread of its own on a two-core runner.
TOGETHER = 4

BARRIER = f'''
import threading

#: Every call waits here for the rest, so their results arrive together.
ARRIVED = threading.Barrier({TOGETHER}, timeout=10)


def together(n: int) -> str:
    """Wait for the calls made beside this one."""
    ARRIVED.wait()
    return f"call {{n}}"


TOOLS = [together]
'''


def test_calls_made_together_get_numbers_of_their_own(ledgered):
    """Calls made together are recorded on threads into one collector. Numbered
    without its lock, two get one id, and the second file replaces the first.

    Driven rather than inspected: the tool holds every call until all have arrived,
    and the interpreter switches threads as often as it can, so the recorders overlap
    on every run rather than when the scheduler happens to let them.
    """
    (tools_dir(ledgered) / "together.py").write_text(BARRIER, encoding="utf-8")
    was = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        result = _run(
            ledgered,
            _calls(*(("together", {"n": n}, f"c{n}") for n in range(TOGETHER))),
            AIMessage(content="done"),
        )
    finally:
        sys.setswitchinterval(was)

    assert len(result.ledger) == TOGETHER, "a call failed, so nothing overlapped"
    assert sorted(e.id for e in result.ledger) == [f"A-000{n}" for n in range(1, TOGETHER + 1)]
    assert len(_kept(ledgered)) == TOGETHER


# -- access ---------------------------------------------------------------------

AUDIENCED_AGENT = """name: only
description: An agent.
source_ids: [public, finance]
builtin_tools: ["*"]
subagents: [researcher]
tools: [web_search, sql_query]
system_prompt: |
  You do the task.
"""

AUDIENCED_RESEARCHER = """name: researcher
description: Researches.
source_ids: [finance]
tools: [web_search, sql_query]
system_prompt: |
  You research.
"""

#: `web_search` locked in its own file, the one place a tool says who may use it.
LOCKED_TOOLS = TOOLS.replace(
    "TOOLS = [web_search,",
    'from kingfisher import ToolSpec\n\nTOOLS = [ToolSpec(web_search, source_ids=["public"]),',
)


#: Who the caller in these tests is: every name the locks use, so nothing is narrowed
#: away and every audience is recorded as written.
HOLDING = ["public", "finance"]


def _audienced(cfg, *, gated: str | None = None):
    """`cfg` with the agent and `researcher` written with audiences."""
    agent = AUDIENCED_AGENT
    if gated is not None:
        agent = agent.replace("system_prompt:", f"interrupt_on: [{gated}]\nsystem_prompt:")
    (tools_dir(cfg) / "probe.py").write_text(LOCKED_TOOLS, encoding="utf-8")
    (cfg.catalogue_roots["agents"] / "only.yaml").write_text(agent, encoding="utf-8")
    (subagents_dir(cfg) / "researcher.yaml").write_text(AUDIENCED_RESEARCHER, encoding="utf-8")
    return cfg


def test_access_is_the_audience_each_result_was_offered_under(ledgered):
    """Recorded so a later design can act on it without old rows lacking it; a
    subagent's answer can repeat anything its run read, so only a caller reaching all
    of that reaches it.
    """
    cfg = _audienced(ledgered)
    Scripted.script.extend(
        [
            _calls(("web_search", {"query": "EV"}, "c1"), ("sql_query", {"query": "q"}, "c2")),
            _task("t1"),
            _calls(("web_search", {"query": "BYD"}, "c3"), ("sql_query", {"query": "q"}, "c4")),
            AIMessage(content="thin margins"),
            AIMessage(content="done"),
        ]
    )

    result = Kingfisher(cfg, sessions=LocalSessions()).run(
        Request("go", agent="only", session_id="s"), source_ids=HOLDING
    )

    assert {e.call: e.access for e in result.ledger} == {
        # The definition's own audience, joined with the tool's own lock where it has one.
        "c1": ("public",),
        "c2": ("public", "finance"),
        # The subagent's own definition, not the agent's.
        "c3": (("finance", "public"),),
        "c4": ("finance",),
        # Whoever reaches `task` and everything its run read: both, held together.
        "t1": (("finance", "public"),),
    }
    assert {row["call"]: row["access"] for row in _rows(cfg) if "call" in row}["t1"] == [
        ["finance", "public"]
    ]


def test_a_subagent_paused_at_a_gate_answers_under_everything_its_run_read(ledgered):
    """A subagent stopped at a gate answers in the next turn, and what its run read
    before the pause is in the ledger alone -- counted only from this turn, the answer
    would claim a wider audience than the work it repeats.
    """
    cfg = _audienced(ledgered, gated="sql_query")
    kf = Kingfisher(cfg, sessions=LocalSessions())
    Scripted.script.extend(
        [
            _task("t1"),
            _calls(("web_search", {"query": "BYD"}, "c1")),
            _calls(("sql_query", {"query": "q"}, "c2")),
        ]
    )
    paused = kf.run(Request("go", agent="only", session_id="s"), source_ids=HOLDING)

    assert paused.stop_reason == AWAITING, "the subagent never stopped at the gate"
    assert [(e.call, e.run, e.access) for e in paused.ledger] == [
        ("c1", "t1", (("finance", "public"),))
    ]

    Scripted.script.extend([AIMessage(content="thin margins"), AIMessage(content="done")])
    resumed = kf.run(
        Resume(
            session_id="s",
            decisions=(Decision(call_id=paused.pending[0].call_id, action="approve"),),
        ),
        source_ids=HOLDING,
    )

    assert {e.call: e.access for e in resumed.ledger} == {
        "c2": ("finance",),
        "t1": (("finance", "public"),),
    }


# -- what the model is handed ----------------------------------------------------

BLOCKS = '''
#: One pixel, so a result can carry an image beside its text.
PIXEL = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAE"
    "hQGAhKmMIQAAAABJRU5ErkJggg=="
)


def two_blocks() -> list:
    """Return two text blocks."""
    return [{"type": "text", "text": "first"}, {"type": "text", "text": "second"}]


def loud_blocks() -> list:
    """Return about 101,000 characters of text, and an image beside them."""
    return [
        {"type": "text", "text": "\\n".join(f"row {n:05d} " + "x" * 90 for n in range(1_000))},
        {"type": "image", "base64": PIXEL, "mime_type": "image/png"},
    ]


TOOLS = [two_blocks, loud_blocks]
'''


def _handed(call_id: str, call: int = -1) -> ToolMessage:
    """`call_id`'s result as the model was handed it on one of its calls, the last by default."""
    return next(
        one
        for one in Scripted.heard[call]
        if isinstance(one, ToolMessage) and one.tool_call_id == call_id
    )


def _asked(call: int = -1) -> str:
    """The user message the model was answering on one of its calls, the last by default."""
    return next(one for one in reversed(Scripted.heard[call]) if one.type == "human").text


def _transcribed(cfg, call_id: str) -> str:
    """A result as the transcript keeps it, for every later turn to replay."""
    text = (_session(cfg) / HARNESS / TRANSCRIPT_FILE).read_text(encoding="utf-8")
    return next(one.content for one in from_json(text) if one.call_id == call_id)


def test_the_model_is_handed_each_result_ending_in_its_id(ledgered):
    """Without the id on what it reads, the model has nothing to cite a result by, and
    nothing ties it to the line the next message lists.
    """
    _run(ledgered, _calls(("web_search", {"query": "EV"}, "c1")), AIMessage(content="done"))

    assert _handed("c1").content == "Ten results for EV\n1. one\n2. two\n[A-0001]"
    assert _transcribed(ledgered, "c1") == _handed("c1").content


def test_the_kept_file_and_the_size_are_the_result_without_its_id(ledgered):
    """The file is what the tool returned, read back by the agent and parsed by a caller:
    with the id in it a JSON result no longer parses, and the size counts a line the
    tool never wrote.
    """
    result = _run(
        ledgered, _calls(("sql_query", {"query": "q"}, "c1")), AIMessage(content="done")
    )

    (entry,) = result.ledger
    assert _handed("c1").content.endswith("\n[A-0001]"), "the model was never handed the id"
    assert json.loads(_read(ledgered, entry.path)) == {"rows": [[1, "a"], [2, "b"]]}
    assert entry.size == len('{"rows": [[1, "a"], [2, "b"]]}')


def test_a_result_made_of_blocks_gains_a_block_holding_its_id(ledgered):
    """A list of blocks has no last line to append to. And a block holding the bare id
    reads as the end of the result's last line wherever blocks are joined with nothing
    between them, which is how the transcript every later turn replays joins them.
    """
    (tools_dir(ledgered) / "blocks.py").write_text(BLOCKS, encoding="utf-8")

    _run(ledgered, _calls(("two_blocks", {}, "c1")), AIMessage(content="done"))

    assert _handed("c1").content == [
        {"type": "text", "text": "first"},
        {"type": "text", "text": "second"},
        {"type": "text", "text": "\n[A-0001]"},
    ]
    assert _transcribed(ledgered, "c1") == "firstsecond\n[A-0001]"


@pytest.mark.parametrize("awaited", [False, True], ids=["run", "arun"])
def test_a_subagents_answer_and_each_result_it_read_end_in_their_ids(ledgered, awaited):
    """`task` hands back a `Command` carrying its answer rather than a message, and a
    stamp written for a message alone leaves every subagent's answer without its id.
    """
    _delegated(ledgered, awaited=awaited)

    assert _handed("t1").content == "margins are thin\n[A-0003]"
    # The researcher's second call, which read what its own two calls returned.
    read = {_handed(call, call=2).content.rsplit("\n", 1)[-1] for call in ("c1", "c2")}
    assert read == {"[A-0001]", "[A-0002]"}


def test_a_command_is_stamped_in_a_copy_that_keeps_the_rest_of_it(ledgered):
    """A tool may hand back a message it still holds, and stamped in place the id goes
    wherever else that message goes; a `Command` rebuilt from its messages alone drops
    whatever a subagent passed up beside its answer.

    Driven on the recorder alone, because no real turn keeps what its tool returned
    for a test to look at afterwards.
    """
    # Two, because the stamp has to follow the id and not the position: a `Command`
    # carrying an earlier message of its own had the id put on that one instead, and
    # the row then measured a result the model never saw under this call.
    earlier = ToolMessage(content="passed up beside it", tool_call_id="t0", name="task")
    answer = ToolMessage(content="thin margins", tool_call_id="t1", name="task")
    returned = Command(update={"messages": [earlier, answer], "todos": ["kept"]}, goto="model")
    recorder = RecorderMiddleware(
        Ledger("turn", KeptLedger()), backend_at(ledgered, _session(ledgered)),
        None,
        DEFAULT_LAYOUT.tool_results_route,
    )

    handed = recorder.wrap_tool_call(
        SimpleNamespace(tool_call={"name": "task", "id": "t1", "args": {}}),
        lambda _: returned,
    )

    assert [one.content for one in handed.update["messages"]] == [
        "passed up beside it",
        "thin margins\n[A-0001]",
    ]
    assert (handed.update["todos"], handed.goto) == (["kept"], "model")
    assert answer.content == "thin margins"
    assert returned.update["messages"] == [earlier, answer]


def test_the_id_outlasts_eviction_as_the_last_line_of_the_stub(ledgered):
    """deepagents swaps a long result for a stub of its first and last lines, so an id
    anywhere but last is cut away with the middle, and the model never sees it.
    """
    _run(ledgered, _calls(("loud", {}, "c1")), AIMessage(content="done"))

    stub = _handed("c1").text
    assert stub.startswith("Tool result too large"), "the control: it was never evicted"
    assert stub.rstrip().endswith("[A-0001]")


def test_the_id_outlasts_eviction_of_a_result_made_of_blocks(ledgered):
    """deepagents joins a block list's text into one stub and keeps an image beside it,
    so the id's own block survives only as the last line of the joined text.
    """
    (tools_dir(ledgered) / "blocks.py").write_text(BLOCKS, encoding="utf-8")

    _run(ledgered, _calls(("loud_blocks", {}, "c1")), AIMessage(content="done"))

    stub, image = _handed("c1").content
    assert isinstance(stub, dict), "the text was no longer a block of its own"
    assert isinstance(image, dict), "the image was lost"
    assert stub["text"].startswith("Tool result too large"), "the control: never evicted"
    assert stub["text"].rstrip().endswith("[A-0001]")
    assert image["type"] == "image", "the image was lost"


def test_a_result_the_backend_would_not_keep_is_stamped_all_the_same(ledgered):
    """Its row exists, with no file, and the next message lists it as not kept: without
    the id on the result, the model cannot tell which line is the one it read.
    """
    result = _run(
        ledgered,
        _calls(("web_search", {"query": "EV"}, "c1")),
        AIMessage(content="done"),
        kf=Kingfisher(ledgered, sessions=_Refusing()),
    )

    assert result.ledger[0].path is None, "the control: the file was kept after all"
    assert _handed("c1").content.endswith("\n[A-0001]")


def test_a_file_tools_result_and_a_failed_call_carry_no_id(ledgered):
    """Neither has a row, and an id on either sends the model looking for a line the
    next message never lists.
    """
    (_session(ledgered) / "outputs" / "notes.txt").write_text("alpha\n", encoding="utf-8")

    _run(
        ledgered,
        _calls(
            ("read_file", {"file_path": "/outputs/notes.txt"}, "c1"),
            ("broken", {"query": "sales"}, "c2"),
            ("web_search", {"query": "EV"}, "c3"),
        ),
        AIMessage(content="done"),
    )

    assert "alpha" in _handed("c1").text, "the control: the file was never read"
    assert _handed("c2").status == "error", "the control: the call never failed"
    assert "[A-" not in _handed("c1").text + _handed("c2").text
    assert _handed("c3").text.endswith("\n[A-0001]")


# -- the delta in the next message ----------------------------------------------


def _entry(  # noqa: PLR0913 -- one keyword for each field the delta reads
    number: int,
    tool: str,
    *,
    args: str = "",
    size: int = 100,
    run: str | None = None,
    call: str | None = None,
    kept: bool = True,
) -> LedgerEntry:
    """An entry as a recorder makes one, varying only in what the delta reads."""
    at = f"A-{number:04d}"
    return LedgerEntry(
        id=at,
        turn="t",
        call=call or f"c{number}",
        run=run,
        role=MAIN if run is None else WORKING,
        tool=tool,
        args=args,
        access=None,
        size=size,
        path=f"/scratchpad/tool_results/{at}-{tool}.txt" if kept else None,
        summary=tool,
    )


def _listed_in(message: str) -> list[str] | None:
    """Each line of a message's delta cut to its indent and id, or `None` for no delta."""
    if "<ledger_delta>" not in message:
        return None
    block = message.split("<ledger_delta>\n", 1)[1].split("\n</ledger_delta>", 1)[0]
    return re.findall(r"^( *A-\d+) ", block, re.MULTILINE)


def test_a_subagents_entries_sit_under_its_answer_and_unanswered_ones_follow_the_rest():
    """The parent is shown where its subagent's results are and not what they hold. An
    entry whose task never answered has nothing to sit under: indented, it would read
    as belonging to whichever entry it followed.
    """
    shown = ledger_delta(
        [
            _entry(1, "search", args="query='EV'", size=14_211),
            _entry(2, "fetch", args="url='https://x'", size=31_000, run="t1"),
            _entry(3, "search", args="query='BYD'", run="t0"),
            _entry(4, "sql", args="query='select 1'", size=6_100, run="t1"),
            _entry(5, "task", args="subagent_type='researcher'", size=2_048, call="t1"),
            _entry(6, "loud", size=100_999, kept=False),
        ]
    )

    assert shown.splitlines() == [
        "<ledger_delta>",
        "A-0001 search query='EV' -> 14 KB  /scratchpad/tool_results/A-0001-search.txt",
        "A-0005 task subagent_type='researcher' -> 2 KB  /scratchpad/tool_results/A-0005-task.txt",
        "  A-0002 fetch url='https://x' -> 30 KB  /scratchpad/tool_results/A-0002-fetch.txt",
        "  A-0004 sql query='select 1' -> 6 KB  /scratchpad/tool_results/A-0004-sql.txt",
        "A-0006 loud -> 99 KB  not kept",
        "A-0003 search query='BYD' -> 100 B  /scratchpad/tool_results/A-0003-search.txt"
        "  (its task never answered)",
        "</ledger_delta>",
    ]


@pytest.mark.parametrize(
    ("size", "shown"),
    [
        (0, "0 B"),
        (1_023, "1023 B"),
        (1_024, "1 KB"),
        (1_535, "1 KB"),
        (1_536, "2 KB"),
        (1_048_063, "1023 KB"),
        (1_048_064, "1.0 MB"),
        (15_938_355, "15.2 MB"),
    ],
)
def test_a_size_is_shown_in_the_unit_it_has_reached(size, shown):
    """Bytes under a KB, the nearest KB under an MB, then MB: with the unit picked
    before rounding, a size just short of an MB reads as 1024 KB.
    """
    (line,) = ledger_delta([_entry(1, "search", size=size)]).splitlines()[1:-1]

    assert line == f"A-0001 search -> {shown}  /scratchpad/tool_results/A-0001-search.txt"


def test_the_delta_follows_what_the_turn_message_already_said():
    """The task leads and the turn's own facts follow it, as before; the delta anywhere
    ahead of them splits the task from what it says about where to work.
    """
    earlier = KeptLedger(exchanges=((_entry(1, "search"),),))
    message = turn_message("go", ("a.txt",), earlier, layout=DEFAULT_LAYOUT)

    assert message == (
        "go\n\n/scratchpad/ is yours to work in (from the shell, scratchpad)."
        " New files in /inputs/: a.txt.\n\n"
        "<ledger_delta>\n"
        "A-0001 search -> 100 B  /scratchpad/tool_results/A-0001-search.txt\n"
        "</ledger_delta>"
    )


def test_the_next_message_lists_what_was_recorded_since_the_last_one(ledgered):
    """A resume adds no message, so the one after it reaches back past the resume to
    the turn that paused: stopping at the resume loses the paused turn's results, and
    reaching further lists again what an earlier message already did.
    """
    an_agent(ledgered, subagents="[researcher]", interrupt_on="[sql_query]", **EVERY_TOOL)
    kf = Kingfisher(ledgered, sessions=LocalSessions())
    _run(ledgered, _calls(("web_search", {"query": "one"}, "c1")), AIMessage("done"), kf=kf)
    paused = _run(
        ledgered,
        _calls(("web_search", {"query": "two"}, "c2")),
        _calls(("sql_query", {"query": "select 2"}, "c3")),
        kf=kf,
    )
    assert paused.stop_reason == AWAITING, "the turn never stopped at the gate"
    Scripted.script.append(AIMessage(content="done"))
    kf.run(
        Resume(
            session_id="s",
            decisions=(Decision(call_id=paused.pending[0].call_id, action="approve"),),
        )
    )
    _run(ledgered, AIMessage(content="done"), kf=kf)

    # Two calls asked, two paused, one resumed, one asked.
    assert len(Scripted.heard) == 6
    assert _listed_in(_asked(0)) is None, "a first turn has nothing to list"
    assert _listed_in(_asked(2)) == ["A-0001"]
    assert _asked(4) == _asked(3), "the resume was handed a message of its own"
    assert _listed_in(_asked(5)) == ["A-0002", "A-0003"]


def test_a_resume_says_so_on_its_closing_line_and_an_asked_turn_does_not(ledgered):
    """The mark is how the next delta finds the last user message: a resume without it
    hides the turn it paused, and an asked turn with it reaches into the one before.
    """
    an_agent(ledgered, subagents="[researcher]", interrupt_on="[sql_query]", **EVERY_TOOL)
    kf = Kingfisher(ledgered, sessions=LocalSessions())
    paused = _run(ledgered, _calls(("sql_query", {"query": "select 1"}, "c1")), kf=kf)
    Scripted.script.append(AIMessage(content="done"))
    resumed = kf.run(
        Resume(
            session_id="s",
            decisions=(Decision(call_id=paused.pending[0].call_id, action="approve"),),
        )
    )

    assert [row for row in _rows(ledgered) if "id" not in row] == [
        {"turn": paused.turn_id},
        {"turn": resumed.turn_id, "resumed": True},
    ]


def test_rows_a_crash_left_without_their_closing_line_are_not_counted_as_the_next_turns(
    ledgered,
):
    """Every turn rewrites the whole ledger, so a crash can leave a turn's rows without
    its closing line, with the next turn's rows written after them: read by position,
    a later message lists them as that next turn's.
    """
    _run(ledgered, _calls(("web_search", {"query": "one"}, "c1")), AIMessage("done"))
    ledger = _session(ledgered) / HARNESS / LEDGER_FILE
    ledger.write_text("\n".join(ledger.read_text().splitlines()[:-1]) + "\n")
    _run(ledgered, _calls(("web_search", {"query": "two"}, "c2")), AIMessage("done"))
    _run(ledgered, AIMessage(content="done"))

    assert [row.get("id") for row in _rows(ledgered)][:2] == ["A-0001", "A-0002"]
    assert _listed_in(_asked()) == ["A-0002"]


def test_the_next_message_lists_a_subagents_results_under_its_answer(ledgered):
    """Read back from the ledger rather than handed over in memory, a working entry sits
    under its answer only if its run came back with it.
    """
    _delegated(ledgered)
    _run(ledgered, AIMessage(content="noted"))

    assert _listed_in(_asked()) == ["A-0003", "  A-0001", "  A-0002"]


def test_a_superseded_subagents_results_are_listed_as_never_answered(ledgered):
    """A new request drops a subagent paused at a gate. What it read is real and kept,
    but its answer never comes, so there is nothing for its results to sit under.
    """
    an_agent(ledgered, subagents="[researcher]", interrupt_on="[sql_query]", **EVERY_TOOL)
    paused = _run(
        ledgered,
        _task("t1"),
        _calls(("web_search", {"query": "BYD"}, "c1")),
        _calls(("sql_query", {"query": "select 1"}, "c2")),
    )
    assert paused.stop_reason == AWAITING, "the subagent never stopped at the gate"

    _run(ledgered, AIMessage(content="something else, then"))

    assert _listed_in(_asked()) == ["A-0001"]
    assert _asked().split("\n</ledger_delta>")[0].endswith("  (its task never answered)")


def test_no_delta_where_nothing_was_recorded_since_the_last_message(ledgered):
    """An empty block tells the model nothing and still costs every message it is in."""
    _run(ledgered, AIMessage(content="hello"))
    _run(ledgered, AIMessage(content="again"))

    assert [_listed_in(_asked(n)) for n in (0, 1)] == [None, None]


def test_a_graph_the_deployment_supplied_is_handed_no_delta(ledgered):
    """Nothing of kingfisher's records on such a graph and its prompt need not be
    kingfisher's, so a delta there lists results in a block nothing taught it to read.
    """
    _run(ledgered, _calls(("web_search", {"query": "EV"}, "c1")), AIMessage(content="done"))
    model = Scripted(model="m", base_url="u", api_key="k", max_tokens=1, timeout=1.0)
    graph = build_agent(
        ledgered, backend=backend_at(ledgered, _session(ledgered)), model=model
    ).graph
    Scripted.script.append(AIMessage(content="supplied"))

    result = Kingfisher(ledgered, graph=graph, sessions=LocalSessions()).run(
        Request("again", session_id="s")
    )

    assert result.answer == "supplied", "the supplied graph never ran"
    assert _asked().startswith("again")
    assert _listed_in(_asked()) is None


def test_the_system_prompt_is_the_same_whatever_the_ledger_holds(ledgered):
    """The system prompt is the cached prefix: anything in it that moves with the
    ledger misses the cache on every call of every turn after the first.
    """
    _run(ledgered, _calls(("web_search", {"query": "EV"}, "c1")), AIMessage(content="done"))
    _run(ledgered, AIMessage(content="done"))

    first, later = Scripted.heard[0][0], Scripted.heard[2][0]
    assert first.type == later.type == "system"
    assert first.content == later.content
    assert _listed_in(_asked(2)) == ["A-0001"], "the control: the ledger had nothing to say"


# -- the index, once the conversation has been summarised --------------------------

#: A model window, in tokens, that the short turns below stay well inside.
#: deepagents' trigger counts the system prompt and tools as well, so a window much
#: narrower summarises every turn.
WINDOW = 80_000

#: Half that window, at the four characters a token deepagents counts. One stays under
#: the trigger and two go past it -- and the second has to fit once the first is
#: summarised, because deepagents refuses a request it cannot bring under 95% of the
#: window less the output tokens, and a question longer than the window never can.
#: Under the 50,000 tokens past which it keeps a question aside instead of sending it.
HALF = "q" * 160_000


def _windowed(cfg, window: int = WINDOW):
    """`cfg` with every model declaring `window`, which is what deepagents' real
    summariser reads to decide when to fire.
    """
    models = {
        name: replace(one, extra={"profile": {"max_input_tokens": window}})
        for name, one in cfg.models.models.items()
    }
    return replace(cfg, models=replace(cfg.models, models=models))


def _say(kf: Kingfisher, task: str, *script: AIMessage):
    Scripted.script.extend(script)
    return kf.run(Request(task, agent="only", session_id="s"))


def _closing(cfg) -> list[dict[str, Any]]:
    return [row for row in _rows(cfg) if "id" not in row]


def _block_in(message: str, tag: str) -> list[str] | None:
    """The lines of one block in a message, or `None` where it has none."""
    if f"<{tag}>" not in message:
        return None
    return message.split(f"<{tag}>\n", 1)[1].split(f"\n</{tag}>", 1)[0].splitlines()


def test_a_turn_says_on_its_closing_line_whether_it_ended_summarised(ledgered):
    """The summary is private state only the turn's final state holds, so read anywhere
    else, or under a key deepagents renamed, the mark is never written and the index
    never shown -- and written regardless, a turn that summarised nothing claims to.

    Driven through deepagents' real summariser: a mark set by hand passes whatever the
    key is called.
    """
    cfg = _windowed(ledgered)
    kf = Kingfisher(cfg, sessions=LocalSessions())

    first = _say(kf, HALF, AIMessage(content="done"))
    assert Scripted.summaries == 0, "the control: a turn under the trigger was summarised"
    second = _say(kf, HALF, AIMessage(content="done"))

    assert Scripted.summaries == 1, "the summariser never fired on the second turn"
    assert _closing(cfg) == [{"turn": first.turn_id}, {"turn": second.turn_id, "summarised": True}]


def test_a_resume_of_a_summarised_turn_is_marked_summarised_too(ledgered):
    """A resume carries on the paused turn's conversation, summary and all: unmarked, the
    next message lists a delta for a conversation whose earlier lines are gone.
    """
    an_agent(ledgered, subagents="[researcher]", interrupt_on="[sql_query]", **EVERY_TOOL)
    cfg = _windowed(ledgered)
    kf = Kingfisher(cfg, sessions=LocalSessions())
    # Half before the paused turn, which its summary replaces.
    asked = _say(kf, HALF, AIMessage(content="done"))
    paused = _say(kf, HALF, _calls(("sql_query", {"query": "select 1"}, "c1")))
    assert paused.stop_reason == AWAITING, "the turn never stopped at the gate"
    assert Scripted.summaries == 1, "the summariser never fired on the paused turn"

    Scripted.script.append(AIMessage(content="done"))
    resumed = kf.run(
        Resume(
            session_id="s",
            decisions=(Decision(call_id=paused.pending[0].call_id, action="approve"),),
        )
    )

    assert _closing(cfg) == [
        {"turn": asked.turn_id},
        {"turn": paused.turn_id, "summarised": True},
        {"turn": resumed.turn_id, "resumed": True, "summarised": True},
    ]


def test_once_a_turn_summarised_the_next_message_carries_the_index_and_not_the_delta(
    ledgered,
):
    """The delta lists only what is new, and the messages that listed the rest are what
    a summary replaces: shown it after a summary, the model has lost every earlier id.

    Through the turn loop and the real summariser over several turns, reading what the
    model was handed, because the mark crosses from one turn's final state to the next
    turn's message and nothing short of both turns shows it arriving.
    """
    cfg = _windowed(ledgered)
    kf = Kingfisher(cfg, sessions=LocalSessions())
    _say(kf, HALF, _calls(("web_search", {"query": "one"}, "c1")), AIMessage(content="done"))
    _say(kf, HALF, _calls(("web_search", {"query": "two"}, "c2")), AIMessage(content="done"))
    assert Scripted.summaries >= 1, "the summariser never fired on the second turn"
    _say(kf, "go", AIMessage(content="done"))
    _say(kf, "go", AIMessage(content="done"))

    # Two calls a turn for the first two, one for each after.
    assert len(Scripted.heard) == 6
    assert _block_in(_asked(2), "ledger_delta") is not None, "the second turn had no delta"
    assert _block_in(_asked(2), "ledger_index") is None, "indexed before anything summarised"
    for after in (4, 5):
        assert _block_in(_asked(after), "ledger_delta") is None
        assert [line.split()[0] for line in _block_in(_asked(after), "ledger_index") or ()] == [
            "A-0001",
            "A-0002",
        ]
    # The cached prefix, whichever block the message carried.
    assert {Scripted.heard[call][0].type for call in (0, 2, 4, 5)} == {"system"}
    assert len({Scripted.heard[call][0].text for call in (0, 2, 4, 5)}) == 1


def _hand_written(cfg, *turns: tuple[str, tuple[str, ...]]) -> None:
    """A ledger of one recorded result a turn, each turn closed with the marks given."""
    rows = []
    for number, (turn, marks) in enumerate(turns, start=1):
        rows.append(json.dumps(asdict(replace(_entry(number, "web_search"), turn=turn))))
        rows.append(json.dumps({"turn": turn, **dict.fromkeys(marks, True)}))
    ledger = _session(cfg) / HARNESS / LEDGER_FILE
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text("\n".join(rows) + "\n", encoding="utf-8")


#: Each block, and the one a message carries in its place.
_INSTEAD = {"ledger_delta": "ledger_index", "ledger_index": "ledger_delta"}


@pytest.mark.parametrize(
    ("earlier", "asked", "resumed", "shown"),
    [
        ((), (), (), "ledger_delta"),
        (("summarised",), (), (), "ledger_delta"),
        ((), ("summarised",), (), "ledger_index"),
        ((), (), ("summarised",), "ledger_index"),
    ],
    ids=["none", "an-earlier-exchange", "the-asked-turn", "its-resume"],
)
def test_the_index_follows_a_summary_since_the_last_message_and_no_other(
    ledgered, earlier, asked, resumed, shown
):
    """A resume adds no message, so read off the last turn alone a summary made before
    a pause is missed whenever its resume did not summarise again -- and read further
    back than the last message, one the conversation has since outgrown shows an index
    nothing needs.
    """
    _hand_written(ledgered, ("t1", earlier), ("t2", asked), ("t3", ("resumed", *resumed)))

    _run(ledgered, AIMessage(content="done"))

    assert _block_in(_asked(), shown) is not None
    assert _block_in(_asked(), _INSTEAD[shown]) is None


def _one_each(*numbers: int) -> list[tuple[LedgerEntry, ...]]:
    """An exchange for each number, each recording one search."""
    return [(_entry(number, "search"),) for number in numbers]


def _full(*numbers: int) -> list[str]:
    """Those searches, each as the delta lists it."""
    return [
        f"{entry_id(n)} search -> 100 B  /scratchpad/tool_results/{entry_id(n)}-search.txt"
        for n in numbers
    ]


#: An exchange whose `task` ran three calls and answered, and then searched once more.
DELEGATED = (
    _entry(2, "web_search", run="t1"),
    _entry(3, "sql_query", run="t1"),
    _entry(4, "web_search", run="t1"),
    _entry(5, "task", call="t1"),
    _entry(6, "web_search"),
)


def test_the_last_five_exchanges_are_listed_in_full_and_each_older_one_as_a_line():
    """Moved, the boundary lists too much or too little in full, and out of order the
    one-line rows read as the latest work rather than the oldest.
    """
    shown = ledger_index([*_one_each(1), DELEGATED, *_one_each(7, 8, 9, 10, 11)])

    assert shown.splitlines() == [
        "<ledger_index>",
        "A-0001: 1 search",
        "A-0002..A-0006: 3 web_search, 1 sql_query, 1 task",
        *_full(7, 8, 9, 10, 11),
        "</ledger_index>",
    ]
    # Exactly five: the oldest of them is listed in full, not rolled up.
    assert ledger_index(_one_each(7, 8, 9, 10, 11)).splitlines()[1:-1] == _full(7, 8, 9, 10, 11)


def test_an_exchange_in_full_is_listed_exactly_as_the_delta_lists_it():
    """The index's recent part is the delta's rendering of each exchange: copied rather
    than reused, the two drift, and a subagent's results stop sitting under its answer
    in one of them.
    """
    unanswered = (_entry(12, "web_search", run="t9"), _entry(13, "search"))

    shown = ledger_index([DELEGATED, unanswered]).splitlines()[1:-1]

    assert shown == [
        *ledger_delta(DELEGATED).splitlines()[1:-1],
        *ledger_delta(unanswered).splitlines()[1:-1],
    ]
    assert shown[1].startswith("  A-0002"), "the control: nothing sat under its answer"
    assert shown[-1].endswith("(its task never answered)"), "the control: all answered"


def test_an_exchange_that_recorded_nothing_has_no_line_and_is_not_counted():
    """Most exchanges record nothing, and counted among the five they push one that did
    out of the full listing, to make room for lines that say nothing.
    """
    empty: tuple[LedgerEntry, ...] = ()

    shown = ledger_index(
        [empty, *_one_each(1), empty, *_one_each(2, 3), empty, *_one_each(4, 5, 6), empty]
    )

    assert shown.splitlines()[1:-1] == ["A-0001: 1 search", *_full(2, 3, 4, 5, 6)]


def test_a_resume_is_rolled_up_with_the_turn_it_answered(ledgered):
    """A paused `task` answers in its resume, and counted as an exchange of its own the
    rolled-up line splits one task's work in two, its answer counted apart from the
    results it rests on.

    Through real turns, because the grouping is read back from the closing lines the
    turns themselves wrote, and a hand-written ledger would only agree with itself.
    """
    an_agent(ledgered, subagents="[researcher]", interrupt_on="[sql_query]", **EVERY_TOOL)
    cfg = _windowed(ledgered)
    kf = Kingfisher(cfg, sessions=LocalSessions())
    paused = _say(
        kf,
        "go",
        _task("t1"),
        _calls(("web_search", {"query": "BYD"}, "c1")),
        _calls(("sql_query", {"query": "select 1"}, "c2")),
    )
    assert paused.stop_reason == AWAITING, "the subagent never stopped at the gate"
    Scripted.script.extend([AIMessage(content="thin margins"), AIMessage(content="done")])
    kf.run(
        Resume(
            session_id="s",
            decisions=(Decision(call_id=paused.pending[0].call_id, action="approve"),),
        )
    )
    _say(kf, HALF, AIMessage(content="done"))
    for n in range(3, 8):
        _say(kf, "go", _calls(("web_search", {"query": f"q{n}"}, f"c{n}")), AIMessage("done"))
    assert Scripted.summaries == 0, "the control: summarised before the second half"
    _say(kf, HALF, AIMessage(content="done"))
    assert Scripted.summaries >= 1, "the summariser never fired on the second half"

    _say(kf, "go", AIMessage(content="done"))

    index = _block_in(_asked(), "ledger_index") or [""]
    assert index[0] == "A-0001..A-0003: 1 sql_query, 1 task, 1 web_search"
    assert [line.split()[0] for line in index[1:]] == [entry_id(n) for n in range(4, 9)]


def test_an_older_results_file_is_found_by_its_id_as_the_prompt_says(ledgered):
    """Rolled up, an exchange keeps only its ids, so an id is all the model has to find
    a result by: named or ended otherwise than `system.md` says, the file is one it
    cannot find again.

    Through the agent's own `glob` and `grep`, under the rules its turn runs with, and
    with the patterns read out of the prompt rather than written here, so that a test
    spelling them right cannot pass beside a prompt spelling them wrong.
    """
    taught = " ".join(render_system_prompt(layout=DEFAULT_LAYOUT).split())
    by_name = re.search(r"`glob` for `([^`]+)` in `([^`]+)`", taught)
    by_line = re.search(r"ends in its `([^`]+)` line, so `grep` for that in `([^`]+)`", taught)
    assert by_name and by_line, "the prompt no longer says how to find a file by its id"
    first = _run(
        ledgered,
        _calls(("web_search", {"query": "EV"}, "c1")),
        _calls(("loud", {}, "c2")),
        AIMessage(content="done"),
    )
    kept, offloaded = (next(e for e in first.ledger if e.tool == t) for t in ("web_search", "loud"))
    assert kept.path.startswith(TOOL_RESULTS_ROUTE), "the control: the search was not kept"
    assert offloaded.path == f"{SCRATCH_ROUTE}large_tool_results/c2", "the control: not offloaded"

    example = entry_id(42)
    _run(
        ledgered,
        _calls(
            ("glob", {"pattern": by_name[1].replace(example, kept.id), "path": by_name[2]}, "c3"),
            ("grep", {"pattern": by_line[1].replace(example, offloaded.id), "path": by_line[2]},
             "c4"),
        ),
        AIMessage(content="done"),
    )

    assert kept.path in _handed("c3").text
    assert offloaded.path in _handed("c4").text
