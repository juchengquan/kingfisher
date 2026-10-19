"""A tool that says who may use it: carried, read, and honoured wherever a grant is."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
import yaml
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tools import tool

from kingfisher import ToolSpec
from kingfisher.core.access.capabilities import ALL
from kingfisher.core.access.policy import parse
from kingfisher.core.access.source_ids import AccessError
from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.agent.middlewares.recorder import Audiences
from kingfisher.core.definitions.tools.catalogue import (
    CarriedTools,
    LocalToolRepository,
    ToolError,
    found_in,
)
from kingfisher.core.definitions.tools.spec import tool_name
from kingfisher.core.inventory import inventory
from kingfisher.core.layout import DEFAULT_LAYOUT
from kingfisher.core.service import Kingfisher
from kingfisher.plugins import LocalSessions, backend_at
from kingfisher.plugins.sessions.folders import ensure_session_layout
from tests.conftest import tools_dir

#: `line_count` admits A by itself, and nothing in an agent file says so.
TOOL = '''
from kingfisher import ToolSpec


def line_count(path: str) -> str:
    """Count the lines in a text file."""
    return "0"


TOOLS = [ToolSpec(line_count, source_ids=["A"])]
'''

#: The same lock on a `@tool` object and on a `BaseTool` instance.
OTHER_FORMS = {
    "decorated": '''
from langchain_core.tools import tool

from kingfisher import ToolSpec


@tool
def line_count(path: str) -> str:
    """Count the lines in a text file."""
    return "0"


TOOLS = [ToolSpec(line_count, source_ids=["A"])]
''',
    "class": '''
from langchain_core.tools import BaseTool

from kingfisher import ToolSpec


class LineCount(BaseTool):
    name: str = "line_count"
    description: str = "Count the lines in a text file."

    def _run(self, path: str) -> str:
        return "0"


TOOLS = [ToolSpec(LineCount(), source_ids=["A"])]
''',
}

#: Open to A and B, granting the tool by its bare name.
AGENT = """name: surveyor
description: An agent.
source_ids: [A, B]
builtin_tools: ["*"]
tools: [line_count]
system_prompt: |
  You do the task.
"""


def count(path: str) -> str:
    """Count the lines in a text file."""
    return "0"


@pytest.fixture
def policied(cfg):
    """A deployment where `line_count` admits A and its agent admits A and B."""
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "line_count.py").write_text(TOOL, encoding="utf-8")
    agents = cfg.catalogue_roots["agents"]
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "surveyor.yaml").write_text(AGENT, encoding="utf-8")
    vocabulary = parse(yaml.safe_load("source_ids: [A, B, Q]\n"), source="source_ids.yaml")
    return replace(cfg, access=vocabulary)


def built(kf: Kingfisher, held: tuple[str, ...]) -> list[str]:
    """The workspace tools handed to `create_deep_agent` for this caller."""
    session = kf.workspace / "sessions" / "-".join(held)
    session.mkdir(parents=True, exist_ok=True)
    ensure_session_layout(session, DEFAULT_LAYOUT)
    assembled = build_agent(
        kf.cfg,
        agent=kf._agent_named("surveyor", source_ids=held),
        held=kf._held_for(held),
        backend=backend_at(kf.cfg, session, catalogue=kf.catalogue),
        capabilities=kf._effective_grants(held),
        catalogue=kf.catalogue,
        checkpointer=None,
    )
    return [tool_name(t) for t in assembled.tools or ()]


# -- what the loader makes of a `ToolSpec` ---------------------------------------------


def found(entry: Any) -> Any:
    """One `TOOLS` entry as the loader records it."""
    return found_in(entry, where="count.py", holder="TOOLS")


def test_the_audience_is_on_the_record_and_never_on_the_tool():
    """Kept on the tool, in `metadata` or beside it, the audience would ride along into
    every callback.
    """
    decorated = tool(count)
    one = found(ToolSpec(decorated, source_ids=["A", {"B", "Q"}]))

    assert one.tool is decorated
    assert one.source_ids == ("A", frozenset({"B", "Q"}))
    assert decorated.metadata is None
    assert "Q" not in repr(vars(decorated))


def test_the_audience_never_reaches_a_callback():
    """A source id is who may reach data, and a trace is somewhere else entirely. Renamed,
    so the tool that runs is the one the loader made rather than the author's.
    """
    seen: list[Any] = []

    class Spy(BaseCallbackHandler):
        def on_tool_start(self, serialized: Any, input_str: str, **kwargs: Any) -> None:
            seen.append((serialized, input_str, kwargs))

    made = found(ToolSpec(count, name="tally", source_ids=["payroll_zone"])).tool
    made.invoke({"path": "x"}, config={"callbacks": [Spy()]})

    assert seen, "the spy saw no call, so it asserts nothing"
    assert "payroll_zone" not in repr(seen)


@pytest.mark.parametrize("entry", [count, tool(count), ToolSpec(count, source_ids=["*"])],
                         ids=["plain", "decorated", "star"])
def test_a_bare_tool_admits_whoever_its_agent_admits(entry):
    """A bare entry read as shut to someone would close every tool written before
    `ToolSpec` existed the moment `source_ids.yaml` appeared.
    """
    one = found(entry)

    assert one.source_ids == ALL
    assert one.datazone is None


def test_an_audience_that_cannot_be_read_is_refused_naming_the_file(tmp_path):
    """The reader a definition's own line goes through, so the two cannot drift."""
    (tmp_path / "count.py").write_text(
        TOOL.replace('source_ids=["A"]', 'source_ids=[["a", "b"]]'), encoding="utf-8"
    )

    with pytest.raises(ToolError, match=r"^count\.py: tool 'line_count'.*write \{a, b\}"):
        _ = LocalToolRepository(tmp_path).found


def test_an_empty_audience_is_refused_rather_than_read_as_nobody():
    """`[]` read as nobody is a tool nobody can use and nothing reports, the way YAML
    refuses `source_ids: []` on a definition.
    """
    with pytest.raises(ToolError, match=r"count\.py: tool 'count'.*empty, which would mean nobody"):
        found(ToolSpec(count, source_ids=[]))


# -- read off the catalogue --------------------------------------------------------


def test_the_loader_copies_the_audience_into_the_catalogue(tmp_path):
    (tmp_path / "line_count.py").write_text(TOOL, encoding="utf-8")

    (one,) = LocalToolRepository(tmp_path).found

    assert one.source_ids == ("A",)


def test_a_tool_a_subagent_carries_keeps_its_audience_too():
    """The second loader: a portable declaration's tools arrive as objects, not files."""
    (one,) = CarriedTools(tools=(ToolSpec(count, source_ids=["A"]),), source="x").found

    assert one.source_ids == ("A",)


# -- honoured wherever a grant is ---------------------------------------------------


def test_a_caller_the_tool_shuts_out_does_not_get_it_from_an_agent_that_grants_it(
    policied,
):
    """Driven through the real builder: B opens `surveyor`, which names the tool, and
    the tool itself admits only A.
    """
    kf = Kingfisher(policied, sessions=LocalSessions())

    assert "line_count" in built(kf, ("A",)), "the control: A is admitted"
    assert "line_count" not in built(kf, ("B",))


@pytest.mark.parametrize("form", sorted(OTHER_FORMS))
def test_a_lock_on_a_decorated_or_class_tool_holds_like_one_on_a_function(policied, form):
    """`kingfisher.tool` took only a plain function, so a `@tool` object or a `BaseTool`
    instance had no way to say who may use it and reached every caller its agent did.
    """
    (tools_dir(policied) / "line_count.py").write_text(OTHER_FORMS[form], encoding="utf-8")
    kf = Kingfisher(policied, sessions=LocalSessions())

    assert "line_count" in built(kf, ("A",)), "the control: A is admitted"
    assert "line_count" not in built(kf, ("B",))


def test_an_agent_granting_every_tool_still_leaves_it_out(policied):
    """`tools: ["*"]` is every tool, which is a grant no per-name list ever saw -- so
    this is the branch a filter over written names would miss.
    """
    agents = policied.catalogue_roots["agents"]
    (agents / "surveyor.yaml").write_text(
        AGENT.replace("tools: [line_count]\n", 'tools: ["*"]\n'), encoding="utf-8"
    )
    kf = Kingfisher(policied, sessions=LocalSessions())

    assert "line_count" in built(kf, ("A",))
    assert "line_count" not in built(kf, ("B",))


def test_the_listing_agrees_with_the_build(policied):
    """A listing naming a tool the build withholds tells a caller it exists."""
    assert "line_count" in inventory(policied, source_ids=["A"]).tools
    assert "line_count" not in inventory(policied, source_ids=["B"]).tools


def test_a_tool_naming_an_undeclared_source_id_stops_the_deployment(policied):
    """The closed vocabulary, for the one kind that never had a line to check."""
    (tools_dir(policied) / "line_count.py").write_text(
        TOOL.replace('source_ids=["A"]', 'source_ids=["sales_bd"]'), encoding="utf-8"
    )

    with pytest.raises(AccessError, match=r"line_count\.py::line_count.*'sales_bd'"):
        Kingfisher(policied, sessions=LocalSessions())


def test_a_saved_result_is_stamped_no_wider_than_its_tool():
    """Stamped at what the agent offered, the ledger recorded a result as readable by
    callers the tool itself shuts out.
    """

    class Definition:
        source_ids = ("A", "B")

    stamps = Audiences.of(Definition(), own={"line_count": ("A",), "export": ("C",)})

    assert stamps.offered("line_count") == ("A",)
    assert stamps.offered("other") == ("A", "B"), "a tool saying nothing keeps the grant's"
    # Joined with the definition's own line rather than replacing it: the result was
    # produced by whoever opened the agent *and* passed the tool's lock.
    assert stamps.offered("export") == (frozenset({"A", "C"}), frozenset({"B", "C"}))


#: A portable subagent carrying the tool itself, so it arrives as an object, not a file.
CARRIER = '''
from kingfisher import ToolSpec


def count(path: str) -> str:
    """Count the lines in a text file."""
    return "0"


SUBAGENTS = [{
    "name": "counter",
    "description": "Counts lines.",
    "system_prompt": "You count.",
    "tools": [ToolSpec(count, source_ids=["A"])],
}]
'''


def test_a_subagents_own_tool_is_held_back_from_a_caller_it_shuts_out(policied):
    """A bundle skips the request's grant -- the subagent is made of its parts -- and the
    tool's own audience is the one thing that must not be skipped with it.
    """
    from kingfisher.core.agent.kinds.tools import _private_tools

    subagents = policied.catalogue_roots["subagents"]
    subagents.mkdir(parents=True, exist_ok=True)
    (subagents / "counter.py").write_text(CARRIER, encoding="utf-8")
    kf = Kingfisher(policied, sessions=LocalSessions())

    assert [one.name for one in _private_tools(kf.catalogue, "counter", held=frozenset({"A"}))] == [
        "count"
    ], "the control: A is admitted"
    assert _private_tools(kf.catalogue, "counter", held=frozenset({"B"})) == ()


def test_the_operator_listing_says_who_each_tool_admits(policied):
    """No agent file says it, so the operator's audit view named neither tool."""
    from kingfisher.cli.listing import as_json, render

    subagents = policied.catalogue_roots["subagents"]
    subagents.mkdir(parents=True, exist_ok=True)
    (subagents / "counter.py").write_text(CARRIER, encoding="utf-8")
    found = inventory(policied)
    shown = "\n".join(render(found))

    assert "\n  tool line_count  [A]\n" in shown
    assert "\n  tool counter::count  [A]\n" in shown, "a subagent's own, named for it"
    assert as_json(found)["audiences"]["tools"] == {
        "line_count": {"source_ids": ["A"]},
        "counter::count": {"source_ids": ["A"]},
    }


def test_an_unrestricted_grant_still_gets_an_allowlist_without_the_tool():
    """An unrestricted request has no allowlist at all, so without one the only thing
    between a shut-out caller and the tool would be that nothing registered it.
    """
    from kingfisher.core.access.capabilities import Capabilities
    from kingfisher.core.agent.kinds.tools import _ToolSurface
    from kingfisher.core.definitions.tools.spec import FoundTool, Offering

    found = (FoundTool(tool=count, source="count.py", source_ids=("A",)),)
    surface = _ToolSurface(
        offering=Offering.of(found, builtin=("read_file",)),
        asked=Capabilities(),
        found=found,
        out_of_reach=frozenset({"count"}),
    )

    assert surface.permitted is not None
    assert "count" not in surface.permitted
    assert "read_file" in surface.permitted, "the built-ins went with it"


@pytest.mark.parametrize("tool", ["count", "line_count"], ids=["own", "shadowing"])
def test_a_callers_listing_names_no_own_tool_the_build_holds_back(policied, tool):
    """The build left the tool out of `counter` for B, and B's listing still printed it
    under `counter` as one their run would hold. A tool shadowing the catalogue's is
    printed by a line of its own, so it is held back by a filter of its own.
    """
    from kingfisher.cli.listing import render

    subagents = policied.catalogue_roots["subagents"]
    subagents.mkdir(parents=True, exist_ok=True)
    (subagents / "counter.py").write_text(
        CARRIER.replace("def count(", f"def {tool}(").replace("Spec(count,", f"Spec({tool},"),
        encoding="utf-8",
    )
    (policied.catalogue_roots["agents"] / "surveyor.yaml").write_text(
        AGENT + "subagents: [counter]\n", encoding="utf-8"
    )
    a = inventory(policied, source_ids=["A"])
    b = inventory(policied, source_ids=["B"])

    assert f"{tool}  [private tool" in "\n".join(render(a)), "the control: A holds it"
    assert "counter" in b.subagents, "B still reaches the subagent"
    assert "counter" not in b.bundled_tools
    assert "counter" not in b.shadowed
    assert f"{tool}  [private tool" not in "\n".join(render(b))


def test_a_moved_tool_the_caller_cannot_reach_is_not_named_to_them(policied):
    """The listing's moved-tool rows read the agent's half only, and named a tool to a
    caller whose every other view of the deployment withholds it.
    """
    agents = policied.catalogue_roots["agents"]
    (agents / "surveyor.yaml").write_text(
        AGENT.replace("tools: [line_count]", "tools: [elsewhere.py::line_count]"),
        encoding="utf-8",
    )

    assert inventory(policied, source_ids=["A"]).moved_tools, "the control: A is told"
    assert not inventory(policied, source_ids=["B"]).moved_tools


def test_the_run_report_agrees_with_the_build(policied):
    """The third reader of what a caller reaches. A report naming a tool as withheld
    tells the caller it exists, which is what leaving it out of the build was for.
    """
    from kingfisher.core.access.capabilities import Capabilities
    from kingfisher.core.reporting import withheld_by_kind
    from kingfisher.core.request import Request
    from kingfisher.core.session.steps import drive
    from tests.conftest import harness_in

    kf = Kingfisher(policied, sessions=LocalSessions())

    def report(held_names: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
        session = kf.workspace / "sessions" / f"report-{'-'.join(held_names)}"
        session.mkdir(parents=True, exist_ok=True)
        ensure_session_layout(session, DEFAULT_LAYOUT)
        held = kf._held_for(held_names)
        request = Request(task="t", agent="surveyor")
        agent = drive(kf._agent_for(request, harness_in(kf.cfg, session), source_ids=held_names))
        graph = kf._graph_for(
            request,
            capabilities=kf._effective_grants(held_names),
            checkpointer=None,
            agent=agent,
            held=held,
            files=drive(kf._files_for(session.name)),
        ).graph
        # A request declining every tool, or the report skips the axis and says nothing.
        allowed = kf._effective_grants(held_names).intersect(Capabilities(tools=()))
        return dict(withheld_by_kind(allowed, kf.cfg, graph, kf.catalogue, agent=agent, held=held))

    assert "line_count" in report(("A",)).get("tool", ()), "the control: A is told"
    assert "line_count" not in report(("B",)).get("tool", ())
