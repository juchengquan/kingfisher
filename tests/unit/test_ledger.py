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
import time
from collections.abc import Sequence
from dataclasses import asdict, replace
from types import SimpleNamespace
from typing import Any

import pytest
from deepagents.backends.protocol import FileUploadResponse
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command

from kingfisher import Capabilities, Kingfisher, Request
from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.agent.middlewares.recorded import summary
from kingfisher.core.agent.middlewares.recorder import (
    PREVIEW_CHARS,
    RecorderMiddleware,
    made_together,
)
from kingfisher.core.agent.prompting import render_system_prompt
from kingfisher.core.layout import (
    DEFAULT_LAYOUT,
    HARNESS,
    LEDGER_FILE,
    PAUSED_STATE,
    SCRATCH,
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
    """Definitions with the tools above, `researcher`, and an agent that consults it."""
    tools_dir(scripted).mkdir(parents=True, exist_ok=True)
    (tools_dir(scripted) / "probe.py").write_text(TOOLS, encoding="utf-8")
    subagents_dir(scripted).mkdir(parents=True, exist_ok=True)
    (subagents_dir(scripted) / "researcher.yaml").write_text(RESEARCHER, encoding="utf-8")
    an_agent(scripted, subagents="[researcher]", **EVERY_TOOL)
    start(scripted, "s")
    return scripted


def _calls(*made: tuple[str, dict[str, Any], str], message: str | None = None) -> AIMessage:
    """One model turn asking for these calls together, `message` its own id."""
    return AIMessage(
        content="",
        id=message,
        tool_calls=[{"name": name, "args": args, "id": at} for name, args, at in made],
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


# -- when and where it is kept -------------------------------------------------


class _Ordered(LocalSessions):
    """Kingfisher's own backends, noting every file written or deleted through one, in order."""

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
        delete = opened.delete

        def deleted(path):
            self.written.append(f"deleted {path}")
            return delete(path)

        opened.delete = deleted
        return opened

    def at(self, name: str) -> int:
        """Where the first write of a path ending in `name` came."""
        return next(i for i, path in enumerate(self.written) if path.endswith(name))


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


def test_numbering_goes_on_past_four_digits(ledgered):
    """`A-{n:04d}` is a minimum width: a matcher fixed at four digits would restart or
    reuse ids at the 10,000th result, and the new file would replace an old one's."""
    _run(ledgered, _calls(("web_search", {"query": "one"}, "c1")), AIMessage(content="done"))
    ledger = _session(ledgered) / HARNESS / LEDGER_FILE
    ledger.write_text(ledger.read_text().replace('"A-0001"', '"A-9999"', 1))

    second = _run(
        ledgered, _calls(("web_search", {"query": "two"}, "c2")), AIMessage(content="done")
    )
    third = _run(
        ledgered, _calls(("web_search", {"query": "three"}, "c3")), AIMessage(content="done")
    )

    assert [e.id for e in second.ledger] == ["A-10000"]
    assert [e.id for e in third.ledger] == ["A-10001"]
    assert _kept(ledgered) == [
        "A-0001-web_search.txt", "A-10000-web_search.txt", "A-10001-web_search.txt",
    ]


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

    (entry,) = result.ledger
    assert entry.call == "c2"
    assert _kept(ledgered) == [f"{entry.id}-web_search.txt"]


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


class _Reminder(AgentMiddleware):
    """A deployment's middleware that appends a line to every tool result."""

    name = "reminder"

    def _annotate(self, result: Any) -> Any:
        return result.model_copy(update={"content": f"{result.content}\n\nMind the source."})

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        return self._annotate(handler(request))

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        return self._annotate(await handler(request))


def test_what_a_deployments_middleware_left_is_recorded(ledgered):
    """The model reads the result with a deployment's reminder on it, and a row of
    anything else would be a record of a result nobody was shown.
    """
    an_agent(ledgered, subagents="[researcher]", middlewares="[reminder]", **EVERY_TOOL)
    kf = Kingfisher(ledgered, sessions=LocalSessions(), middlewares={"reminder": _Reminder})

    result = _run(
        ledgered,
        _calls(("web_search", {"query": "EV"}, "c1")),
        AIMessage(content="done"),
        kf=kf,
    )

    (entry,) = result.ledger
    assert _read(ledgered, entry.path).endswith("\n\nMind the source.")


def test_a_result_too_long_to_show_is_recorded_whole(ledgered):
    """Eviction swaps a long result for a stub, and a recorder outside it would keep
    the stub: a row saying 'a few KB' for a result of a hundred.
    """
    result = _run(ledgered, _calls(("loud", {}, "c1")), AIMessage(content="done"))

    (entry,) = result.ledger
    assert entry.size > 100_000
    assert entry.summary.startswith("loud -- row 00000 ")



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


def test_a_result_file_is_swept_only_once_the_transcript_is_written(ledgered):
    """Swept before it, a crash between the two leaves the conversation the next turn is
    rebuilt from naming a file that has gone.
    """
    stray = _session(ledgered) / SCRATCH / TOOL_RESULTS / "A-0007-web_search.txt"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_text("from a turn that died", encoding="utf-8")
    backends = _Ordered()
    _run(
        ledgered,
        _calls(("web_search", {"query": "EV"}, "c1")),
        AIMessage(content="done"),
        kf=Kingfisher(ledgered, sessions=backends),
    )

    assert not stray.exists(), "the stray file was never swept"
    assert backends.at(TRANSCRIPT_FILE) < backends.at("A-0007-web_search.txt")


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
        # One call per message: asked together they run at once, and an id is taken as a
        # call starts, so `execute` sometimes took `A-0001` and the names below did not hold.
        _calls(("web_search", {"query": "EV"}, "c1")),
        _calls(("execute", {"command": "printf mine > scratchpad/tool_results/notes.md"}, "c2")),
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
    """A provider reusing a call id across turns once pointed the second row at the
    first result's file, and the sweep then took the copy that was right.

    It cannot now, and that is the point: a file is named from the row's own id, which
    is unique in the session, so the call id reaching it twice names nothing twice.
    Kept because the shape that broke it is a provider's to produce, not ours, and a
    future name taken from the call id would break it the same way.
    """
    first = _run(ledgered, _calls(("loud", {}, "c1")), AIMessage(content="done"))
    second = _run(
        ledgered, _calls(("web_search", {"query": "EV"}, "c1")), AIMessage(content="done")
    )

    (was,) = first.ledger
    (now,) = second.ledger
    assert was.call == now.call == "c1", "the control: the provider reused the id"
    assert now.path != was.path, "two rows name one file, and only one of them is right"
    assert _read(ledgered, now.path).startswith("Ten results for EV")
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


@pytest.mark.parametrize(
    ("sessions", "awaited"),
    # `_Refusing` refuses the sync save only, so the awaited half goes through `_Raising`.
    [(_Refusing, False), (_Raising, False), (_Raising, True)],
    ids=["refused", "raised", "raised-arun"],
)
def test_a_long_result_the_backend_would_not_keep_is_previewed_saying_so(
    ledgered, sessions, awaited
):
    """Handed whole, a result nothing kept could overflow the window, being any size; and
    previewed with a `full result:` line, it named a file that was never written.
    """
    kf = Kingfisher(ledgered, sessions=sessions())
    request = Request("go", agent="only", session_id="s")
    Scripted.script.extend([_calls(("loud", {}, "c1")), AIMessage(content="done")])

    result = asyncio.run(kf.arun(request)) if awaited else kf.run(request)

    assert result.answer == "done", "the save failing failed the call"
    shown = _handed("c1").text
    assert _kept(ledgered) == [], "the control: the backend kept the file after all"
    assert len(shown) < 80_000, "the model was handed the whole of a result nothing kept"
    assert shown.startswith("row 00000 "), "the preview kept no head of the result"
    assert "row 00999 " in shown, "the preview kept no tail of the result"
    assert "full result:" not in shown, "the model was pointed at a file that is not there"
    assert shown.endswith("\nfull result not kept; call loud again for less\n[A-0001]")


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
    assert answer.id == "A-0001", "the answer is numbered as its call started, before its work"
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
        ("t1", "main", None),
        ("t2", "working", "t1"),
        ("c1", "working", "t1"),
    ]


#: A program that calls a tool itself, then hands `researcher` the rest.
PROGRAM = """(async () => {
  const found = await tools.webSearch({query: 'EV'});
  const answer = await task({description: 'dig into it', subagentType: 'researcher'});
  return found + ' | ' + JSON.stringify(answer);
})()"""


@pytest.mark.parametrize("awaited", [False, True], ids=["run", "arun"])
def test_a_subagent_a_program_starts_is_filed_under_the_program(ledgered, awaited):
    """Only `task` once started a run, so a subagent started by `task()` from `eval` was
    filed as the agent's own calls, under no run, and the next message listed them as work
    the agent had done itself.
    """
    cfg = replace(ledgered, interpreter_enabled=True)
    Scripted.script.extend(
        [
            _calls(("eval", {"code": PROGRAM}, "e1")),
            _calls(("sql_query", {"query": "select margin"}, "c1")),
            AIMessage(content="margins are thin"),
            AIMessage(content="done"),
        ]
    )
    kf = Kingfisher(cfg, sessions=LocalSessions())
    # Granted by name: a request granting nothing leaves `tools.*` off inside `eval`.
    granted = Capabilities(builtin_tools=("eval", "task"), tools=("web_search", "sql_query"))
    request = Request("go", agent="only", session_id="s", capabilities=granted)
    result = asyncio.run(kf.arun(request)) if awaited else kf.run(request)

    # The program's own call has no row: the agent was handed only what the program returned.
    assert [(e.call, e.role, e.run) for e in result.ledger] == [
        ("e1", "main", None),
        ("c1", "working", "e1"),
    ]
    program = result.ledger[0].path
    assert program and "Ten results for EV" in _read(cfg, program), "the program never called"
    _run(cfg, AIMessage(content="noted"))
    assert _listed_in(_asked()) == ["A-0001", "  A-0002"]


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
        ("t1", "main", None),
        ("c1", "working", "t1"),
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


# -- an id taken as the call starts ---------------------------------------------

#: Each call a `_Namer` saw, by call id, and the id it was told before the call ran.
_TOLD: list[tuple[str, str | None]] = []


class _Namer(AgentMiddleware):
    """A deployment's middleware inside the recorder, which would name a file of its own
    after the id a call's result is about to have."""

    name = "namer"
    wants = frozenset({"result_id"})

    def __init__(self, result_id: Any) -> None:
        super().__init__()
        self._result_id = result_id

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        _TOLD.append((request.tool_call["id"], self._result_id()))
        return handler(request)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        _TOLD.append((request.tool_call["id"], self._result_id()))
        return await handler(request)


def _named(cfg, **fields: str) -> Kingfisher:
    """A service whose agent names `_Namer`, with nothing told yet. Last, after every
    definition the test writes: the service reads them as it is made."""
    _TOLD.clear()
    an_agent(cfg, subagents="[researcher]", middlewares="[namer]", **fields, **EVERY_TOOL)
    return Kingfisher(cfg, sessions=LocalSessions(), middlewares={"namer": _Namer})


@pytest.mark.parametrize("awaited", [False, True], ids=["run", "arun"])
def test_a_middleware_inside_the_recorder_is_told_the_id_before_the_call_runs(
    ledgered, awaited
):
    """Numbered only once the result is back, a middleware inside the recorder has no
    name to give what it writes; told the id of another call made beside it, it names
    its file after a different result.
    """
    (_session(ledgered) / "outputs" / "notes.txt").write_text("alpha\n", encoding="utf-8")
    kf = _named(ledgered)
    Scripted.script.extend(
        [
            _calls(
                ("web_search", {"query": "EV"}, "c1"),
                ("sql_query", {"query": "select 1"}, "c2"),
                ("read_file", {"file_path": "/outputs/notes.txt"}, "c3"),
            ),
            AIMessage(content="done"),
        ]
    )
    request = Request("go", agent="only", session_id="s")

    result = asyncio.run(kf.arun(request)) if awaited else kf.run(request)

    assert dict(_TOLD) == {**{e.call: e.id for e in result.ledger}, "c3": None}
    assert {e.call for e in result.ledger} == {"c1", "c2"}, "the control: a call failed"


def test_a_subagents_file_tool_is_not_told_the_id_of_the_task_it_runs_inside(ledgered):
    """A file tool's result is never recorded, so it has no id; told the one its `task`
    call took, a middleware names something after the subagent's answer instead.
    """
    (subagents_dir(ledgered) / "researcher.yaml").write_text(
        RESEARCHER.replace("system_prompt:", "builtin_tools: [ls]\nmiddlewares: [namer]\n"
                           "system_prompt:"),
        encoding="utf-8",
    )
    kf = _named(ledgered)
    Scripted.script.extend(
        [
            _task("t1"),
            _calls(("ls", {"path": "/outputs"}, "c1"), ("web_search", {"query": "EV"}, "c2")),
            AIMessage(content="found it"),
            AIMessage(content="done"),
        ]
    )

    result = kf.run(Request("go", agent="only", session_id="s"))

    ids = {e.call: e.id for e in result.ledger}
    assert dict(_TOLD) == {"t1": ids["t1"], "c1": None, "c2": ids["c2"]}


def test_a_call_that_fails_leaves_its_id_unused(ledgered):
    """The id is taken before the call runs, and a middleware may have named something
    after it; given to the next call instead, that name points at another result.
    """
    result = _run(
        ledgered,
        _calls(("web_search", {"query": "one"}, "c1")),
        _calls(("broken", {"query": "sales"}, "c2")),
        _calls(("web_search", {"query": "two"}, "c3")),
        AIMessage(content="done"),
    )

    assert [(e.call, e.id) for e in result.ledger] == [("c1", "A-0001"), ("c3", "A-0003")]


def test_an_id_no_row_holds_is_still_not_given_again_the_next_turn(ledgered):
    """A turn's rows alone say nothing of an id whose call failed last, so the next turn
    would number from the row before it and give that id a second time.
    """
    first = _run(ledgered, _calls(("broken", {"query": "sales"}, "c1")), AIMessage("done"))
    second = _run(ledgered, _calls(("web_search", {"query": "EV"}, "c2")), AIMessage("done"))

    assert first.ledger == ()
    assert _rows(ledgered)[0] == {"turn": first.turn_id, "reserved": "A-0001"}
    assert [e.id for e in second.ledger] == ["A-0002"]


def test_a_ledger_written_before_ids_were_reserved_numbers_on_from_its_rows(ledgered):
    """No closing line said `reserved` until now, and a ledger lacking it has to number
    on as it did, not start again where no closing line holds one.
    """
    _hand_written(ledgered, ("t1", ()), ("t2", ()))

    result = _run(ledgered, _calls(("web_search", {"query": "EV"}, "c1")), AIMessage("done"))

    assert [e.id for e in result.ledger] == ["A-0003"]


def test_a_task_paused_at_a_gate_takes_a_new_id_when_it_is_run_again(ledgered):
    """A `task` stopped at a gate before its subagent recorded anything leaves no row,
    and its resume runs the call again; numbered from the rows, it would be given the
    id the paused call was told, which a middleware may have named something after.
    """
    kf = _named(ledgered, interrupt_on="[sql_query]")
    Scripted.script.extend([_task("t1"), _calls(("sql_query", {"query": "select 1"}, "c1"))])
    paused = kf.run(Request("go", agent="only", session_id="s"))
    assert paused.stop_reason == AWAITING, "the subagent never stopped at the gate"
    assert paused.ledger == ()

    Scripted.script.extend([AIMessage(content="one row"), AIMessage(content="done")])
    resumed = kf.run(
        Resume(
            session_id="s",
            decisions=(Decision(call_id=paused.pending[0].call_id, action="approve"),),
        )
    )

    told = [number for call, number in _TOLD if call == "t1"]
    assert told == ["A-0001", "A-0002"], "the control: the task call was not run again"
    assert {e.call: e.id for e in resumed.ledger} == {"t1": "A-0002", "c1": "A-0003"}


def test_rows_are_kept_in_id_order_whatever_order_their_calls_finish_in():
    """Calls made together finish in any order, and an exchange's span of ids is read
    off its first row and its last; kept as they arrive, or ordered as text, past
    `A-9999`, the span runs backwards.
    """
    ledger = Ledger("t", KeptLedger(last=9_998))
    batch = ["c1", "c2"]
    first = ledger.reserve("c1", batch, message="m1")
    second = ledger.reserve("c2", batch, message="m1")

    ledger.add(_entry(10_000, "web_search"))
    ledger.add(_entry(9_999, "sql_query"))

    assert (first, second) == ("A-9999", "A-10000")
    assert [e.id for e in ledger.entries] == ["A-9999", "A-10000"]


# -- a batch numbered as the model wrote it -------------------------------------


def test_a_batch_takes_its_numbers_in_the_order_the_model_wrote_it():
    """Calls made together reach the recorder on threads in any order, so numbered as
    each arrives a batch's ids run in the order its threads started: the delta lists the
    rows against the calls that did not make them, and two runs of one conversation
    number the same batch two ways.
    """
    ledger = Ledger("t", KeptLedger())
    batch = ["c1", "c2", "c3"]

    # The call the model wrote last asks first, being the thread that got there first.
    assert ledger.reserve("c3", batch, message="m1") == "A-0003"
    assert ledger.reserve("c1", batch, message="m1") == "A-0001"
    assert ledger.reserve("c2", batch, message="m1") == "A-0002"
    assert ledger.reserve("c4", ["c4"], message="m2") == "A-0004", "the next batch follows"
    # A model reuses a call id in a later reply, and each is a result of its own:
    # `test_two_calls_of_one_id_in_a_turn_each_keep_their_own_result` drives it for real.
    assert ledger.reserve("c1", ["c1"], message="m3") == "A-0005"


def test_a_batch_is_read_off_the_message_its_call_arrived_in():
    """The conversation holds every batch the turn has made, and the latest message is
    not always the one being numbered: read off the last, a call takes the places of
    calls it was never made with.
    """
    earlier = _calls(
        ("web_search", {"query": "EV"}, "c1"),
        ("sql_query", {"query": "1"}, "c2"),
        message="m1",
    )
    latest = _calls(("web_search", {"query": "GDP"}, "c3"), message="m2")
    state = {"messages": [earlier, ToolMessage(content="r", tool_call_id="c1"), latest]}

    assert made_together(earlier.tool_calls[1], state) == ("m1", ["c1", "c2"])
    assert made_together(latest.tool_calls[0], state) == ("m2", ["c3"])


def test_a_tool_that_is_never_recorded_is_no_part_of_the_batch():
    """A file tool's result takes no number, so counting it would number every call after
    it one too high and leave a gap no row names.
    """
    together = _calls(
        ("web_search", {"query": "EV"}, "c1"),
        ("read_file", {"file_path": "/outputs/notes.txt"}, "c2"),
        ("sql_query", {"query": "select 1"}, "c3"),
        message="m1",
    )

    assert made_together(together.tool_calls[2], {"messages": [together]}) == ("m1", ["c1", "c3"])


def test_a_call_the_conversation_does_not_hold_is_a_batch_of_its_own():
    """A recorder driven outside a graph has no message to read an order off, and a batch
    the call is no part of leaves `reserve` no number to hand back.
    """
    assert made_together({"name": "web_search", "id": "c9"}, {"messages": []}) == ("", ["c9"])


def test_a_batch_keeps_the_models_order_when_its_first_call_asks_last(ledgered, monkeypatch):
    """The flake this fixed: on CI's macOS runner the second call of a batch was numbered
    first, so `c1`'s result was recorded as `A-0002` and the handle the model read named
    the file of the call beside it. Driven rather than waited for -- 25 local runs of the
    test it reddened numbered the batch the same way every time.
    """
    taking = Ledger.reserve

    def late(self: Ledger, call: str, batch: Sequence[str], *, message: str) -> str:
        # The call the model wrote first is the last to ask for a number.
        if call == "c1":
            time.sleep(0.05)
        return taking(self, call, batch, message=message)

    monkeypatch.setattr(Ledger, "reserve", late)
    kf = _named(ledgered)

    result = _run(
        ledgered,
        _calls(("web_search", {"query": "EV"}, "c1"), ("sql_query", {"query": "select 1"}, "c2")),
        AIMessage(content="done"),
        kf=kf,
    )

    assert [call for call, _ in _TOLD] == ["c2", "c1"], "the control: `c1` asked first"
    assert {e.call: e.id for e in result.ledger} == {"c1": "A-0001", "c2": "A-0002"}


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

    assert _handed("t1").content == "margins are thin\n[A-0001]"
    # The researcher's second call, which read what its own two calls returned.
    read = {_handed(call, call=2).content.rsplit("\n", 1)[-1] for call in ("c1", "c2")}
    assert read == {"[A-0002]", "[A-0003]"}


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

    asked = {"name": "task", "id": "t1", "args": {}}
    handed = recorder.wrap_tool_call(
        SimpleNamespace(tool_call=asked, state={"messages": [_calls(("task", {}, "t1"))]}),
        lambda _: returned,
    )

    assert [one.content for one in handed.update["messages"]] == [
        "passed up beside it",
        "thin margins\n[A-0001]",
    ]
    assert (handed.update["todos"], handed.goto) == (["kept"], "model")
    assert answer.content == "thin margins"
    assert returned.update["messages"] == [earlier, answer]


def test_the_id_outlasts_the_preview_as_the_last_line_of_it(ledgered):
    """A long result is shown as its head and tail, so an id anywhere but last is cut
    away with the middle and the model never sees it.
    """
    _run(ledgered, _calls(("loud", {}, "c1")), AIMessage(content="done"))

    shown = _handed("c1").text
    assert len(shown) < 80_000, "the control: it was shown whole and never previewed"
    assert "lines truncated]" in shown, "the control: nothing was left out"
    assert shown.rstrip().endswith("[A-0001]")


WIDE = '''
def one_line() -> str:
    """Return about 220,000 characters of JSON on one line, as an API's minified body is."""
    pad = "x" * 20
    return "[" + ",".join(f'{{"row": {n}, "pad": "{pad}"}}' for n in range(5_000)) + "]"


def wide_rows() -> str:
    """Return forty lines, each longer than half of what a preview shows."""
    return "\\n".join(f"wide {n:02d} " + "x" * 3_000 + f" end {n:02d}" for n in range(40))


def short_lines() -> str:
    """Return 45,000 lines of one character each."""
    return "x\\n" * 45_000


def blank_lines() -> str:
    """Return 90,000 empty lines and one more."""
    return "\\n" * 90_000 + "end"


TOOLS = [one_line, wide_rows, short_lines, blank_lines]
'''


def test_a_result_on_one_line_is_previewed_by_its_ends(ledgered):
    """The preview kept whole lines only, so minified JSON on one line was shown as
    `... [1 lines truncated] ...` and the path, with nothing of the result itself.
    """
    (tools_dir(ledgered) / "wide.py").write_text(WIDE, encoding="utf-8")

    _run(ledgered, _calls(("one_line", {}, "c1")), AIMessage(content="done"))

    shown = _handed("c1").text
    assert len(shown) < 80_000, "the control: it was shown whole and never previewed"
    assert shown.startswith('[{"row": 0, '), "the preview kept no head of the result"
    assert '{"row": 4999, ' in shown, "the preview kept no tail of the result"
    assert shown.count("characters truncated]") == 1, "one line's cut was said twice, or not"
    assert "lines truncated]" not in shown, "one line was counted as left out entirely"


def test_a_result_whose_every_line_is_too_wide_keeps_its_first_and_last(ledgered):
    """Each line longer than half the preview left nothing whole to show, so a result of
    forty wide lines was previewed as a count of them.
    """
    (tools_dir(ledgered) / "wide.py").write_text(WIDE, encoding="utf-8")

    _run(ledgered, _calls(("wide_rows", {}, "c1")), AIMessage(content="done"))

    shown = _handed("c1").text
    assert shown.startswith("wide 00 "), "the first line's start was not shown"
    assert " end 39\nfull result: " in shown, "the last line's end was not shown"
    assert "... [38 lines truncated] ..." in shown, "the two cut lines were counted as lost"
    assert shown.count("characters truncated]") == 2


@pytest.mark.parametrize("tool", ["short_lines", "blank_lines"])
def test_a_preview_of_short_lines_is_no_longer_than_one_of_long_ones(ledgered, tool):
    """The preview's budget counted each line and not its newline, so a blank line cost
    nothing: 90,000 of them went to the model whole, and short lines at twice the size.
    """
    (tools_dir(ledgered) / "wide.py").write_text(WIDE, encoding="utf-8")

    _run(ledgered, _calls((tool, {}, "c1")), AIMessage(content="done"))

    shown = _handed("c1").text
    assert "lines truncated]" in shown, "the control: nothing was left out"
    assert len(shown) <= PREVIEW_CHARS + 200, len(shown)


def test_the_id_outlasts_the_preview_of_a_result_made_of_blocks(ledgered):
    """A block list's text is previewed where it stood and the image kept beside it, so
    the id's own block survives after both -- and a caption still reads as the image's.
    """
    (tools_dir(ledgered) / "blocks.py").write_text(BLOCKS, encoding="utf-8")

    _run(ledgered, _calls(("loud_blocks", {}, "c1")), AIMessage(content="done"))

    shown, image, mark = _handed("c1").content
    assert isinstance(shown, dict), "the text was no longer a block of its own"
    assert len(shown["text"]) < 80_000, "the control: shown whole and never previewed"
    assert "lines truncated]" in shown["text"], "the control: nothing was left out"
    assert isinstance(image, dict) and isinstance(mark, dict), "a block stopped being one"
    assert image["type"] == "image", "the image was lost, or moved behind the text"
    assert str(mark["text"]).strip() == "[A-0001]"


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

    result = _run(
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
    (searched,) = result.ledger
    assert _handed("c3").text.endswith(f"\n[{searched.id}]")


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

    assert _listed_in(_asked()) == ["A-0001", "  A-0002", "  A-0003"]


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

    # `A-0001` is the `task` call's, which never answered.
    assert _listed_in(_asked()) == ["A-0002"]
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
    assert _block_in(_asked(4), "ledger_delta") is None
    indexed = ["A-0001", "A-0002"]
    assert [line.split()[0] for line in _block_in(_asked(4), "ledger_index") or ()] == indexed
    # The turn after does not summarise again, so the message that carried the index is
    # still in the conversation, and a second one would repeat it.
    assert _block_in(_asked(5), "ledger_index") is None
    assert _asked(4) in [message.text for message in Scripted.heard[5]]
    # The cached prefix, whichever block the message carried.
    assert {Scripted.heard[call][0].type for call in (0, 2, 4, 5)} == {"system"}
    assert len({Scripted.heard[call][0].text for call in (0, 2, 4, 5)}) == 1


def test_a_summarised_conversation_is_not_summarised_again_the_next_turn(ledgered):
    """The summary lived only in the turn's private state, so the next turn started from
    every message again, past the trigger, and summarised the whole conversation once a
    turn -- a summary call and a new history file repeating the early messages, each time.

    Driven through deepagents' real summariser over turns, because the bug is in what
    crosses from one turn to the next.
    """
    cfg = _windowed(ledgered)
    kf = Kingfisher(cfg, sessions=LocalSessions())
    _say(kf, HALF, AIMessage(content="done"))
    _say(kf, HALF, AIMessage(content="done"))
    assert Scripted.summaries == 1, "the control: the summariser never fired"

    _say(kf, "go", AIMessage(content="done"))
    _say(kf, "go", AIMessage(content="done"))

    assert Scripted.summaries == 1, "a conversation already summarised was summarised again"
    history = _session(cfg) / SCRATCH / "conversation_history"
    assert len(list(history.glob("*.md"))) == 1
    saved = from_json((_session(cfg) / HARNESS / TRANSCRIPT_FILE).read_text(encoding="utf-8"))
    assert saved[0].summary, "the transcript does not open on the summary"
    assert HALF not in [message.content for message in saved[:1]]


def test_a_saved_summary_is_still_a_summary_to_deepagents(ledgered):
    """deepagents knows its earlier summary by a mark on the message, and leaves it out of
    the next history file; read back unmarked, it is a user message like any other, and
    every later summary copies it into its file.
    """
    cfg = _windowed(ledgered)
    kf = Kingfisher(cfg, sessions=LocalSessions())
    _say(kf, HALF, AIMessage(content="done"))
    _say(kf, HALF, AIMessage(content="done"))
    _say(kf, "go", AIMessage(content="done"))
    assert Scripted.summaries == 1, "summarised afresh, so this is not the saved summary"

    first = Scripted.heard[-1][1]
    assert first.type == "human"
    assert first.additional_kwargs.get("lc_source") == "summarization"


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
    # From `A-0002`: the `task` call took `A-0001` before it paused, and `A-0003` when it
    # was run again on the resume.
    assert index[0] == "A-0002..A-0004: 1 sql_query, 1 task, 1 web_search"
    assert [line.split()[0] for line in index[1:]] == [entry_id(n) for n in range(5, 10)]


def test_an_older_results_file_is_found_by_its_id_as_the_prompt_says(ledgered):
    """Rolled up, an exchange keeps only its ids, so an id is all the model has to find
    a result by: named or ended otherwise than `system.md` says, the file is one it
    cannot find again.

    Through the agent's own `glob`, under the rules its turn runs with, and with the
    pattern read out of the prompt rather than written here, so that a test spelling it
    right cannot pass beside a prompt spelling it wrong.

    One pattern for both: a long result is named after its id like any other, where it
    was once named after its call and could only be found by grepping for the id line it
    ended in.
    """
    taught = " ".join(render_system_prompt(layout=DEFAULT_LAYOUT).split())
    by_name = re.search(r"`glob` for `([^`]+)` in `([^`]+)`", taught)
    assert by_name, "the prompt no longer says how to find a file by its id"
    first = _run(
        ledgered,
        _calls(("web_search", {"query": "EV"}, "c1")),
        _calls(("loud", {}, "c2")),
        AIMessage(content="done"),
    )
    kept, long = (next(e for e in first.ledger if e.tool == t) for t in ("web_search", "loud"))
    # Both in the one folder, short and long alike: the id is what finds either, and a
    # second folder was the reason an id had to be searched for rather than read.
    assert kept.path.startswith(TOOL_RESULTS_ROUTE), "the control: the search was not kept"
    assert long.path.startswith(TOOL_RESULTS_ROUTE), "the control: the long result was not kept"
    assert long.path != kept.path

    example = entry_id(42)
    _run(
        ledgered,
        _calls(
            ("glob", {"pattern": by_name[1].replace(example, kept.id), "path": by_name[2]}, "c3"),
            ("glob", {"pattern": by_name[1].replace(example, long.id), "path": by_name[2]}, "c4"),
        ),
        AIMessage(content="done"),
    )

    assert kept.path in _handed("c3").text
    assert long.path in _handed("c4").text


# -- expiry --------------------------------------------------------------------


def _rounds(cfg, rounds: int | None):
    return replace(cfg, keep_results_rounds=rounds)


def _searched(cfg, at: str):
    return _run(cfg, _calls(("web_search", {"query": at}, at)), AIMessage(content="done"))


def _closed(*rows: dict[str, Any]) -> list[str]:
    """Each row as the ledger writes it, closed as turn `t1`."""
    return [*(json.dumps(row) for row in rows), json.dumps({"turn": "t1"})]


def _written(cfg, lines: list[str]) -> None:
    ledger = _session(cfg) / HARNESS / LEDGER_FILE
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_a_result_older_than_the_rounds_kept_loses_its_file_and_keeps_its_row(ledgered):
    """Kept for every round, a long session's results folder grows without bound; expired
    without its row, an id the model was handed names nothing at all."""
    cfg = _rounds(ledgered, 2)
    _searched(cfg, "c1")
    _searched(cfg, "c2")
    assert _kept(cfg) == ["A-0001-web_search.txt", "A-0002-web_search.txt"]

    third = _run(cfg, AIMessage(content="nothing this time"))

    assert _kept(cfg) == ["A-0002-web_search.txt"]
    first, second = (row for row in _rows(cfg) if "id" in row)
    assert (first["path"], first["expired_in"]) == (None, third.turn_id)
    assert first["summary"] == "web_search query='c1' -- Ten results for c1"
    assert second["path"] == "/scratchpad/tool_results/A-0002-web_search.txt"
    assert second["expired_in"] is None


def test_a_result_a_crash_left_unclosed_expires_with_the_turn_after_it(ledgered):
    """A turn whose closing line a crash cut joins no exchange, and its results were
    kept for good whatever the rounds said. Aged with the exchange that closed next,
    they go no sooner than that turn's own."""
    cfg = _rounds(ledgered, 2)
    _searched(cfg, "c1")
    ledger = _session(cfg) / HARNESS / LEDGER_FILE
    ledger.write_text("\n".join(ledger.read_text().splitlines()[:-1]) + "\n")
    _searched(cfg, "c2")

    _run(cfg, AIMessage(content="nothing this time"))
    assert _kept(cfg) == ["A-0001-web_search.txt", "A-0002-web_search.txt"]

    fourth = _run(cfg, AIMessage(content="nothing again"))

    assert _kept(cfg) == []
    first = next(row for row in _rows(cfg) if row.get("id") == "A-0001")
    assert (first["path"], first["expired_in"]) == (None, fourth.turn_id)


def test_a_result_a_crash_left_unclosed_before_a_resume_expires_with_its_round(ledgered):
    """A resume's closing line joins the round it answers, and so must what a crash
    left open before it: aged with no round at all, it was kept for good."""
    first = asdict(replace(_entry(1, "web_search"), turn="t1"))
    cut = asdict(replace(_entry(2, "web_search"), turn="t2"))
    _written(
        ledgered,
        [
            *_closed(first),
            json.dumps(cut),
            json.dumps({"turn": "t3", "resumed": True}),
        ],
    )

    asked = _run(_rounds(ledgered, 1), AIMessage(content="done"))

    assert [row["id"] for row in _rows(ledgered) if row.get("expired_in") == asked.turn_id] == [
        "A-0001", "A-0002",
    ]


def test_unset_every_result_keeps_its_file(ledgered):
    """The control beside expiry: a deployment that never set it must lose nothing."""
    for at in ("c1", "c2", "c3"):
        _searched(ledgered, at)

    assert len(_kept(ledgered)) == 3
    assert all(row.get("expired_in") is None for row in _rows(ledgered))


def test_a_resume_carries_on_the_round_it_answers(ledgered):
    """Counted as a round of its own, a resume would age the results of the round it
    answers, and a session paused at every gate would lose them a round early."""
    first, second = _entry(1, "web_search"), _entry(2, "web_search")
    kept = KeptLedger(
        lines=tuple(json.dumps(asdict(one)) for one in (first, second)),
        exchanges=((first,), (second,)),
    )

    resumed = Ledger("t3", kept, resumed=True, keep_rounds=1)
    asked = Ledger("t3", kept, keep_rounds=1)

    assert _expired_in(resumed) == ["A-0001"]
    assert _expired_in(asked) == ["A-0001", "A-0002"]


def test_a_result_the_backend_refused_is_never_relabelled_expired():
    """Expired with the rest, a row whose file the backend refused was listed as expired,
    which sends a reader to the rounds kept rather than to the backend that refused it."""
    refused, saved = _entry(1, "web_search", kept=False), _entry(2, "web_search")
    lines = tuple(json.dumps(asdict(one)) for one in (refused, saved))
    closed = KeptLedger(lines=lines, exchanges=((refused, saved),))
    # The rows of a turn a crash left without its closing line, aged with the next.
    cut = KeptLedger(lines=lines, exchanges=((),), unclosed=((refused, saved),))

    assert _expired_in(Ledger("t2", closed, keep_rounds=1)) == ["A-0002"]
    assert _expired_in(Ledger("t2", cut, keep_rounds=1)) == ["A-0002"]


def _expired_in(ledger: Ledger) -> list[str]:
    rows = (json.loads(line) for line in ledger.earlier.lines)
    return [row["id"] for row in rows if row.get("expired_in") == ledger.turn]


def test_a_row_written_before_expiry_existed_is_still_read(ledgered):
    """A field every row must carry reads each older row as a closing line, and the
    delta, the index and clearing lose every result recorded before it."""
    row = asdict(replace(_entry(1, "web_search"), turn="t1"))
    del row["expired_in"]
    _written(ledgered, _closed(row))

    _run(ledgered, AIMessage(content="done"))

    assert _listed_in(_asked()) == ["A-0001"]


def test_an_expired_row_keeps_what_this_version_does_not_read(ledgered):
    """Rewritten from the entry rather than patched, a row loses any key another version
    wrote into it."""
    cfg = _rounds(ledgered, 1)
    _written(cfg, _closed({**asdict(replace(_entry(1, "web_search"), turn="t1")), "later": "x"}))

    _run(cfg, AIMessage(content="done"))

    (row,) = (row for row in _rows(cfg) if "id" in row)
    assert (row["later"], row["path"]) == ("x", None)
    assert row["expired_in"] is not None



def test_an_expired_result_is_listed_as_expired_not_as_not_kept():
    """'not kept' says the backend refused the file, which sends a reader to the wrong
    cause."""
    entry = replace(_entry(1, "web_search"), path=None, expired_in="t2")

    assert ledger_delta([entry]).splitlines()[1].endswith("  expired")
    assert ledger_delta([replace(entry, expired_in=None)]).splitlines()[1].endswith("not kept")


def test_a_search_for_an_expired_result_says_why_it_found_nothing(ledgered):
    """A bare 'not found' for a file a handle named reads as a wrong path, and the model
    spends its calls looking for it."""
    cfg = _rounds(ledgered, 1)
    (entry,) = _searched(cfg, "c1").ledger
    _run(cfg, AIMessage(content="nothing this time"))

    _run(cfg, _calls(("read_file", {"file_path": entry.path}, "c2")), AIMessage(content="done"))

    assert "not found" in _handed("c2").text
    assert "older than 1 rounds of this conversation has expired" in _handed("c2").text


def test_a_read_that_found_its_saved_result_is_not_told_it_expired(ledgered):
    """The control the miss needs from the other side: with results expiring, a read of
    a live one was told it "has expired and its file is gone" once the check that the
    read found nothing went, and the model drops a result it is holding."""
    cfg = _rounds(ledgered, 1)
    (entry,) = _searched(cfg, "c1").ledger

    _run(cfg, _calls(("read_file", {"file_path": entry.path}, "c2")), AIMessage(content="done"))

    assert "not found" not in _handed("c2").text, _handed("c2").text
    assert "expired" not in _handed("c2").text, _handed("c2").text


def test_a_miss_is_not_explained_where_results_do_not_expire(ledgered):
    """The control: told results expire where none do, the model gives up on a path it
    merely mistyped."""
    missing = f"{TOOL_RESULTS_ROUTE}A-0042-web_search.txt"

    _run(ledgered, _calls(("read_file", {"file_path": missing}, "c1")), AIMessage(content="done"))

    assert "not found" in _handed("c1").text
    assert "expired" not in _handed("c1").text


def test_a_miss_outside_the_results_folder_is_not_explained_as_expired(ledgered):
    """The other control, with results expiring: told a saved result expired for a path
    that was never one, the model gives up on an input it merely mistyped."""
    cfg = _rounds(ledgered, 1)

    _run(
        cfg,
        _calls(("read_file", {"file_path": "/inputs/x.csv"}, "c1")),
        AIMessage(content="done"),
    )

    assert "not found" in _handed("c1").text
    assert "expired" not in _handed("c1").text


def test_the_prompt_says_results_expire_only_where_they_do():
    """Unsaid, the prompt promises every result stays findable; said everywhere, a
    deployment keeping all of them tells the model to call tools again for nothing."""
    expiring = render_system_prompt(layout=DEFAULT_LAYOUT, keep_results_rounds=20)
    keeping = render_system_prompt(layout=DEFAULT_LAYOUT)

    assert "older than 20 rounds of this conversation expires" in " ".join(expiring.split())
    assert "expires" not in keeping
