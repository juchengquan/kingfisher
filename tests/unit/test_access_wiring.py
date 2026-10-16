"""The definitions' audiences reaching a run: who is calling, and what is built."""

from __future__ import annotations

import ast
import json
import re
from dataclasses import replace

import pytest
import yaml
from deepagents.middleware import SubAgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage

from kingfisher import LocalSessions, backend_at
from kingfisher.application.service import Kingfisher
from kingfisher.core.access.capabilities import Capabilities, CapabilityError
from kingfisher.core.access.source_ids import UNSCOPED, AccessError, parse
from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.agent.middlewares.narrowing import ToolAllowlist
from kingfisher.domain.request import Request
from kingfisher.infrastructure.steps import drive
from kingfisher.plugins.sessions.folders import ensure_session_layout
from kingfisher.presentation.cli.__main__ import main
from tests.conftest import (
    an_agent,
    declared_subagents,
    dispatched,
    harness_in,
    middleware_of,
    subagent_of,
    subagents_dir,
    tools_dir,
)
from tests.unit.scripted import Scripted

TOOL = '''
def line_count(path: str) -> str:
    """Count the lines in a text file."""
    return "0"


TOOLS = [line_count]
'''

VOCABULARY = "source_ids: [A, B]\n"

#: `surveyor` is for A and B; its one tool is for A alone. So a caller holding B
#: reaches the agent and runs it with nothing -- the compounding case, in one
#: file.
AGENT = """name: surveyor
description: An agent.
source_ids: [A, B]
tools:
  - name: line_count
    source_ids: [A]
system_prompt: |
  You do the task.
"""


def vocabulary(text: str = VOCABULARY):
    return parse(yaml.safe_load(text), source="source_ids.yaml")


@pytest.fixture
def policied(cfg):
    """A deployment where source id A reaches `line_count` through `surveyor`."""
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "line_count.py").write_text(TOOL, encoding="utf-8")
    directory = cfg.catalogue_roots["agents"]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "surveyor.yaml").write_text(AGENT, encoding="utf-8")
    return replace(cfg, access=vocabulary())


def session_at(kf, name: str):
    session = kf.workspace / "sessions" / name
    session.mkdir(parents=True, exist_ok=True)
    ensure_session_layout(session)
    return session


def built(kf, source_ids, name: str):
    """The tool names handed to `create_deep_agent` for this caller.

    `build_agent` directly rather than through `_graph_for`, because the record is
    the harness's own and the service hands back the graph alone -- there being no
    record for the pre-built graph its other branch returns. Nothing is lost here
    that this helper had: it already supplied its own session and resolved the
    grant itself, so what the service adds around that is not what these assert on.
    """
    held = tuple(source_ids) if source_ids is not UNSCOPED else source_ids
    session = session_at(kf, name)
    assembled = build_agent(
        kf.cfg,
        agent=kf.agent_named("surveyor", source_ids=held),
        held=kf.held_for(held),
        backend=backend_at(kf.cfg, session, catalogue=kf.catalogue),
        capabilities=kf._effective_grants(held),
        catalogue=kf.catalogue,
        checkpointer=None,
    )
    return [getattr(t, "name", getattr(t, "__name__", "")) for t in assembled.tools or ()]


# -- who is calling ---------------------------------------------------------


def test_a_call_that_does_not_say_who_is_calling_is_refused(policied):
    """The dangerous failure is a handler that forgot the boundary, so it is made loud
    rather than left to grant everything in silence.
    """
    kf = Kingfisher(policied, sessions=LocalSessions())
    with pytest.raises(AccessError, match="source_ids="):
        kf.run("anything")


def test_unscoped_runs_without_a_caller_and_says_so_at_the_call(policied):
    """The opt-out is a value someone typed, so a review can find it."""
    kf = Kingfisher(policied, sessions=LocalSessions())
    assert kf.held_for(UNSCOPED) is None


def test_an_unknown_source_id_is_refused(policied):
    """The closed vocabulary, from the caller's end: a typo would otherwise reach
    nothing, which looks exactly like a caller who was denied.
    """
    kf = Kingfisher(policied, sessions=LocalSessions())
    with pytest.raises(AccessError, match="unknown source id"):
        kf.held_for(("Q",))


def test_naming_source_ids_where_there_is_no_vocabulary_is_refused(cfg):
    """A caller naming source ids against a deployment that declares none is confused, and
    silently ignoring them is how they stay confused.

    Asked of `held_for` rather than of `_effective_grants`, because the refusal moved
    into the door both of them share: a turn came through here and the listing did not,
    so `kingfisher list --as marketing` printed the whole catalogue and exited zero on
    the deployment a turn was refused against. It is the state `decisions.md` describes
    over HTTP -- "a server somebody believes is locked down and is not" -- refused there
    at startup since 2026-09-01.
    """
    kf = Kingfisher(cfg, sessions=LocalSessions())
    with pytest.raises(AccessError, match="declares no source ids"):
        kf.held_for(("A",))
    # And still refused at the door a turn comes through, which is what moved.
    with pytest.raises(AccessError, match="declares no source ids"):
        kf._effective_grants(("A",))


def test_a_list_of_source_ids_narrows_exactly_as_a_tuple_does(policied):
    """The trap that folding `for_groups` in had to disarm."""
    kf = Kingfisher(policied, sessions=LocalSessions())

    assert kf.held_for(["A"]) == kf.held_for(("A",))
    assert kf.held_for(["A"]) is not None, "a list read as no opinion at all"


def test_a_bare_string_of_source_ids_is_refused_rather_than_spelled_out(policied):
    """`source_ids="sales_db"` is iterable, so coercing it yields eight one-letter source id
    names.
    """
    kf = Kingfisher(policied, sessions=LocalSessions())

    with pytest.raises(AccessError, match="not a string"):
        kf.held_for("A")


def test_the_refusal_names_what_was_asked_for_and_what_to_do(cfg):
    """A refusal somebody has to go and research is one this file keeps refusing to
    ship: it names the ids that narrow nothing, the file that would declare them, and
    the value that means no caller.
    """
    kf = Kingfisher(cfg, sessions=LocalSessions())

    with pytest.raises(AccessError) as caught:
        kf.held_for(["marketing", "sales"])

    said = str(caught.value)
    assert "marketing, sales" in said
    assert "source_ids.yaml" in said and "UNSCOPED" in said


def test_meaning_no_caller_is_still_how_you_say_it(cfg):
    """`UNSCOPED` is the one value that says the absence is meant, and a deployment with
    no policy is where saying it is most ordinary -- so it was the one that refused it.

    `_effective_grants` refused on `source_ids is not None`, and `UNSCOPED` is not
    `None`: `kingfisher run --as UNSCOPED` could not run a workspace with no
    `source_ids.yaml`, which is every workspace that has not adopted a policy. Driven
    through the turn's own door, because that is the one that refused.
    """
    kf = Kingfisher(cfg, sessions=LocalSessions())

    assert kf.held_for(UNSCOPED) is None
    assert kf.held_for(None) is None
    # An empty sequence too: it names no caller to be wrong about, which is a
    # different thing from naming one the deployment cannot honour.
    assert kf.held_for([]) is None
    assert kf._effective_grants(UNSCOPED) is not None
    assert kf._effective_grants(None) is not None


def test_a_declared_caller_is_still_expanded(cfg):
    """The other direction: a rule refusing on the names rather than on the absence of
    a vocabulary would pass every test above and break every policied deployment.
    """
    kf = Kingfisher(replace(cfg, access=vocabulary()), sessions=LocalSessions())

    assert kf.held_for(["A"]) is not None


def test_one_function_reads_what_shape_a_caller_s_source_ids_are_in():
    """`agent_named` and `session()` each asked "is it a tuple?" while `held_for` took
    any sequence, so `["B"]` opened an agent restricted to A -- one value, read three
    ways. Walked from the source, so a fourth reader fails here the day it is written.
    """
    import ast

    from tests.conftest import repository_root

    src = repository_root() / "src" / "kingfisher"
    readers = set()
    for path in sorted([*(src / "application").glob("*.py"), src / "core/access/checks.py"]):
        for function in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(function):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "isinstance"
                    and node.args
                    and isinstance(node.args[0], ast.Name)
                    and node.args[0].id == "source_ids"
                ):
                    readers.add(f"{path.stem}.{function.name}")

    assert readers == {"checks.held_by"}, (
        f"the shape of source_ids is read in {sorted(readers)} -- ask "
        "`core.access.checks.held_by` instead, or one reader will disagree with another"
    )


def test_a_deployment_without_a_vocabulary_is_unchanged(cfg):
    """Everything that worked before this must still work untouched -- including calling
    `run` without saying anything about source ids.
    """
    kf = Kingfisher(cfg, sessions=LocalSessions())
    assert kf.access is None
    assert kf.held_for(None) is None


def test_resolving_the_same_source_ids_twice_gives_the_same_grant(policied):
    """It was a handle that could be bound once and reused; now the resolution happens
    per call, so the thing worth asserting is that it is stable.
    """
    kf = Kingfisher(policied, sessions=LocalSessions())
    assert kf._effective_grants(("A",)) == kf._effective_grants(("A",))


def test_the_deployments_own_grants_still_bound_a_caller(policied):
    """Two ceilings, and the lower one wins."""
    kf = Kingfisher(replace(policied, grants=Capabilities(tools=())), sessions=LocalSessions())
    assert kf._effective_grants(("A",)).tools == ()


# -- what the graph is built from -------------------------------------------


def test_a_caller_the_audience_admits_gets_the_tool(policied):
    assert "line_count" in built(policied_kf(policied), ["A"], "s1")


def test_a_caller_the_audience_excludes_does_not(policied):
    """Not filtered after the fact -- never attached."""
    assert "line_count" not in built(policied_kf(policied), ["B"], "s2")


def test_unscoped_still_gets_everything(policied):
    """No caller means no narrowing, which is what keeps `declares(None)` the exact
    answer it was before audiences existed.
    """
    assert "line_count" in built(policied_kf(policied), UNSCOPED, "s3")


def policied_kf(cfg):
    return Kingfisher(cfg, sessions=LocalSessions())


# -- the report -------------------------------------------------------------


def test_a_definition_with_no_source_ids_line_is_named(cfg):
    """Default-open must not also be silent."""
    an_agent(cfg, "assistant")
    kf = Kingfisher(replace(cfg, access=vocabulary()), sessions=LocalSessions())
    assert ("agent", "assistant") in kf.access_report.unrestricted


def test_a_subagent_with_no_source_ids_line_is_named_too(cfg):
    """Both kinds, asserted rather than assumed.

    Mutation testing found this: the walk could stop looking at subagents entirely
    and the whole suite stayed green, so a subagent reachable by everyone would have
    gone unreported while the agent beside it was named. The report's whole job is
    that default-open is not silent, and it was silent for half the definitions it
    covers.
    """
    an_agent(cfg, "assistant")
    subagents = cfg.catalogue_roots["subagents"]
    subagents.mkdir(parents=True, exist_ok=True)
    (subagents / "auditor.yaml").write_text(
        "name: auditor\ndescription: A subagent.\nsystem_prompt: |\n  Do it.\n",
        encoding="utf-8",
    )

    kf = Kingfisher(replace(cfg, access=vocabulary()), sessions=LocalSessions())

    assert ("subagent", "auditor") in kf.access_report.unrestricted
    assert "auditor" in "\n".join(kf.access_report.lines())


def test_a_definition_that_restricts_is_not_named(policied):
    kf = Kingfisher(policied, sessions=LocalSessions())
    assert kf.access_report.is_clean


def test_the_report_reads_as_a_sentence(cfg):
    an_agent(cfg, "assistant")
    kf = Kingfisher(replace(cfg, access=vocabulary()), sessions=LocalSessions())
    rendered = "\n".join(kf.access_report.lines())
    assert "reachable by everyone" in rendered
    assert "assistant" in rendered


# -- what a caller is told --------------------------------------------------


def reported(kf, source_ids, name: str, *, asked_for: Capabilities | None = None):
    """The withheld report a caller holding these source ids is handed for one turn.

    `asked_for` is what the request itself declined, intersected as the service
    intersects it. It matters more than it looks: `withheld_by_kind` skips an axis the
    request left at its default, so without one every axis is skipped and the report is
    empty -- which is how a rule asserting a name was absent from it asserted nothing.
    """
    from kingfisher.application.reporting import withheld_by_kind

    session = session_at(kf, name)
    held_names = tuple(source_ids) if source_ids is not UNSCOPED else source_ids
    held = kf.held_for(held_names)
    request = Request(task="t", agent="surveyor")
    allowed = kf._effective_grants(held_names)
    if asked_for is not None:
        allowed = allowed.intersect(asked_for)
    # Once, and handed to both, which is what a turn does: the build and the report
    # are the two readers of it, and resolving separately for each is what this
    # stopped doing.
    agent = drive(kf._agent_for(request, harness_in(kf.cfg, session), source_ids=held_names))
    graph = kf._graph_for(
        request,
        capabilities=kf._effective_grants(held_names),
        checkpointer=None,
        agent=agent,
        held=held,
        files=drive(kf._files_for(session.name)),
    ).graph
    return withheld_by_kind(
        allowed,
        kf.cfg,
        graph,
        kf.catalogue,
        agent=agent,
        held=held,
    )


#: A workspace tool no definition here declares at all. `surveyor` names `line_count`
#: and nothing else, so nobody reaches this one through any agent.
UNDECLARED_TOOL = '''
from langchain_core.tools import tool


@tool
def secret_export(path: str) -> str:
    """Exports."""
    return "0"


TOOLS = [secret_export]
'''


#: One a subagent declares and no agent grants. `ceiling` narrows a subagent's tools by
#: what reached it, so this is a name no path reaches -- and there is no such thing as a
#: *workspace* tool only a subagent can hold. A tool of its own, in its own folder, is
#: the shape that exists for that, and it is listed as a private tool.
SUBAGENT_ONLY_TOOL = UNDECLARED_TOOL.replace("secret_export", "delegated_only").replace(
    "Exports.", "Delegates."
)

HELPER = """name: helper
description: A subagent.
source_ids: [A, B]
tools: [delegated_only]
system_prompt: |
  You help.
"""


@pytest.fixture
def policied_with_a_stranger(policied):
    """`policied`, plus a workspace tool no definition offers anyone, and one offered
    only by a subagent in reach.
    """
    (tools_dir(policied) / "secret_export.py").write_text(UNDECLARED_TOOL, encoding="utf-8")
    (tools_dir(policied) / "delegated_only.py").write_text(
        SUBAGENT_ONLY_TOOL, encoding="utf-8"
    )
    helpers = subagents_dir(policied)
    helpers.mkdir(parents=True, exist_ok=True)
    (helpers / "helper.yaml").write_text(HELPER, encoding="utf-8")
    return policied


def test_a_caller_is_told_only_of_what_a_definition_they_reach_offers(policied_with_a_stranger):
    """The report named every offered thing a grant left out, measured against the whole
    catalogue -- so a caller who narrowed their own grant was handed the name of a tool
    no definition they reach offers, which is the one thing filtering these out of
    listings and refusals exists to prevent. The caller supplies `capabilities`, so
    narrowing their own grant was the whole of what it took.

    A tool has nowhere to write an audience of its own, so what can be asked of one is
    whether an agent in reach offers it -- the question the listing asks, now through
    the same function. The reachable tool is named beside it, because a filter that
    emptied the report would pass the first assertion on its own.
    """
    kf = Kingfisher(policied_with_a_stranger, sessions=LocalSessions())
    declined = Capabilities(tools=())

    told = reported(kf, ["A"], "w1", asked_for=declined)

    (what, names) = told[0]
    assert len(told) == 1 and what == "tool"
    # `delegated_only` is *not* here, and that is the fix rather than an omission: a
    # subagent can only narrow what reached it, so a tool it declares and no agent
    # grants is one this caller gets by no path. Counting it named an asset out of
    # reach, which is the one thing this report exists not to do.
    assert set(names) == {"line_count"}, (
        "the report named a tool nothing here offers, or stopped naming one that is"
    )


A_PLAIN_SUBAGENT = """name: {name}
description: A subagent.
source_ids: [A, B]
system_prompt: |
  You help.
"""


@pytest.fixture
def policied_with_one_helper(policied):
    """`policied`, where `surveyor` hands work to `helper` and nothing names the other.

    Both subagents' own lines admit this caller, which is the state that matters: the
    difference between them is only whether an agent in reach names them.
    """
    directory = policied.catalogue_roots["agents"]
    (directory / "surveyor.yaml").write_text(AGENT + "subagents: [helper]\n", encoding="utf-8")
    helpers = subagents_dir(policied)
    helpers.mkdir(parents=True, exist_ok=True)
    for name in ("helper", "kept_apart"):
        (helpers / f"{name}.yaml").write_text(
            A_PLAIN_SUBAGENT.format(name=name), encoding="utf-8"
        )
    return policied


def test_a_subagent_no_agent_in_reach_activates_is_not_reported_as_withheld(
    policied_with_one_helper,
):
    """Naming one says it exists, which is what this report is filtered for -- and its
    own `source_ids:` admitting the caller is not the same question as whether any
    agent they can open hands it work. `activated` records that a subagent reaches a
    run only where the agent names it.

    `helper` is asserted beside it, so a filter that emptied the row would not pass.
    """
    kf = Kingfisher(policied_with_one_helper, sessions=LocalSessions())

    told = dict(reported(kf, ["A"], "w2", asked_for=Capabilities(subagents=())))

    assert set(told["subagent"]) == {"helper"}


#: A subagent `surveyor` hands work to, naming a tool by a name nothing offers. The
#: typo is the file's fault whoever is asking -- which is why the refusal still fires
#: for every caller -- and what it *lists* is the question here.
A_TYPO_IN_A_SUBAGENT = """name: helper
description: A subagent.
source_ids: [A, B]
tools: [line_counts]
system_prompt: |
  You help.
"""


def _refused_listing(cfg, source_ids):
    """What one real build's refusal of `line_counts` lists to this caller."""
    kf = Kingfisher(cfg, sessions=LocalSessions())
    session = session_at(kf, "w-refused")
    with pytest.raises(CapabilityError, match=r"unknown tool\(s\): line_counts;") as refused:
        build_agent(
            kf.cfg,
            agent=kf.agent_named("surveyor", source_ids=source_ids),
            held=kf.held_for(source_ids),
            backend=backend_at(kf.cfg, session, catalogue=kf.catalogue),
            capabilities=kf._effective_grants(source_ids),
            catalogue=kf.catalogue,
            checkpointer=None,
        )
    listing = str(refused.value).split("; offered: ", 1)[1]
    # One name a line, with its file beside it: the first word of each line that has
    # one. Stripped before splitting, or the indent is what comes back.
    return {one.strip().split()[0] for one in listing.splitlines() if one.strip()}


@pytest.fixture
def policied_with_a_typo(policied_with_a_stranger):
    """`policied_with_a_stranger`, where `surveyor` hands work to a subagent whose
    `tools:` holds a typo. Three tools exist; the caller reaches one.
    """
    directory = policied_with_a_stranger.catalogue_roots["agents"]
    (directory / "surveyor.yaml").write_text(AGENT + "subagents: [helper]\n", encoding="utf-8")
    (subagents_dir(policied_with_a_stranger) / "helper.yaml").write_text(
        A_TYPO_IN_A_SUBAGENT, encoding="utf-8"
    )
    return policied_with_a_stranger


SURVEYOR_NAMING_A_GHOST = AGENT + "subagents: [helper, ghost]\n"

#: An agent this caller cannot open, which is the only thing naming `theirs`. So
#: `theirs` is reachable by somebody and not by them, and a listing of what some agent
#: can hand work to has to leave it out. What holds that is `narrowed_for` reading a
#: definition's own `source_ids:` as the default audience for its entries, one level
#: below the listing -- an audience filter at the call site changed no answer here.
HIDDEN_AGENT = """name: hidden
description: An agent for B alone.
source_ids: [B]
subagents: [theirs]
system_prompt: |
  You do the task.
"""

#: Where the name nothing defines is written. Both are a *file's* mistake, refused
#: whoever is asking, and both list through `offered_to` -- from two different call
#: sites, which is why they are driven apart: the helper one was reached by no test.
A_GHOST_IS_NAMED_BY = {
    "the agent": ("surveyor", "helper"),
    "the subagent": ("helper", "surveyor"),
}


def _subagents_offered(cfg, source_ids) -> set[str]:
    """What one real build's refusal of `ghost` offers this caller instead."""
    kf = Kingfisher(cfg, sessions=LocalSessions())
    session = session_at(kf, "ghosted")
    with pytest.raises(CapabilityError, match=r"unknown subagent\(s\): ghost;") as refused:
        build_agent(
            kf.cfg,
            agent=kf.agent_named("surveyor", source_ids=source_ids),
            held=kf.held_for(source_ids),
            backend=backend_at(kf.cfg, session, catalogue=kf.catalogue),
            capabilities=kf._effective_grants(source_ids),
            catalogue=kf.catalogue,
            checkpointer=None,
        )
    return set(ast.literal_eval(str(refused.value).split("; offered: ", 1)[1]))


@pytest.fixture
def policied_naming_a_ghost(request, policied_with_one_helper):
    """`policied_with_one_helper`, with a name nothing defines written where this case
    says, an agent `A` cannot open, and the subagent only that agent names.

    No agent here declares `"*"`, which is what makes `kept_apart` reachable by no path
    at all -- in a deployment with one, every definition is activated and there is no
    such case to make.
    """
    names, _plain = A_GHOST_IS_NAMED_BY[request.param]
    cfg = policied_with_one_helper
    agents = cfg.catalogue_roots["agents"]
    helpers = subagents_dir(cfg)
    (agents / "hidden.yaml").write_text(HIDDEN_AGENT, encoding="utf-8")
    (helpers / "theirs.yaml").write_text(A_PLAIN_SUBAGENT.format(name="theirs"), encoding="utf-8")
    if names == "surveyor":
        (agents / "surveyor.yaml").write_text(SURVEYOR_NAMING_A_GHOST, encoding="utf-8")
    else:
        (agents / "surveyor.yaml").write_text(AGENT + "subagents: [helper]\n", encoding="utf-8")
        (helpers / "helper.yaml").write_text(
            A_PLAIN_SUBAGENT.format(name="helper") + "subagents: [ghost]\n", encoding="utf-8"
        )
    return cfg


@pytest.mark.parametrize(
    "policied_naming_a_ghost", sorted(A_GHOST_IS_NAMED_BY), indirect=True,
    ids=sorted(A_GHOST_IS_NAMED_BY),
)
def test_a_refusal_lists_no_subagent_no_agent_can_hand_work_to(policied_naming_a_ghost):
    """`kept_apart`'s own line admits this caller and no agent names it, so no request
    can wire it: `Capabilities.subagents` may only narrow what an agent declares.
    Naming it beside a refusal says it exists.

    The listing and the withheld report took both conditions together; the three
    refusals that list through `offered_to` were left asking the subagent's own line
    alone, which is the half that cannot see this. `helper` is asserted beside it,
    because a listing narrowed to nothing would pass the first line.
    """
    offered = _subagents_offered(policied_naming_a_ghost, ("A",))

    assert "kept_apart" not in offered, "no agent hands it work"
    assert "theirs" not in offered, "only an agent `A` cannot open hands it work"
    assert offered == {"helper"}


@pytest.mark.parametrize(
    "policied_naming_a_ghost", ["the agent"], indirect=True, ids=["the agent"]
)
def test_the_operator_is_still_offered_the_subagent_nobody_activates(
    policied_naming_a_ghost,
):
    """`UNSCOPED` reaches everything and there is nobody to narrow for, so the refusal
    says what it always said -- a definition no agent uses is exactly what an operator
    reads a refusal to find out about.
    """
    assert _subagents_offered(policied_naming_a_ghost, UNSCOPED) == {
        "helper",
        "kept_apart",
        "theirs",
    }


def test_a_refusal_lists_no_tool_out_of_the_callers_reach(policied_with_a_typo):
    """The subagent refusals were scoped and this one was not: a caller holding `A` was
    handed every workspace tool and its file -- measured, three names and three paths
    where the agent they reach grants one. A refusal is read by whoever triggered it,
    so it is as much a listing as the listing is.
    """
    assert _refused_listing(policied_with_a_typo, ("A",)) == {"line_count"}


def test_a_definition_naming_a_real_tool_out_of_reach_is_not_refused(
    policied_with_a_stranger,
):
    """What the refusal *judges* stays the whole catalogue, and this is the case that
    says so: `secret_export` exists, so a subagent naming it has made no mistake, and
    the ceiling drops it for a caller whose agent was not granted it -- quietly, which
    is the documented behaviour.

    Narrow the judgement to the caller's reach and this file would be refused for some
    callers and not others, which is exactly what `refuse_misnamed_by` was written to
    stop. Found by mutating the listing fix: with the typo alone, the two sets agree
    and nothing here could tell them apart.
    """
    directory = policied_with_a_stranger.catalogue_roots["agents"]
    (directory / "surveyor.yaml").write_text(AGENT + "subagents: [helper]\n", encoding="utf-8")
    (subagents_dir(policied_with_a_stranger) / "helper.yaml").write_text(
        A_TYPO_IN_A_SUBAGENT.replace("line_counts", "secret_export"), encoding="utf-8"
    )
    kf = Kingfisher(policied_with_a_stranger, sessions=LocalSessions())
    session = session_at(kf, "w-in-reach")

    built = build_agent(
        kf.cfg,
        agent=kf.agent_named("surveyor", source_ids=("A",)),
        held=kf.held_for(("A",)),
        backend=backend_at(kf.cfg, session, catalogue=kf.catalogue),
        capabilities=kf._effective_grants(("A",)),
        catalogue=kf.catalogue,
        checkpointer=None,
    )

    helper = subagent_of(built, "helper")
    assert "secret_export" not in {
        getattr(one, "name", "") for one in helper.get("tools") or ()
    }, "the ceiling drops it, which is why naming it is not an error"


def test_the_operator_is_still_told_every_tool_the_workspace_has(policied_with_a_typo):
    """`UNSCOPED` reaches everything, so nothing is narrowed and the refusal says what
    it always said. The control: a listing narrowed to nothing would pass the rule
    above, and a refusal naming no alternative is one somebody has to go and research.
    """
    assert _refused_listing(policied_with_a_typo, UNSCOPED) == {
        "line_count",
        "secret_export",
        "delegated_only",
    }


LONGHAND = """name: surveyor
description: An agent granting its tool the long way.
source_ids: [A, B]
tools: [line_count.py::line_count]
system_prompt: |
  You do the task.
"""


def test_the_report_reads_a_grant_written_the_long_way(policied):
    """The report asks `offers` the same question the listing does, and had the same
    blindness: `formats.md` documents `where::what`, the catalogue spells a uniquely
    named tool short, and comparing the two as written matched nothing -- so a caller
    whose agent granted the long form was told a run withheld nothing from them.
    """
    directory = policied.catalogue_roots["agents"]
    (directory / "surveyor.yaml").write_text(LONGHAND, encoding="utf-8")
    kf = Kingfisher(replace(policied, access=vocabulary()), sessions=LocalSessions())

    told = dict(reported(kf, ["A"], "longhand", asked_for=Capabilities(tools=())))

    assert told["tool"] == ("line_count",)


def test_a_run_under_no_policy_is_still_told_of_everything_it_declined(policied_with_a_stranger):
    """`UNSCOPED`, and a run with no vocabulary, reach everything -- so there is nothing
    to filter and the report says what it always said. The control for the rule above:
    the filter answers a caller, not every reader.
    """
    kf = Kingfisher(policied_with_a_stranger, sessions=LocalSessions())

    told = reported(kf, UNSCOPED, "w2", asked_for=Capabilities(tools=()))

    (what, names) = told[0]
    assert (what, set(names)) == (
        "tool",
        {"line_count", "secret_export", "delegated_only"},
    )


def test_a_caller_is_not_told_about_what_their_source_ids_took_away(policied):
    """This report names every offered thing a grant left out -- so measured against the
    unfiltered catalogue it would hand a caller the exact list of what their source ids
    denied them.

    The request declines the tool, so that the tools axis is measured at all: left at
    its default the axis is skipped, and this asserted a name was absent from an empty
    report. The caller who does reach the tool is beside it and is told, which is what
    says the axis can name it -- a filter that hid everything from everyone would pass
    the first assertion on its own.
    """
    kf = Kingfisher(policied, sessions=LocalSessions())
    declined = Capabilities(tools=())

    reaches_it = reported(kf, ["A"], "w1", asked_for=declined)
    does_not = reported(kf, ["B"], "w2", asked_for=declined)

    assert reaches_it == (("tool", ("line_count",)),)
    assert does_not == ()


def test_the_report_still_names_a_builtin_the_request_declined(policied):
    """An axis no audience controls is unaffected, so the report keeps doing its
    original job.
    """
    kf = Kingfisher(policied, sessions=LocalSessions())
    session = session_at(kf, "w2")
    held = ("A",)
    grants = replace(kf._effective_grants(held), builtin_tools=("read_file",))
    request = Request(task="t", agent="surveyor")
    graph = kf._graph_for(
        request,
        capabilities=grants,
        checkpointer=None,
        agent=drive(kf._agent_for(request, harness_in(kf.cfg, session), source_ids=held)),
        held=kf.held_for(held),
        files=drive(kf._files_for(session.name)),
    ).graph
    from kingfisher.application.reporting import withheld_by_kind

    kinds = dict(
        withheld_by_kind(
            grants,
            kf.cfg,
            graph,
            kf.catalogue,
            agent=kf.agent_named("surveyor", source_ids=held),
            held=kf.held_for(held),
        )
    )
    assert "execute" in kinds.get("builtin tool", ())


# -- skills take the same audience, by a different road ---------------------

SKILL = """---
name: {name}
description: Something to do.
---

Do it.
"""

SKILLED = """name: skilled
description: Holds two skills at different audiences.
source_ids: [A, B]
skills:
  - name: audit
    source_ids: [A]
  - name: review
    source_ids: [A, B]
system_prompt: |
  You do the task.
"""


@pytest.fixture
def with_skills(cfg):
    """Two skills, and an agent holding them at different audiences."""
    for name in ("audit", "review"):
        folder = cfg.skills_dir / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text(SKILL.format(name=name), encoding="utf-8")
    directory = cfg.catalogue_roots["agents"]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "skilled.yaml").write_text(SKILLED, encoding="utf-8")
    return replace(cfg, access=vocabulary(), skills_enabled=True)


def test_a_skill_audience_narrows_the_selection(with_skills):
    kf = Kingfisher(with_skills, sessions=LocalSessions())

    assert kf.agent_named("skilled", source_ids=UNSCOPED).declares(
        kf.held_for(("A",))
    ).skills == ("audit", "review")
    assert kf.agent_named("skilled", source_ids=UNSCOPED).declares(
        kf.held_for(("B",))
    ).skills == ("review",)


def test_a_skill_out_of_reach_is_not_advertised_to_the_model(with_skills):
    """The half a selection alone does not prove."""
    kf = Kingfisher(with_skills, sessions=LocalSessions())
    held = ("B",)
    session = session_at(kf, "sk1")
    request = Request(task="t", agent="skilled")
    built = kf._graph_for(
        request,
        capabilities=kf._effective_grants(held),
        checkpointer=None,
        agent=drive(kf._agent_for(request, harness_in(kf.cfg, session), source_ids=held)),
        held=kf.held_for(held),
        files=drive(kf._files_for(session.name)),
    )
    narrowed = [m for m in built.middleware if type(m).__name__ == "NarrowedSkills"]
    advertised = {name for m in narrowed for name in m._allowed}

    assert not any(name.endswith("::audit") for name in advertised)
    assert any(name.endswith("::review") for name in advertised)


def skills_withheld(kf, held: tuple[str, ...], granted: tuple[str, ...]) -> tuple[str, ...]:
    """The skills a caller holding `held` is told a request granting `granted` left out."""
    from kingfisher.application.reporting import withheld_by_kind

    grants = replace(kf._effective_grants(held), skills=granted)
    session = session_at(kf, "withheld-" + "-".join(held))
    request = Request(task="t", agent="skilled")
    agent = drive(kf._agent_for(request, harness_in(kf.cfg, session), source_ids=held))
    graph = kf._graph_for(
        request,
        capabilities=grants,
        checkpointer=None,
        agent=agent,
        held=kf.held_for(held),
        files=drive(kf._files_for(session.name)),
    ).graph
    report = withheld_by_kind(
        grants,
        kf.cfg,
        graph,
        kf.catalogue,
        agent=agent,
        held=kf.held_for(held),
    )
    return dict(report).get("skill", ())


def test_a_skill_out_of_reach_is_not_reported_as_withheld(with_skills):
    """The skills row went unfiltered after skills gained an audience, so a caller who
    could not reach `audit` was told a run had withheld it -- naming the one skill their
    source ids exist to hide.
    """
    kf = Kingfisher(with_skills, sessions=LocalSessions())

    assert "audit" not in skills_withheld(kf, ("B",), ("review",))


def test_a_skill_in_reach_is_still_reported_when_the_request_left_it_out(with_skills):
    """The control, from the same caller: `B` cannot reach `audit` but can reach
    `review`, so a request granting neither is told about `review` and only that. Asked
    of a caller with something hidden, because for one who reaches everything the filter
    never runs, and a report that hid every skill would pass.
    """
    kf = Kingfisher(with_skills, sessions=LocalSessions())

    assert skills_withheld(kf, ("B",), ()) == ("review",)


#: A third skill, declared by a subagent `skilled` hands work to and named by no agent.
#: `subagent_skills` narrows a subagent's `skills:` by what the run activated, so this
#: one is inert -- and the report, like the listing, must not name it.
DECLARES_ITS_OWN = """name: helper
description: A subagent.
source_ids: [A, B]
skills: [spare]
system_prompt: |
  You help.
"""


@pytest.fixture
def with_a_subagents_own_skill(with_skills):
    """`with_skills`, plus a skill only a reached subagent declares."""
    folder = with_skills.skills_dir / "spare"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(SKILL.format(name="spare"), encoding="utf-8")
    directory = with_skills.catalogue_roots["agents"]
    (directory / "skilled.yaml").write_text(SKILLED + "subagents: [helper]\n", encoding="utf-8")
    helpers = subagents_dir(with_skills)
    helpers.mkdir(parents=True, exist_ok=True)
    (helpers / "helper.yaml").write_text(DECLARES_ITS_OWN, encoding="utf-8")
    return with_skills


def test_a_skill_only_a_subagent_declares_is_not_reported_as_withheld(
    with_a_subagents_own_skill,
):
    """The skills half of what `offers` asks, on the report rather than the listing. A
    subagent can only narrow what reached it, so a skill no reached agent is told about
    is one this caller gets by no path -- and naming it in the withheld report says it
    exists.

    Unguarded on both readers until now, for one reason: the listing's fixture had the
    subagent declare the skill its own agent already granted, so unioning the subagents
    back in added nothing either rule would notice. `review` is asserted beside it, so a
    filter that emptied the row would not pass.
    """
    kf = Kingfisher(with_a_subagents_own_skill, sessions=LocalSessions())

    told = skills_withheld(kf, ("B",), ())

    assert "spare" not in told, "no agent B reaches is told about it"
    assert told == ("review",), "and the one B does reach is still named"


def test_a_skill_audience_written_qualified_hides_the_bare_name_too(with_skills):
    """The agent file may spell a skill `catalogue::audit` while the listing says `audit`.
    Compared as written, the audience would hide nothing.
    """
    agent = with_skills.catalogue_roots["agents"] / "skilled.yaml"
    agent.write_text(
        agent.read_text(encoding="utf-8").replace("- name: audit", "- name: catalogue::audit"),
        encoding="utf-8",
    )
    kf = Kingfisher(with_skills, sessions=LocalSessions())

    assert "audit" not in skills_withheld(kf, ("B",), ("review",))
    assert "audit" in skills_withheld(kf, ("A",), ("review",))


def test_a_caller_the_audience_admits_is_told_about_both(with_skills):
    """So the assertion above is not passing because nothing was advertised."""
    kf = Kingfisher(with_skills, sessions=LocalSessions())
    held = ("A",)
    session = session_at(kf, "sk2")
    request = Request(task="t", agent="skilled")
    built = kf._graph_for(
        request,
        capabilities=kf._effective_grants(held),
        checkpointer=None,
        agent=drive(kf._agent_for(request, harness_in(kf.cfg, session), source_ids=held)),
        held=kf.held_for(held),
        files=drive(kf._files_for(session.name)),
    )
    narrowed = [m for m in built.middleware if type(m).__name__ == "NarrowedSkills"]
    advertised = {name for m in narrowed for name in m._allowed}

    assert any(name.endswith("::audit") for name in advertised)
    assert any(name.endswith("::review") for name in advertised)


# -- a subagent's own audiences, wherever it is used ------------------------

DELEGATED_TOOL = '''
def {name}(q: str) -> str:
    """{name}."""
    return "0"


TOOLS = [{name}]
'''

#: `formats.md`'s example, with a skill and a helper written the same way, and one
#: helper -- `insider` -- that its own line rather than this entry keeps from `C`.
REVIEWER = """name: reviewer
description: Reviews.
source_ids: [A, B, C]
tools:
  - name: sql_query
    source_ids: [A, B]
  - name: http_fetch
    source_ids: [A, B, C]
skills:
  - name: audit
    source_ids: [A, B]
  - name: review
subagents:
  - name: second-opinion
    source_ids: [A, B]
  - plain-helper
  - insider
system_prompt: |
  You review.
"""

OPEN_TO_ALL = """name: {name}
description: Helps.
source_ids: [A, B, C]
system_prompt: |
  You help.
"""

INSIDER = """name: insider
description: Only for A.
source_ids: [A]
system_prompt: |
  You keep it.
"""

COMPILED = '''
def _build(model, tools):
    from langchain.agents import create_agent

    return create_agent(model, tools)


SUBAGENTS = [
    {
        "name": "profiler",
        "description": "Profiles.",
        "source_ids": ["A", "B", "C"],
        "tools": [{"name": "sql_query", "source_ids": ["A"]}, "http_fetch"],
        "build": _build,
    },
    {
        "name": "ledger",
        "description": "Only for A.",
        "source_ids": ["A"],
        "build": _build,
    },
]
'''

ABC = "source_ids: [A, B, C]\n"

#: Names every subagent itself and grants both skills, so nothing a subagent loses
#: below is the agent's doing.
BOSS = """name: boss
description: Delegates.
source_ids: [A, B, C]
skills: [audit, review]
subagents: [reviewer, second-opinion, plain-helper, insider, profiler, ledger]
system_prompt: |
  You delegate.
"""


@pytest.fixture
def delegating(cfg):
    """An agent every caller opens, delegating to subagents whose own lines narrow `C`."""
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    for name in ("sql_query", "http_fetch"):
        (tools_dir(cfg) / f"{name}.py").write_text(
            DELEGATED_TOOL.format(name=name), encoding="utf-8"
        )
    for name in ("audit", "review"):
        folder = cfg.skills_dir / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text(SKILL.format(name=name), encoding="utf-8")
    subagents = subagents_dir(cfg)
    subagents.mkdir(parents=True, exist_ok=True)
    (subagents / "reviewer.yaml").write_text(REVIEWER, encoding="utf-8")
    for name in ("second-opinion", "plain-helper"):
        (subagents / f"{name}.yaml").write_text(OPEN_TO_ALL.format(name=name), encoding="utf-8")
    (subagents / "insider.yaml").write_text(INSIDER, encoding="utf-8")
    (subagents / "graphs.py").write_text(COMPILED, encoding="utf-8")
    agents = cfg.catalogue_roots["agents"]
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "boss.yaml").write_text(BOSS, encoding="utf-8")
    # In the workspace too, for `kingfisher list`, which reads it from there.
    (cfg.definitions / "source_ids.yaml").write_text(ABC, encoding="utf-8")
    return replace(cfg, access=vocabulary(ABC), skills_enabled=True)


def delegated(cfg, source_ids: tuple[str, ...]):
    """What `boss` is built with for one caller, as `create_deep_agent` received it."""
    kf = Kingfisher(cfg, sessions=LocalSessions())
    session = session_at(kf, "as-" + "".join(source_ids))
    return build_agent(
        kf.cfg,
        agent=kf.agent_named("boss", source_ids=source_ids),
        held=kf.held_for(source_ids),
        backend=backend_at(kf.cfg, session, catalogue=kf.catalogue),
        capabilities=kf._effective_grants(source_ids),
        catalogue=kf.catalogue,
        checkpointer=None,
    )


def holding(built, name: str) -> dict[str, set[str]]:
    """The tools, allowlist, skills and helpers one assembled subagent was handed."""
    found: dict[str, set[str]] = {
        "tools": {
            getattr(one, "name", getattr(one, "__name__", ""))
            for one in subagent_of(built, name).get("tools", ())
        },
        "allowed": set(),
        "skills": set(),
        "helpers": set(),
    }
    for one in middleware_of(built, name):
        if isinstance(one, ToolAllowlist):
            found["allowed"] = set(one._allowed)
        elif type(one).__name__ == "NarrowedSkills":
            found["skills"] = {skill.rsplit("::", 1)[-1] for skill in one._allowed}
        elif isinstance(one, SubAgentMiddleware):
            found["helpers"] = {helper["name"] for helper in one._subagents}
    return found


def built_names(built) -> set[str]:
    return {spec["name"] for spec in declared_subagents(built)}


def test_a_caller_in_c_gets_a_reviewer_holding_http_fetch_and_not_sql_query(delegating):
    """`formats.md`'s own example: the build read `reviewer`'s raw `tools:` and handed a
    caller in `C` the `sql_query` its own entry keeps from them.
    """
    held = holding(delegated(delegating, ("C",)), "reviewer")

    assert held["tools"] == {"http_fetch"}
    assert "http_fetch" in held["allowed"]
    assert "sql_query" not in held["allowed"]


def test_a_subagents_own_audiences_narrow_its_skills_and_helpers_too(delegating):
    """The other two audienced fields were read raw as well, and a helper whose own line
    excludes the caller was built under a parent that named it.
    """
    built = delegated(delegating, ("C",))
    held = holding(built, "reviewer")

    assert held["skills"] == {"review"}
    assert held["helpers"] == {"plain-helper"}
    # `reviewer`'s entry narrows `reviewer`'s context and not the agent's.
    assert "second-opinion" in built_names(built)


@pytest.mark.parametrize("name", ["insider", "ledger"])
def test_a_subagent_whose_own_source_ids_exclude_the_caller_is_not_built(delegating, name):
    """An agent naming a subagent built it for every caller that agent admitted, a
    document and a compiled declaration alike, whatever the subagent's own line said.
    """
    assert name not in built_names(delegated(delegating, ("C",)))


def test_a_compiled_subagent_is_handed_only_the_tools_its_audiences_admit(delegating):
    """`build` was handed every tool the declaration listed, its per-tool audience unread."""
    graph = subagent_of(delegated(delegating, ("C",)), "profiler")["runnable"]

    assert "http_fetch" in dispatched(graph)
    assert "sql_query" not in dispatched(graph)


def test_a_caller_every_audience_admits_gets_everything_each_subagent_names(delegating):
    """The control: every absence above also holds for a build that withheld these names
    from everyone.
    """
    built = delegated(delegating, ("A",))
    held = holding(built, "reviewer")

    assert held["tools"] == {"http_fetch", "sql_query"}
    assert {"http_fetch", "sql_query"} <= held["allowed"]
    assert held["skills"] == {"audit", "review"}
    assert held["helpers"] == {"second-opinion", "plain-helper", "insider"}
    assert {"insider", "ledger"} <= built_names(built)
    assert {"http_fetch", "sql_query"} <= set(
        dispatched(subagent_of(built, "profiler")["runnable"])
    )


# -- what a caller is told about a subagent out of reach --------------------

#: Keeps `plain-helper` from `C` with its own entry, where `insider` is kept by its
#: own line, and names nothing else but `second-opinion`.
GATEKEEPER = """name: gatekeeper
description: Delegates less.
source_ids: [A, B, C]
subagents:
  - name: plain-helper
    source_ids: [A]
  - second-opinion
system_prompt: |
  You delegate.
"""


def on_the_default_host(cfg, *names: str) -> None:
    """Put these subagents on `cheap-model`, which shares the default's host, so a run
    has something to report about each one it activates.
    """
    for name in names:
        path = subagents_dir(cfg) / f"{name}.yaml"
        text = path.read_text(encoding="utf-8")
        path.write_text(
            text.replace("system_prompt:", "model: cheap-model\nsystem_prompt:"),
            encoding="utf-8",
        )


def told(cfg, source_ids: tuple[str, ...], *, agent: str = "boss", **asked):
    """The subagents one real turn tells this caller were not granted, and that run on
    the main agent's host, read off the events before the model is reached.
    """
    kf = Kingfisher(cfg, sessions=LocalSessions())
    turn = kf.stream(
        Request("go", agent=agent, capabilities=Capabilities(**asked)), source_ids=source_ids
    )
    found: dict[str, set[str]] = {"withheld": set(), "indistinct": set()}
    try:
        for event in turn:
            if event.kind == "run_start":
                break
            if event.kind == "withheld" and " subagent(s) " in event.text:
                found["withheld"] |= set(event.text.split(": ", 1)[1].split(", "))
            elif event.kind == "indistinct" and event.agent is not None:
                found["indistinct"].add(event.agent)
    finally:
        turn.close()
    return found


def listed(capsys, source_ids: str, *, as_json: bool = False) -> str:
    """What `kingfisher list --as` prints for one caller."""
    assert main(["list", "--as", source_ids, *(["--json"] if as_json else [])]) == 0
    return capsys.readouterr().out


def test_a_subagent_out_of_reach_is_not_reported_as_withheld(delegating):
    """A request narrowing `subagents:` told a caller in `C` that `insider` and `ledger`
    were not granted, though their own `source_ids:` exist to hide them from `C`.
    """
    assert told(delegating, ("C",), subagents=("reviewer",))["withheld"] == {
        "plain-helper",
        "profiler",
        "second-opinion",
    }


@pytest.mark.parametrize("as_json", [False, True])
def test_a_scoped_listing_chains_no_agent_to_a_subagent_out_of_reach(
    as_json, delegating, at_the_command_line, capsys
):
    """`list --as C` left `insider` and `ledger` out of its subagents and still printed
    both on `boss`'s `subagents:` line.
    """
    printed = listed(capsys, "C", as_json=as_json)

    assert [one for one in ("insider", "ledger") if one in printed] == []
    assert "reviewer" in printed


def test_a_subagent_out_of_reach_is_not_reported_as_running_on_the_callers_host(
    delegating,
):
    """The report of a subagent that meant to run elsewhere read the request's `"*"`,
    and told a caller in `C` which model `insider` asks for.
    """
    on_the_default_host(delegating, "insider", "plain-helper")

    assert told(delegating, ("C",))["indistinct"] == {"plain-helper"}


def test_the_listing_chains_what_an_agent_activates_for_this_caller(
    delegating, at_the_command_line, capsys
):
    """The chain read the agent's raw `subagents:` line, so `C` saw `plain-helper` under
    `gatekeeper`, whose own entry keeps it from them.
    """
    (delegating.catalogue_roots["agents"] / "gatekeeper.yaml").write_text(
        GATEKEEPER, encoding="utf-8"
    )

    chains = {
        held: json.loads(listed(capsys, held, as_json=True))["agent_subagents"]["gatekeeper"]
        for held in ("C", "A")
    }

    assert chains == {"C": ["second-opinion"], "A": ["plain-helper", "second-opinion"]}


def test_a_run_reports_only_the_subagents_it_activated(delegating):
    """Reading the request's grant, the report named `plain-helper` to a caller in `C`
    whom `gatekeeper`'s entry keeps it from, and `insider` to a caller in `A` whose
    agent never names it.
    """
    (delegating.catalogue_roots["agents"] / "gatekeeper.yaml").write_text(
        GATEKEEPER, encoding="utf-8"
    )
    on_the_default_host(delegating, "insider", "plain-helper")

    assert told(delegating, ("C",), agent="gatekeeper")["indistinct"] == set()
    assert told(delegating, ("A",), agent="gatekeeper")["indistinct"] == {"plain-helper"}


def test_a_caller_every_audience_admits_is_told_about_each_subagent(
    delegating, at_the_command_line, capsys
):
    """The control: every absence above also holds for a report that named these
    subagents to nobody.
    """
    on_the_default_host(delegating, "insider", "plain-helper")

    assert {"insider", "ledger"} <= told(delegating, ("A",), subagents=("reviewer",))["withheld"]
    assert told(delegating, ("A",))["indistinct"] == {"insider", "plain-helper"}
    assert {"insider", "ledger"} <= set(
        json.loads(listed(capsys, "A", as_json=True))["agent_subagents"]["boss"]
    )
    printed = listed(capsys, "A")
    assert "insider" in printed
    assert "ledger" in printed


# -- what a refusal offers a caller instead ---------------------------------

#: The three places a subagent is named, each naming `ghost`, which nothing defines:
#: an agent's own file, a request under an agent offering every subagent, and a
#: subagent's file naming a helper.
GHOSTLY = """name: ghostly
description: Names a subagent nothing defines.
source_ids: [A, B, C]
subagents: [plain-helper, ghost]
system_prompt: |
  You delegate.
"""

OPENER = """name: opener
description: Offers every subagent, so a request may name any.
source_ids: [A, B, C]
subagents: ["*"]
system_prompt: |
  You delegate.
"""

ASKER = """name: asker
description: Delegates to a subagent whose helper is a typo.
source_ids: [A, B, C]
subagents: [helpless]
system_prompt: |
  You delegate.
"""

HELPLESS = """name: helpless
description: Names a helper nothing defines.
source_ids: [A, B, C]
subagents: [plain-helper, ghost]
system_prompt: |
  You help.
"""

WHERE = ["agent", "request", "helper"]


def naming_a_ghost(cfg, where: str) -> tuple[str, Capabilities]:
    """Write what names `ghost` at `where`; the agent to run and what to ask of it."""
    agents = cfg.catalogue_roots["agents"]
    if where == "agent":
        (agents / "ghostly.yaml").write_text(GHOSTLY, encoding="utf-8")
        return "ghostly", Capabilities()
    if where == "request":
        (agents / "opener.yaml").write_text(OPENER, encoding="utf-8")
        return "opener", Capabilities(subagents=("ghost",))
    (subagents_dir(cfg) / "helpless.yaml").write_text(HELPLESS, encoding="utf-8")
    (agents / "asker.yaml").write_text(ASKER, encoding="utf-8")
    return "asker", Capabilities()


def offered_instead(cfg, source_ids: tuple[str, ...] | None, where: str) -> set[str]:
    """The subagents one real turn's refusal of `ghost` lists to this caller."""
    agent, asked = naming_a_ghost(cfg, where)
    kf = Kingfisher(cfg, sessions=LocalSessions())
    with pytest.raises(CapabilityError, match=r"unknown subagent\(s\): ghost;") as refused:
        turn = kf.stream(Request("go", agent=agent, capabilities=asked), source_ids=source_ids)
        try:
            for event in turn:
                if event.kind == "run_start":
                    break
        finally:
            turn.close()
    return set(ast.literal_eval(str(refused.value).split("; offered: ", 1)[1]))


#: Every subagent `delegating` defines that `C` reaches, which is all but `insider`
#: and `ledger`.
REACHED_BY_C = {"plain-helper", "profiler", "reviewer", "second-opinion"}


@pytest.mark.parametrize("where", WHERE)
def test_refusing_an_unknown_subagent_lists_none_out_of_the_callers_reach(delegating, where):
    """Refusing `ghost` listed every subagent the catalogue defines, so a caller in `C`
    read `insider` and `ledger` there, whose own `source_ids:` exist to hide them.
    """
    expected = REACHED_BY_C | ({"helpless"} if where == "helper" else set())

    assert offered_instead(delegating, ("C",), where) == expected


@pytest.mark.parametrize("where", WHERE)
@pytest.mark.parametrize("source_ids", [("A",), None], ids=["A", "no-policy"])
def test_refusing_an_unknown_subagent_lists_every_one_a_caller_reaches(
    delegating, where, source_ids
):
    """The control: a refusal listing nothing out of reach to anybody, a caller in `A` or
    a run with no `source_ids.yaml`, would pass the test above.
    """
    cfg = delegating if source_ids is not None else replace(delegating, access=None)
    expected = REACHED_BY_C | {"insider", "ledger"} | ({"helpless"} if where == "helper" else set())

    assert offered_instead(cfg, source_ids, where) == expected


# -- a name a caller writes, out of reach or never written ------------------


def refusal(cfg, source_ids: tuple[str, ...] | None, agent: str, asked: Capabilities) -> str:
    """What one real turn refuses this caller with, or `""` when it reaches the model."""
    kf = Kingfisher(cfg, sessions=LocalSessions())
    try:
        turn = kf.stream(Request("go", agent=agent, capabilities=asked), source_ids=source_ids)
        try:
            for event in turn:
                if event.kind == "run_start":
                    break
        finally:
            turn.close()
    except CapabilityError as refused:
        return str(refused)
    return ""


def opening(cfg) -> str:
    """Write `opener`, which offers every subagent, so a request's names reach the build."""
    (cfg.catalogue_roots["agents"] / "opener.yaml").write_text(OPENER, encoding="utf-8")
    return "opener"


#: What no message to `C` may name: the two `delegating` hides by their own line, and
#: the `x` that `two_of_x` hides the same way.
HIDDEN_FROM_C = ("insider", "ledger", "b/x.yaml::x")


def naming_none_hidden(*messages: str) -> bool:
    return not [name for name in HIDDEN_FROM_C for one in messages if name in one]


#: What an agent may say about its subagents, and whether a request's own name then
#: reaches the build to be refused. Only the star lets it through: a list and an absent
#: line both narrow a request's name away silently, which the rule below this one
#: holds for one agent and this holds for the three shapes.
NAMES_REACH_THE_BUILD = {
    "no subagents line": ("", False),
    "a list": ("subagents: [mine]\n", False),
    "the star": ('subagents: ["*"]\n', True),
}


@pytest.mark.parametrize("shape", sorted(NAMES_REACH_THE_BUILD))
def test_a_request_is_refused_a_name_only_by_an_agent_that_offers_every_subagent(
    shape, policied
):
    """Which is the premise a narrower answer at that refusal rests on, and the reason
    no rule can assert the narrower answer itself.

    `_activated_subagents` lists what the agent the build *opened* hands work to,
    rather than what every agent this caller can open does, because
    `Capabilities.subagents` may only narrow what that agent declares. Measured, the
    two answers cannot differ: a refusal there needs the request's name to survive the
    agent's ceiling, only `"*"` lets it through, and `"*"` is every subagent in reach
    -- a superset of what any other agent activates, so the union equals it. The
    other branch of that call, for a build that resolved no agent, is unreachable: a
    workspace offering no agent refuses the turn before this.

    So this pins the premise instead. Widen the refusal -- let a listing agent refuse
    a name it does not declare -- and this rule goes red, which is the moment the
    narrower listing becomes something a rule can see.
    """
    line, refused = NAMES_REACH_THE_BUILD[shape]
    (policied.catalogue_roots["agents"] / "surveyor.yaml").write_text(
        AGENT + line, encoding="utf-8"
    )
    helpers = subagents_dir(policied)
    helpers.mkdir(parents=True, exist_ok=True)
    for name in ("mine", "nobodys"):
        (helpers / f"{name}.yaml").write_text(
            A_PLAIN_SUBAGENT.format(name=name), encoding="utf-8"
        )

    said = refusal(policied, ("A",), "surveyor", Capabilities(subagents=("ghost",)))

    assert bool(said) is refused, said or "nothing was refused"
    if refused:
        # Everything in reach, which is what makes the narrower answer the same answer.
        assert "offered: ('mine', 'nobodys')" in said


@pytest.mark.parametrize("name", ["insider", "ledger"])
def test_a_request_naming_a_subagent_out_of_reach_reads_what_a_typo_reads(delegating, name):
    """Under an agent offering every subagent, `C` naming `insider` ran without it and was
    told nothing while `ghost` was refused, so guessing told a real name from a typo.
    """
    agent = opening(delegating)

    hidden = refusal(delegating, ("C",), agent, Capabilities(subagents=(name,)))
    typo = refusal(delegating, ("C",), agent, Capabilities(subagents=("ghost",)))

    assert typo.startswith("this request names unknown subagent(s): ghost;")
    assert hidden == typo.replace("ghost", name)
    assert naming_none_hidden(typo)


def test_a_typo_and_a_subagent_out_of_reach_are_refused_together(delegating):
    """Both are unknown to `C`, so a refusal naming one and passing over the other would
    say which was real.
    """
    message = refusal(
        delegating, ("C",), opening(delegating), Capabilities(subagents=("ghost", "insider"))
    )

    assert message.startswith("this request names unknown subagent(s): ghost, insider;")


@pytest.mark.parametrize("source_ids", [("A",), None], ids=["A", "no-policy"])
def test_a_caller_who_reaches_the_subagent_named_still_runs_with_it(delegating, source_ids):
    """The control: refusing `insider` to everybody would pass the tests above."""
    cfg = delegating if source_ids is not None else replace(delegating, access=None)
    agent = opening(cfg)

    assert refusal(cfg, source_ids, agent, Capabilities(subagents=("insider",))) == ""
    assert refusal(cfg, source_ids, agent, Capabilities(subagents=("ghost",))).startswith(
        "this request names unknown subagent(s): ghost;"
    )


@pytest.mark.parametrize("name", ["insider", "ghost"])
def test_an_agent_listing_its_subagents_narrows_either_name_silently(delegating, name):
    """`boss` lists its subagents, so neither name reaches the build as written, and
    refusing one of them but not the other would reopen the gap.
    """
    assert refusal(delegating, ("C",), "boss", Capabilities(subagents=(name,))) == ""


# -- two subagents called `x`, one of them out of reach ---------------------

X = """name: x
description: The {where} x.
source_ids: {source_ids}
system_prompt: |
  You x.
"""


@pytest.fixture
def two_of_x(delegating):
    """`delegating` and `opener`, with an `x` every caller reaches and another only `A`
    does.
    """
    for where, source_ids in (("a", "[A, B, C]"), ("b", "[A]")):
        folder = subagents_dir(delegating) / where
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "x.yaml").write_text(
            X.format(where=where, source_ids=source_ids), encoding="utf-8"
        )
    opening(delegating)
    return delegating


def test_every_subagent_means_every_one_the_caller_reaches(two_of_x):
    """`"*"` was every definition, so `C`, reaching one `x`, was refused for activating
    two and shown the other's file.
    """
    assert refusal(two_of_x, ("C",), "opener", Capabilities()) == ""
    assert "Activate the one you meant: a/x.yaml::x, b/x.yaml::x" in refusal(
        two_of_x, ("A",), "opener", Capabilities()
    )


def test_a_bare_name_means_the_one_the_caller_reaches(two_of_x):
    """`C` writing `x` was refused because more than one source offers it, with the
    spelling only `A` reaches listed as the fix.
    """
    on_the_default_host(two_of_x, "a/x")

    run = told(two_of_x, ("C",), agent="opener", subagents=("x",))

    # The reports read what the build activated, which is the `x` this caller reaches.
    assert run["indistinct"] == {"a/x.yaml::x"}
    assert "a/x.yaml::x" not in run["withheld"]
    assert naming_none_hidden(*run["withheld"])


def test_a_bare_name_two_reachable_subagents_share_is_still_refused(two_of_x):
    """The control: settling a bare name for everybody would pass the test above."""
    assert "write a/x.yaml::x, b/x.yaml::x" in refusal(
        two_of_x, ("A",), "opener", Capabilities(subagents=("x",))
    )


def test_naming_the_other_x_reads_what_a_typo_reads(two_of_x):
    """`C` naming `b/x.yaml::x` ran without it and was told nothing, where a spelling
    nothing defines was refused.
    """
    hidden = refusal(two_of_x, ("C",), "opener", Capabilities(subagents=("b/x.yaml::x",)))
    typo = refusal(two_of_x, ("C",), "opener", Capabilities(subagents=("b/y.yaml::x",)))

    assert typo.startswith("this request names unknown subagent(s): b/y.yaml::x;")
    assert hidden == typo.replace("b/y.yaml::x", "b/x.yaml::x")
    assert naming_none_hidden(typo)


# -- a mistake in a file, refused for every caller --------------------------

#: Keeps `ghost` from `C` with its entry, so only a check reading the file as written
#: refuses it to them.
SHADY = """name: shady
description: Names a subagent nothing defines, behind an audience.
source_ids: [A, B, C]
subagents:
  - plain-helper
  - name: ghost
    source_ids: [A]
system_prompt: |
  You delegate.
"""


def test_an_agents_own_typo_is_refused_in_its_name(delegating):
    """An agent's typo was refused as "this request names", sending the caller to fix a
    request that named nothing.
    """
    agent, asked = naming_a_ghost(delegating, "agent")

    assert refusal(delegating, ("C",), agent, asked).startswith(
        "agent 'ghostly' names unknown subagent(s): ghost;"
    )


@pytest.mark.parametrize("source_ids", [("C",), ("A",)])
def test_an_agents_typo_behind_an_entry_audience_is_refused_for_every_caller(
    delegating, source_ids
):
    """The agent's names were checked as this caller reaches them, so a typo its entry
    keeps from `C` was refused for `A` and ran silently for `C`.
    """
    (delegating.catalogue_roots["agents"] / "shady.yaml").write_text(SHADY, encoding="utf-8")

    message = refusal(delegating, source_ids, "shady", Capabilities())

    assert message.startswith("agent 'shady' names unknown subagent(s): ghost;")


# -- what the model is told when it asks for one ----------------------------


def task(*names: str, at: str = "") -> AIMessage:
    """One model turn calling `task` for each of these, each call's id `at` + its name."""
    return AIMessage(
        content="",
        tool_calls=[
            {"name": "task", "args": {"description": "go", "subagent_type": n}, "id": at + n}
            for n in names
        ],
    )


def results_shown() -> dict[str, str]:
    """Every tool result a scripted turn showed any of its models, by call id, less the
    ledger id stamped on each, which counts calls and so differs between any two.
    """
    return {
        message.tool_call_id: re.sub(r"\n\[[^\]\n]+\]$", "", str(message.content))
        for heard in Scripted.heard
        for message in heard
        if isinstance(message, ToolMessage)
    }


def test_a_task_out_of_reach_reads_what_a_task_to_a_typo_reads(delegating, scripted):
    """Both refusals of a `task` -- `DeclaredSubagentsOnly` at the agent, deepagents' own
    inside a subagent -- list what was built, so either would tell `insider` from
    `ghost` the moment it listed or worded the two apart.
    """
    Scripted.script.extend([
        task("reviewer"),
        # `reviewer`, which consults `plain-helper` and, for `C`, not `insider`.
        task("insider", "ghost", at="nested-"),
        AIMessage(content="reviewed"),
        task("insider", "ghost", at="top-"),
        AIMessage(content="done"),
        # Spare, so a build that let `insider` run fails on what the models were shown
        # rather than on a script it ran out of.
        *[AIMessage(content="spare")] * 4,
    ])
    kf = Kingfisher(replace(delegating, models=scripted.models), sessions=LocalSessions())

    list(kf.stream(Request("go", agent="boss"), source_ids=("C",)))
    shown = results_shown()

    # Each level's own refusal, listing what it reaches: a test passing on another
    # refusal, or on an empty list, would say nothing about these two.
    assert "does not exist" in shown["nested-ghost"]
    assert "plain-helper" in shown["nested-ghost"]
    assert "is not a subagent this request may use" in shown["top-ghost"]
    assert "reviewer" in shown["top-ghost"]
    for at in ("nested-", "top-"):
        assert shown[at + "insider"] == shown[at + "ghost"].replace("ghost", "insider")
        assert naming_none_hidden(shown[at + "ghost"])
