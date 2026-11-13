"""An offered tool's `path`, resolved where the session's backend keeps it."""

from __future__ import annotations

import json

import pytest
from deepagents import FilesystemPermission
from deepagents.backends import CompositeBackend, FilesystemBackend
from deepagents.backends.protocol import BackendProtocol
from langchain_core.messages import AIMessage, ToolMessage

from kingfisher import Kingfisher, Request
from kingfisher.core.access.capabilities import Capabilities
from kingfisher.core.agent.assembly import build_agent, read_only_permissions
from kingfisher.core.agent.host_paths import UnreachablePathError
from kingfisher.core.agent.permitted_backend import PermittedBackend, host_path
from kingfisher.core.agent.session_paths import SessionPaths
from kingfisher.core.layout import DEFAULT_LAYOUT
from kingfisher.plugins import LocalSessions, ShellSettings, backend_at
from kingfisher.plugins.sessions.sandbox.confinement import harness_unfenced, shell_confinement
from tests.conftest import (
    EVERY_TOOL,
    FakeToolCallingModel,
    RecordedEvents,
    an_agent,
    start,
    tools_dir,
)
from tests.unit.scripted import Scripted
from tests.unit.stand_ins import Elsewhere, ElsewhereFiles

FIRST_LINE = '''
def first_line(path: str) -> str:
    """The first line of a file."""
    with open(path, encoding="utf-8") as handle:
        return handle.readline().strip()

TOOLS = [first_line]
'''


def _calls(**args: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": "first_line", "args": args, "id": "c1"}])


def _said(events) -> str:
    return next(e.text for e in events if e.kind == "tool_result" and e.tool == "first_line")


def _with_the_tool(cfg) -> None:
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "first_line.py").write_text(FIRST_LINE, encoding="utf-8")
    an_agent(cfg, **EVERY_TOOL)


# -- through a turn ----------------------------------------------------------


def test_a_skills_file_reaches_a_tool_where_the_backend_keeps_it(scripted):
    """The default backend reads `/skills` from the catalogue. The tool was handed
    `<session>/skills/...`, which does not exist, while `read_file` read the same path.
    """
    _with_the_tool(scripted)
    template = scripted.skills_dir / "report" / "template.md"
    template.parent.mkdir(parents=True, exist_ok=True)
    template.write_text("the template\n", encoding="utf-8")
    start(scripted, "s")
    Scripted.script.extend([_calls(path="/skills/report/template.md"), AIMessage("done")])

    events = list(
        Kingfisher(scripted, sessions=LocalSessions()).stream(
            Request("go", agent="only", session_id="s")
        )
    )

    assert _said(events) == "the template\n[A-0001]"


def test_a_tool_is_not_handed_the_turn_log(scripted):
    """A tool handed `/.harness/recent_turns.jsonl` as a real file reads, and could rewrite, the
    conversation kingfisher keeps, where the file tools are refused it.
    """
    if harness_unfenced(ShellSettings(), shell_confinement(scripted, ShellSettings())) is not None:
        pytest.skip("no sandbox kingfisher applies itself is available on this host")
    _with_the_tool(scripted)
    start(scripted, "s")
    kf = Kingfisher(scripted, sessions=LocalSessions())
    Scripted.script.append(AIMessage("hello"))
    list(kf.stream(Request("first", agent="only", session_id="s")))
    Scripted.script.extend([_calls(path="/.harness/recent_turns.jsonl"), AIMessage("done")])

    said = _said(list(kf.stream(Request("go", agent="only", session_id="s"))))

    kept = (scripted.workspace / "sessions" / "s" / ".harness" / "recent_turns.jsonl").read_text(
        encoding="utf-8"
    )
    assert kept.strip(), "the first turn wrote no turn log, so the refusal checks nothing"
    assert "permission denied for read on /.harness/recent_turns.jsonl" in said
    assert kept.splitlines()[0].strip() not in said


def test_a_backend_keeping_sessions_elsewhere_on_this_host_still_serves_a_path(
    scripted, tmp_path
):
    """Not remote, only elsewhere: every route is a directory this host reads, so a
    path tool works. A rule about *which* backend would have refused it.
    """
    _with_the_tool(scripted)
    source = tmp_path / "notes.txt"
    source.write_text("alpha\n")
    Scripted.script.extend([_calls(path="/inputs/notes.txt"), AIMessage("done")])
    kf = Kingfisher(scripted, sessions=Elsewhere(tmp_path / "remote"))

    events = list(kf.stream(Request("go", agent="only", inputs=(source,))))

    assert _said(events) == "alpha\n[A-0001]"


#: Where a deployment's mount keeps its sessions, which its own exception quotes.
MOUNT = "/mnt/tenant-store-8812"


class _Unmapped(ElsewhereFiles):
    def host_path(self, virtual: str) -> None:
        msg = f"the mount table lost {MOUNT}{virtual}"
        raise RuntimeError(msg)


class _UnmappedSessions(Elsewhere):
    def open(self, cfg, session_id, /, *, catalogue=None):
        opened = super().open(cfg, session_id, catalogue=catalogue)
        opened.__class__ = _Unmapped
        return opened


def test_a_host_path_that_raises_fails_the_call_and_not_the_turn(scripted, tmp_path):
    """A deployment's `host_path` raising ended the turn with its exception, whose message
    may name the host's own paths and must reach neither the model nor the log."""
    _with_the_tool(scripted)
    Scripted.script.extend([_calls(path="/inputs/notes.txt"), AIMessage("done")])
    sink = RecordedEvents()
    kf = Kingfisher(scripted, sessions=_UnmappedSessions(tmp_path / "remote"), run_events=sink)

    result = kf.run(Request("go", agent="only"))

    assert result.answer == "done"
    (told,) = [one for one in Scripted.heard[-1] if isinstance(one, ToolMessage)]
    assert told.status == "error"
    assert "the session's storage could not say where /inputs/notes.txt is kept" in told.text
    assert MOUNT not in told.text
    (warned,) = sink.named("host_path_failed")
    assert (warned["call"], warned["plugin"], warned["error_type"]) == (
        "c1",
        "_Unmapped",
        "RuntimeError",
    )
    assert MOUNT not in json.dumps(warned)


# -- the pieces --------------------------------------------------------------


class Remote(BackendProtocol):
    """What a backend running somewhere else looks like from here: nothing a host path
    could be read from.
    """


def _paths(backend, tmp_path, permissions=None) -> SessionPaths:
    rules = read_only_permissions(DEFAULT_LAYOUT) if permissions is None else permissions
    handed = PermittedBackend(backend, rules, DEFAULT_LAYOUT)
    return SessionPaths(handed, tmp_path / "sessions", DEFAULT_LAYOUT)


def test_a_backend_with_some_routes_here_serves_those_and_refuses_the_rest(tmp_path):
    """The `ports.md` shape: `/inputs` on a local directory, everything else remote.
    Asked per path, because the answer is different for two paths on one backend.
    """
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "in.csv").write_text("a,b\n")
    mixed = CompositeBackend(default=Remote(), routes={"/inputs/": FilesystemBackend(inputs)})
    paths = _paths(mixed, tmp_path)

    assert paths.real("/inputs/in.csv") == str((inputs / "in.csv").resolve())
    with pytest.raises(UnreachablePathError, match=r"not kept on this host.*ToolContext"):
        paths.real("/outputs/report.txt")


def test_a_backend_that_says_where_its_files_are_is_taken_at_its_word(tmp_path):
    """A network mount this host reads: the backend knows, and nothing else could."""
    mounted = tmp_path / "mnt" / "abc"

    class Mounted(Remote):
        def host_path(self, virtual):
            return mounted / virtual.lstrip("/")

    assert host_path(Mounted(), "/inputs/x.csv") == (None, mounted / "inputs" / "x.csv")


def test_a_rule_added_after_the_guards_were_built_still_applies(tmp_path):
    """`build_agent` builds the guards before it has finished adding rules -- memory
    declined, skills narrowed. A copy of the rules taken when they were built would
    hand a tool `/memory` on a turn that refused it.
    """
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "AGENTS.md").write_text("kept")
    rules = read_only_permissions(DEFAULT_LAYOUT)
    paths = _paths(FilesystemBackend(tmp_path), tmp_path, rules)

    rules.append(FilesystemPermission(operations=["read"], paths=["/memory/**"], mode="deny"))

    with pytest.raises(UnreachablePathError, match="permission denied"):
        paths.real("/memory/AGENTS.md")


def test_a_subagent_is_handed_the_parents_paths_on_any_backend(cfg, tmp_path):
    """It guessed its session from `backend.workspace`, which a deployment's backend
    need not have -- and then its tools lost translation and the host-path refusal
    with nothing said. `ElsewhereFiles` has no such attribute.
    """
    from tests.conftest import middleware_of

    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "first_line.py").write_text(FIRST_LINE, encoding="utf-8")
    (cfg.definitions / "subagents").mkdir(parents=True, exist_ok=True)
    (cfg.definitions / "subagents" / "reviewer.yaml").write_text(
        "name: reviewer\ndescription: d\nsystem_prompt: |\n  You review.\n", encoding="utf-8"
    )
    backend = ElsewhereFiles(tmp_path / "kept", cfg.definitions / "skills")
    assert not hasattr(backend, "workspace"), "the attribute is there; test proves nothing"

    built = build_agent(
        cfg,
        backend=backend,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=("reviewer",)),
    )

    def translating(stack):
        return [m for m in stack if type(m).__name__ == "GuardedCallsMiddleware"]

    (theirs,) = translating(middleware_of(built, "reviewer"))
    (mine,) = translating(built.middleware)
    assert theirs.paths is mine.paths


def test_a_route_named_without_its_slash_is_the_routed_folder(cfg, session_dir):
    """`/skills` misses the `/skills/` route by its prefix, and fell through to a folder of
    that name in the session. The real backend rather than a composite made here, so the
    route under test is the one the session mounts."""
    skills = cfg.catalogue_roots["skills"].resolve()

    found = host_path(backend_at(cfg, session_dir), "/skills")

    assert found is not None
    root, where = found
    assert where.resolve() == skills, where
    assert root is not None and root.resolve() == skills


def test_a_route_without_its_slash_does_not_take_a_longer_name(tmp_path):
    """A `/data` route took `/database/x` as its own `/base/x`, so a tool was handed a file
    under a folder the rules deny while they judged one beside it they allow."""
    (tmp_path / "kept" / "database").mkdir(parents=True)
    (tmp_path / "kept" / "database" / "x").write_text("allowed")
    (tmp_path / "denied" / "base").mkdir(parents=True)
    (tmp_path / "denied" / "base" / "x").write_text("denied")
    mixed = CompositeBackend(
        default=FilesystemBackend(tmp_path / "kept", virtual_mode=True),
        routes={"/data": FilesystemBackend(tmp_path / "denied", virtual_mode=True)},
    )
    rules = [FilesystemPermission(operations=["read"], paths=["/data/**"], mode="deny")]

    handed = _paths(mixed, tmp_path, rules).real("/database/x")

    assert handed == str((tmp_path / "kept" / "database" / "x").resolve())
