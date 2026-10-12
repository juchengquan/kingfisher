"""The ledger: what a turn's tools returned, recorded, kept as files, and listed.

Through real turns throughout -- a scripted model, kingfisher's own build and session
backend -- because the recorder's position in the stack is most of what it is, and a
stack assembled by hand would put it wherever the test did.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from dataclasses import asdict, replace
from typing import Any

import pytest
import yaml
from deepagents.backends.protocol import FileUploadResponse
from langchain_core.messages import AIMessage

from assets_examples.middlewares.tool_note import ToolNote
from kingfisher import Kingfisher, Request, default_backends
from kingfisher.domain.access import parse
from kingfisher.domain.request import Decision, Resume
from kingfisher.domain.result import AWAITING
from kingfisher.infrastructure.harness.agent import build_agent
from kingfisher.infrastructure.harness.backend import DefaultBackends, backend_at
from kingfisher.infrastructure.sandbox.confinement import EXTERNAL
from kingfisher.layout import HARNESS, LEDGER_FILE, PAUSED_STATE, RESULTS, SCRATCH, TRANSCRIPT_FILE
from tests.conftest import FakeToolCallingModel, an_agent, start, subagents_dir, tools_dir
from tests.unit.scripted import Scripted

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


TOOLS = [web_search, sql_query, broken, loud, line_count]
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
    an_agent(scripted, subagents="[researcher]")
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
    service = kf or Kingfisher(cfg, backends=default_backends)
    return service.run(Request("go", agent="only", session_id="s", **request))


def _session(cfg):
    return cfg.workspace / "sessions" / "s"


def _rows(cfg) -> list[dict[str, Any]]:
    """The ledger as the session keeps it, one decoded line each."""
    text = (_session(cfg) / HARNESS / LEDGER_FILE).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def _kept(cfg) -> list[str]:
    """What the results folder holds, by name."""
    folder = _session(cfg) / SCRATCH / RESULTS
    return sorted(one.name for one in folder.iterdir()) if folder.exists() else []


def _read(cfg, virtual: str) -> str:
    return (_session(cfg) / virtual.lstrip("/")).read_text(encoding="utf-8")


# -- what is recorded ----------------------------------------------------------


def test_a_result_is_recorded_kept_and_handed_to_the_caller(ledgered):
    """The whole of the first slice: a result the model was handed has a row, a file
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
    assert entry.path == "/scratchpad/results/A-0001-web_search.txt"
    assert _read(ledgered, entry.path) == "Ten results for EV price war\n1. one\n2. two"
    assert entry.size == len(_read(ledgered, entry.path).encode("utf-8"))
    assert entry.access is None, "no source_ids.yaml is in force"
    # What the caller holds is the row, through the JSON a service would send.
    sent = json.loads(json.dumps(asdict(result)))
    assert sent["ledger"] == _rows(ledgered)[:1]
    assert _rows(ledgered)[1:] == [{"turn": result.turn_id}]


def test_a_result_that_is_json_is_kept_as_json(ledgered):
    """The suffix is what tells the agent it can parse the file rather than read it."""
    result = _run(
        ledgered, _calls(("sql_query", {"query": "select 1"}, "c1")), AIMessage(content="done")
    )

    (entry,) = result.ledger
    assert entry.path == "/scratchpad/results/A-0001-sql_query.json"
    assert json.loads(_read(ledgered, entry.path)) == {"rows": [[1, "a"], [2, "b"]]}


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
    cfg = replace(ledgered, shell_sandbox=EXTERNAL)
    (_session(cfg) / "outputs" / "notes.txt").write_text("alpha\n", encoding="utf-8")

    result = _run(
        cfg,
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
        kf=Kingfisher(cfg, backends=default_backends),
    )

    # The control: every file tool ran and succeeded, so absent means skipped.
    assert (_session(cfg) / "outputs" / "new.txt").read_text() == "gamma\n"
    assert not (_session(cfg) / "outputs" / "notes.txt").exists()
    assert sorted(e.tool for e in result.ledger) == ["execute", "web_search"]
    (shell,) = [e for e in result.ledger if e.tool == "execute"]
    assert "recorded" in _read(cfg, shell.path)


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


def test_a_tool_name_is_one_path_segment_in_the_results_folder(ledgered):
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
    assert entry.path == "/scratchpad/results/A-0001-up_and_away.txt"
    assert _kept(ledgered) == ["A-0001-up_and_away.txt"]


# -- where the recorder sits ---------------------------------------------------


def test_the_arguments_recorded_are_the_ones_the_model_wrote(ledgered):
    """`WorkspaceToolPaths` rewrites a `path` into a host path, and recorded after it
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
    an_agent(ledgered, subagents="[researcher]", middlewares="[tool-note]")
    kf = Kingfisher(ledgered, backends=default_backends, middlewares={"tool-note": ToolNote})

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
    assert len(_read(ledgered, entry.path)) == entry.size
    assert _kept(ledgered) == [], "kingfisher's own copy was kept beside deepagents'"
    assert _rows(ledgered)[0]["path"] == entry.path


# -- when and where it is kept -------------------------------------------------


class _Ordered(DefaultBackends):
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
        kf=Kingfisher(ledgered, backends=backends),
    )

    assert backends.at("A-0001-web_search.txt") < backends.at(LEDGER_FILE)
    assert backends.at(LEDGER_FILE) < backends.at(TRANSCRIPT_FILE)


def test_a_paused_turn_keeps_its_rows_before_its_state(ledgered):
    """A turn that stops at a gate has run real calls, and its resume carries on from
    the state it left: unkept, their rows are lost and the resume numbers over them.
    """
    an_agent(ledgered, subagents="[researcher]", interrupt_on="[sql_query]")
    backends = _Ordered()
    kf = Kingfisher(ledgered, backends=backends)

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


def test_a_result_file_no_row_names_is_swept(ledgered):
    """A turn that died before writing its rows leaves its files behind, and nothing
    else would ever remove them.
    """
    stray = _session(ledgered) / SCRATCH / RESULTS / "A-0007-web_search.txt"
    stray.parent.mkdir(parents=True)
    stray.write_text("from a turn that died", encoding="utf-8")
    _run(ledgered, _calls(("web_search", {"query": "EV"}, "c1")), AIMessage(content="done"))

    assert not stray.exists()
    assert _kept(ledgered) == ["A-0001-web_search.txt"]

    _run(ledgered, AIMessage(content="nothing this time"))

    assert _kept(ledgered) == ["A-0001-web_search.txt"], "an earlier turn's file was swept"


def test_a_result_the_backend_will_not_keep_still_has_its_row(ledgered, caplog):
    """The call has run and the model is reading its answer: a refused file must not
    become a failed call, which the model may make again and run the tool twice.
    """

    class Refusing(DefaultBackends):
        def open(self, cfg, session_id, /, *, catalogue=None):
            opened = super().open(cfg, session_id, catalogue=catalogue)
            upload = opened.upload_files

            def refused(files):
                if any(path.startswith(f"/{SCRATCH}/{RESULTS}/") for path, _ in files):
                    return [FileUploadResponse(path=p, error="permission_denied") for p, _ in files]
                return upload(files)

            opened.upload_files = refused
            return opened

    with caplog.at_level(logging.WARNING):
        result = _run(
            ledgered,
            _calls(("web_search", {"query": "EV"}, "c1")),
            AIMessage(content="done"),
            kf=Kingfisher(ledgered, backends=Refusing()),
        )

    assert result.answer == "done"
    (entry,) = result.ledger
    assert (entry.id, entry.path) == ("A-0001", None)
    assert _rows(ledgered)[0]["path"] is None
    assert "A-0001 was not kept" in caplog.text
    assert "permission_denied" in caplog.text


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

    result = Kingfisher(ledgered, graph=graph, backends=default_backends).run(
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
    kf = Kingfisher(cfg, backends=default_backends)
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
    an_agent(ledgered, subagents="[researcher, checker]")

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

VOCABULARY = "source_ids: [public, finance]\n"

AUDIENCED_AGENT = """name: only
description: An agent.
source_ids: [public, finance]
subagents: [researcher]
tools:
  - name: web_search
    source_ids: [public]
  - sql_query
system_prompt: |
  You do the task.
"""

AUDIENCED_RESEARCHER = """name: researcher
description: Researches.
source_ids: [finance]
tools:
  - name: web_search
    source_ids: [public]
  - sql_query
system_prompt: |
  You research.
"""


#: Who the caller in these tests is: everyone the vocabulary names, so nothing is
#: narrowed away and every audience is recorded as written.
HOLDING = ["public", "finance"]


def _audienced(cfg, *, gated: str | None = None):
    """`cfg` under `VOCABULARY`, with the agent and `researcher` written with audiences."""
    agent = AUDIENCED_AGENT
    if gated is not None:
        agent = agent.replace("system_prompt:", f"interrupt_on: [{gated}]\nsystem_prompt:")
    (cfg.catalogue_roots["agents"] / "only.yaml").write_text(agent, encoding="utf-8")
    (subagents_dir(cfg) / "researcher.yaml").write_text(AUDIENCED_RESEARCHER, encoding="utf-8")
    return replace(cfg, access=parse(yaml.safe_load(VOCABULARY), source="source_ids.yaml"))


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

    result = Kingfisher(cfg, backends=default_backends).run(
        Request("go", agent="only", session_id="s"), source_ids=HOLDING
    )

    assert {e.call: e.access for e in result.ledger} == {
        # The entry's own audience, then the definition's where the entry has none.
        "c1": ("public",),
        "c2": ("public", "finance"),
        # The subagent's own definition, not the agent's.
        "c3": ("public",),
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
    kf = Kingfisher(cfg, backends=default_backends)
    Scripted.script.extend(
        [
            _task("t1"),
            _calls(("web_search", {"query": "BYD"}, "c1")),
            _calls(("sql_query", {"query": "q"}, "c2")),
        ]
    )
    paused = kf.run(Request("go", agent="only", session_id="s"), source_ids=HOLDING)

    assert paused.stop_reason == AWAITING, "the subagent never stopped at the gate"
    assert [(e.call, e.run, e.access) for e in paused.ledger] == [("c1", "t1", ("public",))]

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
