"""A caller's tool reaching the turn's backend, and the rules that come with it.

Against kingfisher's own backend throughout, never a stand-in. Half of what the
wrapper does is filter what a backend answers with, and a stand-in answers in shapes
of the test's own making -- a directory entry with no trailing slash, a match with no
route in its path -- which the filters would then be proven against instead of the
ones a turn produces.
"""

from __future__ import annotations

import asyncio
import warnings
from dataclasses import replace
from typing import Any

import pytest
from deepagents import FilesystemPermission
from deepagents.backends.protocol import BackendProtocol, SandboxBackendProtocol
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, ToolMessage

from kingfisher import Kingfisher, ToolContext, backend_at, default_backend
from kingfisher.domain.capabilities import Capabilities
from kingfisher.domain.request import Decision, Request, Resume
from kingfisher.domain.result import AWAITING
from kingfisher.infrastructure.harness import runtime
from kingfisher.infrastructure.harness.agent import build_agent
from kingfisher.infrastructure.harness.permitted_backend import PermittedBackend
from kingfisher.infrastructure.harness.tool_guards import guarded_tools
from kingfisher.kinds.tools.catalogue import LocalToolRepository
from tests.conftest import (
    FakeToolCallingModel,
    an_agent,
    start,
    subagents_dir,
    tools_dir,
)
from tests.unit.scripted import Scripted

KEY = "/derived/private/key.txt"
OPEN = "/derived/open.txt"

#: One rule over one folder, both operations, so every call has a path on each side
#: of it.
PRIVATE = FilesystemPermission(
    operations=["read", "write"], paths=["/derived/private/**"], mode="deny"
)


@pytest.fixture
def backend(cfg, session_dir):
    """Kingfisher's backend over one session, holding a file either side of `PRIVATE`."""
    (session_dir / "derived" / "private").mkdir(parents=True)
    (session_dir / "derived" / "private" / "key.txt").write_text("needle: private\n")
    (session_dir / "derived" / "open.txt").write_text("needle: open\n")
    return backend_at(cfg, session_dir)


def _refusal(result: Any) -> str:
    """A result's error, for a test expecting one: `None` fails here, by name, rather
    than as a `TypeError` from the `in` that reads it.
    """
    assert result.error is not None, f"nothing was refused: {result}"
    return result.error


# -- the wrapper ------------------------------------------------------------


def test_a_denied_read_is_refused_where_the_bare_backend_answers(backend):
    """A tool handed the backend itself reads what `read_file` refuses: deepagents
    applies the rules in its file tools, and the backend keeps none of them.
    """
    handed = PermittedBackend(backend, [PRIVATE])

    # The control. Without it this passes against a backend that cannot read the
    # file at all, and says nothing about who refused.
    assert "needle: private" in backend.read(KEY).file_data["content"]

    refused = handed.read(KEY)
    assert refused.file_data is None
    assert refused.error == f"permission denied for read on {KEY}"
    assert "needle: open" in handed.read(OPEN).file_data["content"]


def test_a_denied_write_or_edit_leaves_the_file_as_it_was(backend, session_dir):
    """An error on the result with the write already made would read as a refusal
    and be the opposite.
    """
    handed = PermittedBackend(backend, [PRIVATE])
    private = session_dir / "derived" / "private"

    written = handed.write("/derived/private/new.txt", "x")
    edited = handed.edit(KEY, "private", "changed")

    assert written.error == "permission denied for write on /derived/private/new.txt"
    assert edited.error == f"permission denied for write on {KEY}"
    assert not (private / "new.txt").exists()
    assert (private / "key.txt").read_text() == "needle: private\n"
    # And the same two calls go through where no rule matches.
    assert handed.write("/derived/new.txt", "x").error is None
    assert handed.edit(OPEN, "open", "changed").error is None
    assert (session_dir / "derived" / "open.txt").read_text() == "needle: changed\n"


def test_a_listing_leaves_out_what_may_not_be_read(backend):
    """A refused `read` is little use if `ls`, `glob` and `grep` still say the file is
    there and quote it: `grep` returns the matching line.
    """
    handed = PermittedBackend(backend, [PRIVATE])

    def seen(found: Any) -> list[str]:
        return sorted(one["path"] for one in found)

    # Each control is the bare backend answering with the private path, so a filter
    # that matched nothing -- a changed entry shape -- fails here rather than passing.
    assert seen(backend.ls("/derived").entries) == [OPEN, "/derived/private/"]
    assert seen(backend.glob("**/*.txt", "/derived").matches) == [OPEN, KEY]
    assert seen(backend.grep("needle", "/derived").matches) == [OPEN, KEY]

    assert seen(handed.ls("/derived").entries) == [OPEN]
    assert seen(handed.glob("**/*.txt", "/derived").matches) == [OPEN]
    assert seen(handed.grep("needle", "/derived").matches) == [OPEN]
    assert seen(handed.grep("needle").matches) == [OPEN], "no path named, and still filtered"
    assert KEY not in seen(handed.glob("**/*.txt").matches)
    assert OPEN in seen(handed.glob("**/*.txt").matches)


def test_a_listing_of_a_denied_path_is_refused_rather_than_empty(backend):
    """Filtering alone answers "nothing here", which tells a model the folder is
    empty and sends it looking elsewhere for a file it was never going to be shown.
    """
    handed = PermittedBackend(backend, [PRIVATE])
    inside = "/derived/private/inner"
    refusal = f"permission denied for read on {inside}"

    assert handed.ls(inside).error == refusal
    assert handed.glob("*", inside).error == refusal
    assert handed.grep("needle", inside).error == refusal
    # The folder the rule is written over is the one exception, and it is deepagents'
    # own: `/derived/private/**` matches everything under the folder and not the
    # folder, so it is listed and found empty.
    assert handed.ls("/derived/private").entries == []


def test_a_rule_that_asks_a_person_is_a_refusal_here(backend, session_dir):
    """deepagents lets `interrupt` entries through its filters and through a leaf
    delete, because for a file tool the person has answered by then. Nothing pauses
    for a call made inside a tool, so the same rule would let everything through.
    """
    ask = replace(PRIVATE, mode="interrupt")
    handed = PermittedBackend(backend, [ask])

    assert handed.read(KEY).error == f"permission denied for read on {KEY}"
    assert [one["path"] for one in handed.ls("/derived").entries or []] == [OPEN]
    assert [one["path"] for one in handed.grep("needle", "/derived").matches or []] == [OPEN]
    assert "permission denied for write" in _refusal(handed.delete(KEY))
    assert (session_dir / "derived" / "private" / "key.txt").exists()
    assert ask.mode == "interrupt", "the caller's rule was rewritten in place"


def test_deleting_a_folder_is_refused_for_what_is_under_it(backend, session_dir):
    """`/derived` matches no rule and holds a path that does. Checked the way a write
    is, the delete goes through and takes the denied file with it.
    """
    handed = PermittedBackend(backend, [PRIVATE])

    refused = _refusal(handed.delete("/derived"))

    assert "permission denied for write on /derived" in refused
    assert "/derived/private/**" in refused, "the rule that stopped it is not named"
    assert (session_dir / "derived" / "private" / "key.txt").exists()
    # A leaf no rule reaches still goes, so the refusal above is not a delete that
    # refuses everything.
    assert handed.delete(OPEN).error is None
    assert not (session_dir / "derived" / "open.txt").exists()


def test_a_batch_marks_what_it_refused_and_keeps_each_answer_in_its_place(backend, session_dir):
    """A refused path in the middle of a batch must not move the answers after it up
    by one: the fourth file would be reported under the third path.
    """
    handed = PermittedBackend(backend, [PRIVATE])
    derived = session_dir / "derived"

    sent = handed.upload_files(
        [
            # Written the shell's way, so the answer has a spelling to keep: the
            # backend is handed `/derived/a.bin` and answers with that.
            ("derived/a.bin", b"first"),
            ("/derived/private/b.bin", b"second"),
            ("../c.bin", b"third"),
            ("/derived/d.bin", b"fourth"),
        ]
    )

    assert [(one.path, one.error) for one in sent] == [
        ("derived/a.bin", None),
        ("/derived/private/b.bin", "permission_denied"),
        ("../c.bin", "invalid_path"),
        ("/derived/d.bin", None),
    ]
    assert (derived / "a.bin").read_bytes() == b"first"
    assert (derived / "d.bin").read_bytes() == b"fourth"
    assert not (derived / "private" / "b.bin").exists()

    fetched = handed.download_files([KEY, OPEN, "/derived/d.bin"])

    assert [(one.path, one.error) for one in fetched] == [
        (KEY, "permission_denied"),
        (OPEN, None),
        ("/derived/d.bin", None),
    ]
    assert [one.content for one in fetched] == [None, b"needle: open\n", b"fourth"]


def test_a_batch_that_is_refused_whole_never_reaches_the_backend(backend):
    """An empty list handed on is a call made for nothing, and to a backend a network
    away it is a round trip.
    """
    calls: list[Any] = []
    handed = PermittedBackend(_Spy(backend, calls), [PRIVATE])

    assert [one.error for one in handed.download_files([KEY])] == ["permission_denied"]
    assert [one.error for one in handed.upload_files([(KEY, b"x")])] == ["permission_denied"]
    assert calls == []


class _Spy(BackendProtocol):
    """The two batch calls, counted on the way through."""

    def __init__(self, backend: Any, calls: list[Any]) -> None:
        self._backend = backend
        self._calls = calls

    def upload_files(self, files):
        self._calls.append(files)
        return self._backend.upload_files(files)

    def download_files(self, paths):
        self._calls.append(paths)
        return self._backend.download_files(paths)


def test_the_shell_is_not_on_the_backend_a_tool_is_handed(backend):
    """No rule reaches `execute`. A tool holding it has a way round every one of
    them, and runs commands for a request that was refused the shell.
    """
    handed = PermittedBackend(backend, [PRIVATE])

    assert hasattr(backend, "execute"), "the backend underneath has no shell to withhold"
    assert not hasattr(handed, "execute")
    assert not hasattr(handed, "aexecute")
    assert not isinstance(handed, SandboxBackendProtocol)
    # Still a backend as deepagents means one, which it asks with `isinstance`.
    assert isinstance(handed, BackendProtocol)


@pytest.mark.parametrize(
    "spelling",
    ["derived/private/key.txt", "/derived/./private/key.txt", "/derived/private//key.txt"],
)
def test_another_spelling_of_a_denied_path_is_the_same_path(backend, spelling):
    """The rules are globs, and a path the backend resolves to the denied file while
    no glob matches it as written is read straight past them.
    """
    handed = PermittedBackend(backend, [PRIVATE])

    assert "needle" in backend.read(spelling).file_data["content"], "the spelling resolves"
    assert handed.read(spelling).error == f"permission denied for read on {KEY}"


def test_the_backend_is_asked_for_the_spelling_the_rules_were(cfg, backend):
    """`skills/x` written the shell's way passes the rules as `/skills/x` and, handed
    on as written, is looked for under the session instead of in the catalogue: the
    backend routes on the leading slash.
    """
    skill = cfg.catalogue_roots["skills"] / "probe"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("in the catalogue\n")
    handed = PermittedBackend(backend, [PRIVATE])

    assert backend.read("skills/probe/SKILL.md").error, "the two spellings are one file"
    assert "in the catalogue" in handed.read("skills/probe/SKILL.md").file_data["content"]


def test_a_path_that_climbs_out_is_an_error_on_the_result(backend):
    """Raised, it would reach the model as whatever the tool's exception reads like
    rather than as the refusal every other path gets.
    """
    handed = PermittedBackend(backend, [PRIVATE])

    assert "traversal" in _refusal(handed.read("/derived/../../etc/passwd"))
    assert "traversal" in _refusal(handed.write("../outside.txt", "x"))
    assert "traversal" in _refusal(handed.delete("/derived/../.."))


def test_the_async_calls_keep_the_same_rules(backend, session_dir):
    """Each is a second body rather than the sync one on a thread, so that a backend
    with an async path of its own is not driven through its blocking one -- and a
    second body is a second place for the check to be missing.
    """
    handed = PermittedBackend(backend, [PRIVATE])

    async def ask() -> dict[str, Any]:
        return {
            "read": (await handed.aread(KEY)).error,
            "write": (await handed.awrite("/derived/private/new.txt", "x")).error,
            "edit": (await handed.aedit(KEY, "private", "changed")).error,
            "delete": (await handed.adelete("/derived")).error,
            "ls": [one["path"] for one in (await handed.als("/derived")).entries or []],
            "ls inside": (await handed.als("/derived/private/inner")).error,
            "glob inside": (await handed.aglob("*", "/derived/private/inner")).error,
            "grep inside": (await handed.agrep("needle", "/derived/private/inner")).error,
            "glob": [
                one["path"] for one in (await handed.aglob("**/*.txt", "/derived")).matches or []
            ],
            "grep": [
                one["path"] for one in (await handed.agrep("needle", "/derived")).matches or []
            ],
            "upload": [
                one.error
                for one in await handed.aupload_files(
                    [("/derived/private/b.bin", b"x"), ("/derived/a.bin", b"y")]
                )
            ],
            "download": [one.error for one in await handed.adownload_files([KEY, OPEN])],
        }

    got = asyncio.run(ask())

    assert got["read"] == f"permission denied for read on {KEY}"
    assert got["write"] == "permission denied for write on /derived/private/new.txt"
    assert got["edit"] == f"permission denied for write on {KEY}"
    assert "/derived/private/**" in got["delete"]
    assert got["ls"] == [OPEN]
    inside = "permission denied for read on /derived/private/inner"
    assert (got["ls inside"], got["glob inside"], got["grep inside"]) == (inside,) * 3
    assert got["glob"] == [OPEN]
    assert got["grep"] == [OPEN]
    assert got["upload"] == ["permission_denied", None]
    assert got["download"] == ["permission_denied", None]
    private = session_dir / "derived" / "private"
    assert sorted(one.name for one in private.iterdir()) == ["key.txt"]
    assert (private / "key.txt").read_text() == "needle: private\n"


# -- what a build hands over -------------------------------------------------


def test_the_backend_a_build_hands_over_carries_this_requests_rules(cfg, session_dir, fake_model):
    """A request that declined memory is denied `/memory` by a rule added for that
    request alone. A wrapper built from the standing rules would read it.
    """
    (session_dir / "memory" / "AGENTS.md").write_text("remembered\n")

    built = build_agent(
        replace(cfg, memory_enabled=True),
        session_dir=session_dir,
        model=fake_model,
        capabilities=Capabilities(memory=False),
    )
    handed = built.context.backend

    assert "remembered" in built.backend.read("/memory/AGENTS.md").file_data["content"]
    assert handed.read("/memory/AGENTS.md").error == (
        "permission denied for read on /memory/AGENTS.md"
    )
    # The standing ones too, and a path none of them names.
    assert handed.write("/data/new.txt", "x").error == (
        "permission denied for write on /data/new.txt"
    )
    assert handed.write("/derived/new.txt", "x").error is None


def test_the_graph_declares_the_context_a_tool_annotates(cfg, session_dir, fake_model):
    """A graph delivers a context it is handed whether or not it declared one, so no
    turn would notice the declaration gone. It is what the graph itself says
    `runtime.context` holds, and this is the only thing reading it.
    """
    built = build_agent(cfg, session_dir=session_dir, model=fake_model)

    assert built.graph.context_schema is ToolContext
    assert isinstance(built.context, ToolContext)


# -- a tool, in a graph kingfisher built -------------------------------------


#: The tool `tools.md` writes out, so that what the guide tells an author to type is
#: what these tests run. The two braces are the two things the guide says not to vary.
FIRST_LINE = '''from langchain.tools import ToolRuntime

from kingfisher import ToolContext


def first_line({argument}: str, runtime: {annotation}) -> str:
    """Return the first line of a text file. `{argument}` is the same virtual
    path the file tools take, such as `/data/report.csv`."""
    found = runtime.context.backend.read({argument})
    if found.error:
        raise OSError(found.error)
    return found.file_data["content"].splitlines()[0]


TOOLS = [first_line]
'''

#: The same tool as a class declaring its own schema, which is the reason `tools.md`
#: gives for writing a class at all.
FIRST_LINE_AS_A_CLASS = '''from typing import Type

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from kingfisher import ToolContext


class FirstLineInput(BaseModel):
    file_path: str


class FirstLine(BaseTool):
    name: str = "first_line"
    description: str = "Return the first line of a text file."
    args_schema: Type[BaseModel] = FirstLineInput

    def _run(self, file_path: str, runtime: ToolRuntime[ToolContext]) -> str:
        return runtime.context.backend.read(file_path).file_data["content"].splitlines()[0]


TOOLS = [FirstLine()]
'''


def _a_tool(
    cfg,
    *,
    annotation: str = "ToolRuntime[ToolContext]",
    argument: str = "file_path",
    source: str = FIRST_LINE,
) -> None:
    """Write the one workspace tool these tests run."""
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "first_line.py").write_text(
        source.format(annotation=annotation, argument=argument), encoding="utf-8"
    )


def _notes(session_dir) -> None:
    (session_dir / "data" / "notes.txt").write_text("alpha\nbeta\n", encoding="utf-8")


def _calls(name: str, **args: Any) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call-{name}"}])


def _run(cfg, session_dir, *responses: AIMessage, **keywords: Any) -> list[tuple[bool, Any]]:
    """Every tool result of one run, each with whether a delegate produced it.

    Driven with the build's own context and streamed into delegates, which is what the
    service does -- so a result here is one a turn would have produced.
    """
    built = build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=list(responses)),
        **keywords,
    )
    found: list[tuple[bool, Any]] = []
    for namespace, chunk in built.graph.stream(
        {"messages": [{"role": "user", "content": "go"}]},
        stream_mode="updates",
        subgraphs=True,
        context=built.context,
    ):
        for update in chunk.values():
            found += [
                (bool(namespace), message)
                for message in runtime.messages_in(update)
                if isinstance(message, ToolMessage)
            ]
    return found


def _pydantic_complaints(caught: list[warnings.WarningMessage]) -> list[str]:
    return [str(one.message) for one in caught if "Pydantic serializer" in str(one.message)]


def test_a_workspace_tool_reads_a_session_file_through_the_backend(cfg, session_dir):
    """The whole change: `/data/notes.txt` means to a caller's tool what it means to
    `read_file`, with no host path anywhere in the tool.
    """
    _a_tool(cfg)
    _notes(session_dir)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        ((delegated, result),) = _run(
            cfg,
            session_dir,
            _calls("first_line", file_path="/data/notes.txt"),
            AIMessage(content="done"),
        )

    assert not delegated
    assert (result.status, result.content) == ("success", "alpha")
    assert not _pydantic_complaints(caught), "the documented annotation is not a clean one"


def test_a_bare_runtime_annotation_works_and_warns_on_every_call(cfg, session_dir):
    """The reason `tools.md` spells the annotation out. A bare `ToolRuntime` declares
    a context of `None`, and pydantic says so each time the tool is handed a real
    one -- if this stops failing, the guide's warning has gone stale.
    """
    _a_tool(cfg, annotation="ToolRuntime")
    _notes(session_dir)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        ((_, result),) = _run(
            cfg,
            session_dir,
            _calls("first_line", file_path="/data/notes.txt"),
            AIMessage(content="done"),
        )

    assert result.content == "alpha"
    assert _pydantic_complaints(caught)


def test_an_argument_called_path_never_reaches_the_backend_as_it_was_written(cfg, session_dir):
    """The other half of what `tools.md` says. `path` is rewritten to a host path
    before the tool runs, and the backend takes virtual ones -- so the two ways of
    handing a tool a file cannot be mixed, and the mistake has to be loud.
    """
    _a_tool(cfg, argument="path")
    _notes(session_dir)

    ((_, result),) = _run(
        cfg, session_dir, _calls("first_line", path="/data/notes.txt"), AIMessage(content="done")
    )

    assert result.status == "error"
    assert "is a host path" in result.content
    assert "/data/notes.txt" in result.content, "the refusal does not say what to write instead"


def test_a_tool_is_refused_what_the_turn_denies(cfg, session_dir):
    """The wrapper, from where a model meets it: a tool pointed at the run log comes
    back with the refusal `read_file` gives, not with the log.
    """
    _a_tool(cfg)
    (session_dir / ".harness" / "secret.txt").write_text("not for the agent\n")

    ((_, result),) = _run(
        cfg,
        session_dir,
        _calls("first_line", file_path="/.harness/secret.txt"),
        AIMessage(content="done"),
    )

    assert (result.status, result.content) == (
        "error",
        "Error: OSError: permission denied for read on /.harness/secret.txt",
    )


def test_a_class_declaring_its_own_schema_is_called_without_the_runtime(cfg, session_dir):
    """The shape `tools.md` tells an author not to write this kind of tool in.
    langgraph reads which arguments to fill off the schema, a declared one does not
    name `runtime`, and naming it there fails at import -- so if this stops failing,
    the guide's warning has gone stale.
    """
    _a_tool(cfg, source=FIRST_LINE_AS_A_CLASS)
    _notes(session_dir)

    ((_, result),) = _run(
        cfg,
        session_dir,
        _calls("first_line", file_path="/data/notes.txt"),
        AIMessage(content="done"),
    )

    assert result.status == "error"
    assert "missing 1 required positional argument: 'runtime'" in result.content


HELPER = """name: helper
description: Reads a file's first line.
tools: [first_line]
system_prompt: |
  You call first_line.
"""


def test_a_delegates_tool_is_handed_the_backend_too(cfg, session_dir):
    """deepagents starts a delegate with no context of its own, and whether the
    parent's arrives is langgraph's to decide. Pinned, because a delegate holding the
    same tool and handed `None` fails on the first line of it.
    """
    _a_tool(cfg)
    _notes(session_dir)
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "helper.yaml").write_text(HELPER, encoding="utf-8")

    results = _run(
        cfg,
        session_dir,
        # One model, shared: the delegate names none and runs its parent's, so the
        # second answer is the delegate's first.
        _calls("task", description="read it", subagent_type="helper"),
        _calls("first_line", file_path="/data/notes.txt"),
        AIMessage(content="read"),
        AIMessage(content="done"),
        capabilities=Capabilities(subagents=("helper",)),
    )

    mine = [(delegated, m.content) for delegated, m in results if m.name == "first_line"]
    assert mine == [(True, "alpha")]


def test_the_wrapper_a_compiled_delegates_tools_wear_passes_the_runtime_on(cfg, session_dir):
    """A compiled delegate gets no middleware, so its tools are wrapped instead -- and
    `runtime` is the one argument a model never sends, which a wrapper forwarding
    only what it was called with would drop.
    """
    _a_tool(cfg)
    _notes(session_dir)
    (loaded,) = LocalToolRepository(tools_dir(cfg)).found
    (wrapped,) = guarded_tools([loaded.tool], session_dir)
    graph = create_agent(
        FakeToolCallingModel(
            responses=[_calls("first_line", file_path="/data/notes.txt"), AIMessage(content="ok")]
        ),
        tools=[wrapped],
        context_schema=ToolContext,
    )

    out = graph.invoke(
        {"messages": [{"role": "user", "content": "go"}]},
        context=ToolContext(backend=PermittedBackend(backend_at(cfg, session_dir), [])),
    )

    (result,) = [m for m in out["messages"] if isinstance(m, ToolMessage)]
    assert (result.status, result.content) == ("success", "alpha")


# -- the service ---------------------------------------------------------------


def _session(cfg) -> str:
    start(cfg, "s")
    _notes(cfg.workspace / "sessions" / "s")
    return "s"


def _results(events: list[Any]) -> list[tuple[str | None, str]]:
    return [(event.tool, event.text) for event in events if event.kind == "tool_result"]


def test_a_turn_the_service_runs_hands_the_tool_its_sessions_backend(scripted):
    """`build_agent` makes the context and only the service can pass it: a build that
    carried one nothing drove the graph with would hand every tool `None`.
    """
    _a_tool(scripted)
    an_agent(scripted)
    Scripted.script.extend(
        [_calls("first_line", file_path="/data/notes.txt"), AIMessage(content="done")]
    )
    kf = Kingfisher(scripted, backend=default_backend)

    events = list(kf.stream(Request("go", agent="only", session_id=_session(scripted))))

    assert _results(events) == [("first_line", "alpha")]


def test_a_resumed_turn_is_handed_it_again(scripted):
    """langgraph keeps the context out of the checkpoint. A resume driven without one
    runs the call a person just approved with `runtime.context` as `None`, and the
    approval buys an `AttributeError`.
    """
    _a_tool(scripted)
    an_agent(scripted, interrupt_on="[first_line]")
    Scripted.script.extend(
        [_calls("first_line", file_path="/data/notes.txt"), AIMessage(content="done")]
    )
    kf = Kingfisher(scripted, backend=default_backend)
    paused = kf.run(Request("go", agent="only", session_id=_session(scripted)))
    assert paused.stop_reason == AWAITING, "the turn never stopped, so nothing is resumed"

    events = list(
        kf.stream(
            Resume(
                session_id="s",
                decisions=(Decision(call_id=paused.pending[0].call_id, action="approve"),),
            )
        )
    )

    assert _results(events) == [("first_line", "alpha")]


def test_a_graph_the_caller_built_is_driven_with_no_context_at_all(cfg):
    """Its context is whatever its builder decided. Handed kingfisher's, a graph with
    a schema of its own is given the wrong type; handed `context=None`, one that is
    not langgraph's refuses the keyword.
    """

    class Theirs:
        def __init__(self) -> None:
            self.driven: list[set[str]] = []

        def stream(self, state, **keywords):
            self.driven.append(set(keywords))
            yield ((), "values", {"messages": [AIMessage(content="ok")]})

    graph = Theirs()

    Kingfisher(cfg, graph=graph).run(Request("go"))

    assert graph.driven == [{"config", "stream_mode", "subgraphs"}]
