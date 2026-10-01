"""A workspace tool's `path`, resolved where the session's backend keeps it."""

from __future__ import annotations

import pytest
from deepagents import FilesystemPermission
from deepagents.backends import CompositeBackend, FilesystemBackend
from deepagents.backends.protocol import BackendProtocol
from langchain_core.messages import AIMessage

from kingfisher import Kingfisher, Request, default_backend
from kingfisher.domain.capabilities import Capabilities
from kingfisher.domain.references import UnsafeReferenceError
from kingfisher.infrastructure.harness.agent import build_agent, read_only_permissions
from kingfisher.infrastructure.harness.permitted_backend import PermittedBackend, host_path
from kingfisher.infrastructure.harness.session_paths import SessionPaths
from kingfisher.infrastructure.sandbox.confinement import harness_unfenced
from tests.conftest import FakeToolCallingModel, an_agent, start, tools_dir
from tests.unit.scripted import Scripted
from tests.unit.test_session_files import Elsewhere, ElsewhereFiles

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
    an_agent(cfg)


# -- through a turn ----------------------------------------------------------


def test_a_skills_file_reaches_a_tool_where_the_backend_keeps_it(scripted):
    """The default backend reads `/skills` from the catalogue. The tool was handed
    `<session>/skills/...`, which does not exist, while `read_file` read the same path.
    """
    _with_the_tool(scripted)
    template = scripted.workspace / "skills" / "report" / "template.md"
    template.parent.mkdir(parents=True, exist_ok=True)
    template.write_text("the template\n", encoding="utf-8")
    start(scripted, "s")
    Scripted.script.extend([_calls(path="/skills/report/template.md"), AIMessage("done")])

    events = list(
        Kingfisher(scripted, backend=default_backend).stream(
            Request("go", agent="only", session_id="s")
        )
    )

    assert _said(events) == "the template"


def test_the_pinned_agent_is_not_handed_to_a_tool(scripted):
    """Nothing signs the pin, so nothing would catch a tool that writes to its `path`.
    The file tools are refused `/.harness`, and a tool is refused it the same way now.
    """
    if harness_unfenced(scripted) is not None:
        pytest.skip("no sandbox kingfisher applies itself is available on this host")
    _with_the_tool(scripted)
    start(scripted, "s")
    Scripted.script.extend([_calls(path="/.harness/agent.yaml"), AIMessage("done")])

    events = list(
        Kingfisher(scripted, backend=default_backend).stream(
            Request("go", agent="only", session_id="s")
        )
    )

    said = _said(events)
    assert "permission denied for read on /.harness/agent.yaml" in said
    assert "name: only" not in said


def test_a_backend_keeping_sessions_elsewhere_on_this_host_still_serves_a_path(
    scripted, tmp_path
):
    """Not remote, only elsewhere: every route is a directory this host reads, so a
    path tool works. A rule about *which* backend would have refused it.
    """
    _with_the_tool(scripted)
    source = tmp_path / "notes.txt"
    source.write_text("alpha\n")
    Scripted.script.extend([_calls(path="/data/notes.txt"), AIMessage("done")])
    kf = Kingfisher(scripted, backend=Elsewhere(tmp_path / "remote"))

    events = list(kf.stream(Request("go", agent="only", data=(source,))))

    assert _said(events) == "alpha"


# -- the pieces --------------------------------------------------------------


class Remote(BackendProtocol):
    """What a backend running somewhere else looks like from here: nothing a host path
    could be read from.
    """


def _paths(backend, tmp_path, permissions=None) -> SessionPaths:
    rules = read_only_permissions() if permissions is None else permissions
    return SessionPaths(PermittedBackend(backend, rules), tmp_path / "sessions")


def test_a_backend_with_some_routes_here_serves_those_and_refuses_the_rest(tmp_path):
    """The `ports.md` shape: `/data` on a local directory, everything else remote.
    Asked per path, because the answer is different for two paths on one backend.
    """
    data = tmp_path / "data"
    data.mkdir()
    (data / "in.csv").write_text("a,b\n")
    mixed = CompositeBackend(default=Remote(), routes={"/data/": FilesystemBackend(data)})
    paths = _paths(mixed, tmp_path)

    assert paths.real("/data/in.csv") == str((data / "in.csv").resolve())
    with pytest.raises(UnsafeReferenceError, match=r"not kept on this host.*ToolContext"):
        paths.real("/derived/report.txt")


def test_a_backend_that_says_where_its_files_are_is_taken_at_its_word(tmp_path):
    """A network mount this host reads: the backend knows, and nothing else could."""
    mounted = tmp_path / "mnt" / "abc"

    class Mounted(Remote):
        def host_path(self, virtual):
            return mounted / virtual.lstrip("/")

    assert host_path(Mounted(), "/data/x.csv") == (None, mounted / "data" / "x.csv")


def test_a_rule_added_after_the_guards_were_built_still_applies(tmp_path):
    """`build_agent` builds the guards before it has finished adding rules -- memory
    declined, skills narrowed. A copy of the rules taken when they were built would
    hand a tool `/memory` on a turn that refused it.
    """
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "AGENTS.md").write_text("kept")
    rules = read_only_permissions()
    paths = _paths(FilesystemBackend(tmp_path), tmp_path, rules)

    rules.append(FilesystemPermission(operations=["read"], paths=["/memory/**"], mode="deny"))

    with pytest.raises(UnsafeReferenceError, match="permission denied"):
        paths.real("/memory/AGENTS.md")


def test_a_delegate_is_handed_the_parents_paths_on_any_backend(cfg, tmp_path):
    """It guessed its session from `backend.workspace`, which a deployment's backend
    need not have -- and then its tools lost translation and the host-path refusal
    with nothing said. `ElsewhereFiles` has no such attribute.
    """
    from tests.conftest import middleware_of

    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "first_line.py").write_text(FIRST_LINE, encoding="utf-8")
    (cfg.workspace / "subagents").mkdir(parents=True, exist_ok=True)
    (cfg.workspace / "subagents" / "reviewer.yaml").write_text(
        "name: reviewer\ndescription: d\nsystem_prompt: |\n  You review.\n", encoding="utf-8"
    )
    backend = ElsewhereFiles(tmp_path / "kept", cfg.workspace / "skills")
    assert not hasattr(backend, "workspace"), "the attribute is there; test proves nothing"

    built = build_agent(
        cfg,
        backend=backend,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=("reviewer",)),
    )

    def translating(stack):
        return [m for m in stack if type(m).__name__ == "WorkspaceToolPaths"]

    (theirs,) = translating(middleware_of(built, "reviewer"))
    (mine,) = translating(built.middleware)
    assert theirs.paths is mine.paths
