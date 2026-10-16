"""A tool that says who may use it: carried, read, and honoured wherever a grant is."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
import yaml
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tools import StructuredTool, tool

import kingfisher
from kingfisher import LocalSessions, backend_at
from kingfisher.application.inventory import inventory
from kingfisher.application.service import Kingfisher
from kingfisher.domain.access import AccessError, parse
from kingfisher.domain.capabilities import ALL
from kingfisher.infrastructure.harness.agent import build_agent
from kingfisher.infrastructure.harness.middlewares.recorder import Audiences
from kingfisher.kinds.tools.catalogue import CarriedTools, LocalToolRepository, ToolError
from kingfisher.kinds.tools.spec import own_audience
from kingfisher.plugins.sessions.folders import ensure_session_layout
from tests.conftest import tools_dir

#: `line_count` admits A by itself, and nothing in an agent file says so.
TOOL = '''
import kingfisher


def line_count(path: str) -> str:
    """Count the lines in a text file."""
    return "0"


TOOLS = [kingfisher.tool(line_count, source_ids=["A"])]
'''

#: Open to A and B, granting the tool by its bare name.
AGENT = """name: surveyor
description: An agent.
source_ids: [A, B]
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
    ensure_session_layout(session)
    assembled = build_agent(
        kf.cfg,
        agent=kf.agent_named("surveyor", source_ids=held),
        held=kf.held_for(held),
        backend=backend_at(kf.cfg, session, catalogue=kf.catalogue),
        capabilities=kf._effective_grants(held),
        catalogue=kf.catalogue,
        checkpointer=None,
    )
    return [getattr(t, "name", "") for t in assembled.tools or ()]


# -- what `kingfisher.tool` makes ------------------------------------------------


def test_the_tool_is_a_langchain_tool_whose_audience_is_not_in_its_metadata():
    """Kept in `metadata`, the audience would ride along into every callback."""
    made = kingfisher.tool(count, source_ids=["A", {"B", "Q"}])

    assert isinstance(made, StructuredTool)
    assert made.name == "count"
    assert made.metadata is None
    assert own_audience(made) == ("A", frozenset({"B", "Q"}))


def test_the_audience_never_reaches_a_callback():
    """A source id is who may reach data, and a trace is somewhere else entirely."""
    seen: list[Any] = []

    class Spy(BaseCallbackHandler):
        def on_tool_start(self, serialized: Any, input_str: str, **kwargs: Any) -> None:
            seen.append((serialized, input_str, kwargs))

    made = kingfisher.tool(count, source_ids=["payroll_zone"])
    made.invoke({"path": "x"}, config={"callbacks": [Spy()]})

    assert seen, "the spy saw no call, so it asserts nothing"
    assert "payroll_zone" not in repr(seen)


def test_a_tool_kingfisher_did_not_make_admits_whoever_its_agent_admits():
    assert own_audience(tool(count)) == ALL
    assert own_audience(count) == ALL


def test_an_audience_that_cannot_be_read_is_refused_naming_the_tool():
    """The reader a definition's own line goes through, so the two cannot drift."""
    with pytest.raises(ToolError, match=r"tool 'count'.*write \{a, b\}"):
        kingfisher.tool(count, source_ids=[["a", "b"]])


# -- read off the catalogue --------------------------------------------------------


def test_the_loader_copies_the_audience_into_the_catalogue(tmp_path):
    (tmp_path / "line_count.py").write_text(TOOL, encoding="utf-8")

    (found,) = LocalToolRepository(tmp_path).found

    assert found.source_ids == ("A",)


def test_a_tool_a_subagent_carries_keeps_its_audience_too():
    """The second loader: a portable declaration's tools arrive as objects, not files."""
    (found,) = CarriedTools(tools=(kingfisher.tool(count, source_ids=["A"]),), source="x").found

    assert found.source_ids == ("A",)


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


def test_an_agent_granting_every_tool_still_leaves_it_out(policied):
    """`tools:` omitted means every tool, which is a grant no per-name list ever saw --
    so this is the branch a filter over written names would miss.
    """
    agents = policied.catalogue_roots["agents"]
    (agents / "surveyor.yaml").write_text(
        AGENT.replace("tools: [line_count]\n", ""), encoding="utf-8"
    )
    kf = Kingfisher(policied, sessions=LocalSessions())

    assert "line_count" in built(kf, ("A",))
    assert "line_count" not in built(kf, ("B",))


def test_an_agents_entry_can_narrow_the_tools_audience_and_never_widen_it(policied):
    """`[B]` on the entry is an **and** with the tool's `[A]`, not a replacement."""
    agents = policied.catalogue_roots["agents"]
    (agents / "surveyor.yaml").write_text(
        AGENT.replace(
            "tools: [line_count]\n", "tools:\n  - name: line_count\n    source_ids: [B]\n"
        ),
        encoding="utf-8",
    )
    kf = Kingfisher(policied, sessions=LocalSessions())

    assert "line_count" not in built(kf, ("B",)), "the entry widened the tool"
    assert "line_count" not in built(kf, ("A",)), "the entry was ignored"
    assert "line_count" in built(kf, ("A", "B"))


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
        audiences: dict[str, Any] = {}

    stamps = Audiences.of(Definition(), own={"line_count": ("A",)})

    assert stamps.offered("line_count") == ("A",)
    assert stamps.offered("other") == ("A", "B"), "a tool saying nothing keeps the grant's"


#: A portable subagent carrying the tool itself, so it arrives as an object, not a file.
CARRIER = '''
import kingfisher


def count(path: str) -> str:
    """Count the lines in a text file."""
    return "0"


SUBAGENTS = [{
    "name": "counter",
    "description": "Counts lines.",
    "system_prompt": "You count.",
    "tools": [kingfisher.tool(count, source_ids=["A"])],
}]
'''


def test_a_subagents_own_tool_is_held_back_from_a_caller_it_shuts_out(policied):
    """A bundle skips the request's grant -- the subagent is made of its parts -- and the
    tool's own audience is the one thing that must not be skipped with it.
    """
    from kingfisher.infrastructure.harness.kinds.tools import _private_tools

    subagents = policied.catalogue_roots["subagents"]
    subagents.mkdir(parents=True, exist_ok=True)
    (subagents / "counter.py").write_text(CARRIER, encoding="utf-8")
    kf = Kingfisher(policied, sessions=LocalSessions())

    assert [one.name for one in _private_tools(kf.catalogue, "counter", held=frozenset({"A"}))] == [
        "count"
    ], "the control: A is admitted"
    assert _private_tools(kf.catalogue, "counter", held=frozenset({"B"})) == ()


def test_an_unrestricted_grant_still_gets_an_allowlist_without_the_tool():
    """An unrestricted request has no allowlist at all, so without one the only thing
    between a shut-out caller and the tool would be that nothing registered it.
    """
    from kingfisher.domain.capabilities import Capabilities
    from kingfisher.infrastructure.harness.kinds.tools import _ToolSurface
    from kingfisher.kinds.tools.spec import Found, Offering

    found = (
        Found(tool=kingfisher.tool(count, source_ids=["A"]), source="count.py", source_ids=("A",)),
    )
    surface = _ToolSurface(
        offering=Offering.of(found, builtin=("read_file",)),
        asked=Capabilities(),
        found=found,
        out_of_reach=frozenset({"count"}),
    )

    assert surface.permitted is not None
    assert "count" not in surface.permitted
    assert "read_file" in surface.permitted, "the built-ins went with it"


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
    from kingfisher.application.reporting import withheld_by_kind
    from kingfisher.domain.capabilities import Capabilities
    from kingfisher.domain.request import Request
    from kingfisher.infrastructure.steps import drive
    from tests.conftest import harness_in

    kf = Kingfisher(policied, sessions=LocalSessions())

    def report(held_names: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
        session = kf.workspace / "sessions" / f"report-{'-'.join(held_names)}"
        session.mkdir(parents=True, exist_ok=True)
        ensure_session_layout(session)
        held = kf.held_for(held_names)
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
