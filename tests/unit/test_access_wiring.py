"""The definitions' audiences reaching a run: who is calling, and what is built."""

from __future__ import annotations

from dataclasses import replace

import pytest
import yaml

from kingfisher import default_backend
from kingfisher.application.service import Kingfisher
from kingfisher.domain.access import UNSCOPED, AccessError, parse
from kingfisher.domain.capabilities import Capabilities
from kingfisher.domain.request import Request
from kingfisher.infrastructure.harness.agent import build_agent
from kingfisher.infrastructure.workspace import ensure_session_layout
from tests.conftest import an_agent, tools_dir

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
        backend=default_backend(kf.cfg, session, catalogue=kf.catalogue),
        capabilities=kf._effective_grants(held),
        session_dir=session,
        catalogue=kf.catalogue,
        checkpointer=None,
    )
    return [getattr(t, "name", getattr(t, "__name__", "")) for t in assembled.tools or ()]


# -- who is calling ---------------------------------------------------------


def test_a_call_that_does_not_say_who_is_calling_is_refused(policied):
    """The dangerous failure is a handler that forgot the boundary, so it is made loud
    rather than left to grant everything in silence.
    """
    kf = Kingfisher(policied, backend=default_backend)
    with pytest.raises(AccessError, match="source_ids="):
        kf.run("anything")


def test_unscoped_runs_without_a_caller_and_says_so_at_the_call(policied):
    """The opt-out is a value someone typed, so a review can find it."""
    kf = Kingfisher(policied, backend=default_backend)
    assert kf.held_for(UNSCOPED) is None


def test_an_unknown_source_id_is_refused(policied):
    """The closed vocabulary, from the caller's end: a typo would otherwise reach
    nothing, which looks exactly like a caller who was denied.
    """
    kf = Kingfisher(policied, backend=default_backend)
    with pytest.raises(AccessError, match="unknown source id"):
        kf.held_for(("Q",))


def test_naming_source_ids_where_there_is_no_vocabulary_is_refused(cfg):
    """A caller naming source ids against a deployment that declares none is confused, and
    silently ignoring them is how they stay confused.
    """
    kf = Kingfisher(cfg, backend=default_backend)
    with pytest.raises(AccessError, match="no access policy"):
        kf._effective_grants(("A",))


def test_a_list_of_source_ids_narrows_exactly_as_a_tuple_does(policied):
    """The trap that folding `for_groups` in had to disarm."""
    kf = Kingfisher(policied, backend=default_backend)

    assert kf.held_for(["A"]) == kf.held_for(("A",))
    assert kf.held_for(["A"]) is not None, "a list read as no opinion at all"


def test_a_bare_string_of_source_ids_is_refused_rather_than_spelled_out(policied):
    """`source_ids="sales_db"` is iterable, so coercing it yields eight one-letter source id
    names.
    """
    kf = Kingfisher(policied, backend=default_backend)

    with pytest.raises(AccessError, match="not a string"):
        kf.held_for("A")


def test_one_function_reads_what_shape_a_caller_s_source_ids_are_in():
    """`agent_named` and `session()` each asked "is it a tuple?" while `held_for` took
    any sequence, so `["B"]` opened an agent restricted to A -- one value, read three
    ways. Walked from the source, so a fourth reader fails here the day it is written.
    """
    import ast

    from tests.conftest import repository_root

    application = repository_root() / "src" / "kingfisher" / "application"
    readers = set()
    for path in sorted(application.glob("*.py")):
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

    assert readers == {"access.held_by"}, (
        f"the shape of source_ids is read in {sorted(readers)} -- ask "
        "`application.access.held_by` instead, or one reader will disagree with another"
    )


def test_a_deployment_without_a_vocabulary_is_unchanged(cfg):
    """Everything that worked before this must still work untouched -- including calling
    `run` without saying anything about source ids.
    """
    kf = Kingfisher(cfg, backend=default_backend)
    assert kf.access is None
    assert kf.held_for(None) is None


def test_resolving_the_same_source_ids_twice_gives_the_same_grant(policied):
    """It was a handle that could be bound once and reused; now the resolution happens
    per call, so the thing worth asserting is that it is stable.
    """
    kf = Kingfisher(policied, backend=default_backend)
    assert kf._effective_grants(("A",)) == kf._effective_grants(("A",))


def test_the_deployments_own_grants_still_bound_a_caller(policied):
    """Two ceilings, and the lower one wins."""
    kf = Kingfisher(policied, backend=default_backend, grants=Capabilities(tools=()))
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
    return Kingfisher(cfg, backend=default_backend)


# -- the report -------------------------------------------------------------


def test_a_definition_with_no_source_ids_line_is_named(cfg):
    """Default-open must not also be silent."""
    an_agent(cfg, "assistant")
    kf = Kingfisher(replace(cfg, access=vocabulary()), backend=default_backend)
    assert ("agent", "assistant") in kf.access_report.unrestricted


def test_a_subagent_with_no_source_ids_line_is_named_too(cfg):
    """Both kinds, asserted rather than assumed.

    Mutation testing found this: the walk could stop looking at subagents entirely
    and the whole suite stayed green, so a delegate reachable by everyone would have
    gone unreported while the agent beside it was named. The report's whole job is
    that default-open is not silent, and it was silent for half the definitions it
    covers.
    """
    an_agent(cfg, "assistant")
    delegates = cfg.catalogue_roots["subagents"]
    delegates.mkdir(parents=True, exist_ok=True)
    (delegates / "auditor.yaml").write_text(
        "name: auditor\ndescription: A delegate.\nsystem_prompt: |\n  Do it.\n",
        encoding="utf-8",
    )

    kf = Kingfisher(replace(cfg, access=vocabulary()), backend=default_backend)

    assert ("subagent", "auditor") in kf.access_report.unrestricted
    assert "auditor" in "\n".join(kf.access_report.lines())


def test_a_definition_that_restricts_is_not_named(policied):
    kf = Kingfisher(policied, backend=default_backend)
    assert kf.access_report.is_clean


def test_the_report_reads_as_a_sentence(cfg):
    an_agent(cfg, "assistant")
    kf = Kingfisher(replace(cfg, access=vocabulary()), backend=default_backend)
    rendered = "\n".join(kf.access_report.lines())
    assert "reachable by everyone" in rendered
    assert "assistant" in rendered


# -- what a caller is told --------------------------------------------------


def reported(kf, source_ids, name: str):
    """The withheld report a caller holding these source ids is handed for one turn."""
    from kingfisher.application.reporting import withheld_by_kind

    session = session_at(kf, name)
    held_names = tuple(source_ids) if source_ids is not UNSCOPED else source_ids
    held = kf.held_for(held_names)
    graph = kf._graph_for(
        Request(task="t", agent="surveyor"),
        session,
        capabilities=kf._effective_grants(held_names),
        checkpointer=None,
        source_ids=held_names,
    ).graph
    return withheld_by_kind(
        kf._effective_grants(held_names),
        kf.cfg,
        graph,
        kf.catalogue,
        agent=kf.agent_named("surveyor", source_ids=held_names),
        held=held,
    )


def test_a_caller_is_not_told_about_what_their_source_ids_took_away(policied):
    """This report names every offered thing a grant left out -- so measured against the
    unfiltered catalogue it would hand a caller the exact list of what their source ids
    denied them.
    """
    kf = Kingfisher(policied, backend=default_backend)
    names = " ".join(n for _kind, withheld in reported(kf, ["B"], "w1") for n in withheld)
    assert "line_count" not in names


def test_the_report_still_names_a_builtin_the_request_declined(policied):
    """An axis no audience controls is unaffected, so the report keeps doing its
    original job.
    """
    kf = Kingfisher(policied, backend=default_backend)
    session = session_at(kf, "w2")
    held = ("A",)
    grants = replace(kf._effective_grants(held), builtin_tools=("read_file",))
    graph = kf._graph_for(
        Request(task="t", agent="surveyor"),
        session,
        capabilities=grants,
        checkpointer=None,
        source_ids=held,
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
    kf = Kingfisher(with_skills, backend=default_backend)

    assert kf.agent_named("skilled", source_ids=UNSCOPED).declares(
        kf.held_for(("A",))
    ).skills == ("audit", "review")
    assert kf.agent_named("skilled", source_ids=UNSCOPED).declares(
        kf.held_for(("B",))
    ).skills == ("review",)


def test_a_skill_out_of_reach_is_not_advertised_to_the_model(with_skills):
    """The half a selection alone does not prove."""
    kf = Kingfisher(with_skills, backend=default_backend)
    held = ("B",)
    built = kf._graph_for(
        Request(task="t", agent="skilled"),
        session_at(kf, "sk1"),
        capabilities=kf._effective_grants(held),
        checkpointer=None,
        source_ids=held,
    )
    narrowed = [m for m in built.middleware if type(m).__name__ == "NarrowedSkills"]
    advertised = {name for m in narrowed for name in m._allowed}

    assert not any(name.endswith("::audit") for name in advertised)
    assert any(name.endswith("::review") for name in advertised)


def skills_withheld(kf, held: tuple[str, ...], granted: tuple[str, ...]) -> tuple[str, ...]:
    """The skills a caller holding `held` is told a request granting `granted` left out."""
    from kingfisher.application.reporting import withheld_by_kind

    grants = replace(kf._effective_grants(held), skills=granted)
    graph = kf._graph_for(
        Request(task="t", agent="skilled"),
        session_at(kf, "withheld-" + "-".join(held)),
        capabilities=grants,
        checkpointer=None,
        source_ids=held,
    ).graph
    report = withheld_by_kind(
        grants,
        kf.cfg,
        graph,
        kf.catalogue,
        agent=kf.agent_named("skilled", source_ids=held),
        held=kf.held_for(held),
    )
    return dict(report).get("skill", ())


def test_a_skill_out_of_reach_is_not_reported_as_withheld(with_skills):
    """The skills row went unfiltered after skills gained an audience, so a caller who
    could not reach `audit` was told a run had withheld it -- naming the one skill their
    source ids exist to hide.
    """
    kf = Kingfisher(with_skills, backend=default_backend)

    assert "audit" not in skills_withheld(kf, ("B",), ("review",))


def test_a_skill_in_reach_is_still_reported_when_the_request_left_it_out(with_skills):
    """The control, from the same caller: `B` cannot reach `audit` but can reach
    `review`, so a request granting neither is told about `review` and only that. Asked
    of a caller with something hidden, because for one who reaches everything the filter
    never runs, and a report that hid every skill would pass.
    """
    kf = Kingfisher(with_skills, backend=default_backend)

    assert skills_withheld(kf, ("B",), ()) == ("review",)


def test_a_skill_audience_written_qualified_hides_the_bare_name_too(with_skills):
    """The agent file may spell a skill `catalogue::audit` while the listing says `audit`.
    Compared as written, the audience would hide nothing.
    """
    agent = with_skills.catalogue_roots["agents"] / "skilled.yaml"
    agent.write_text(
        agent.read_text(encoding="utf-8").replace("- name: audit", "- name: catalogue::audit"),
        encoding="utf-8",
    )
    kf = Kingfisher(with_skills, backend=default_backend)

    assert "audit" not in skills_withheld(kf, ("B",), ("review",))
    assert "audit" in skills_withheld(kf, ("A",), ("review",))


def test_a_caller_the_audience_admits_is_told_about_both(with_skills):
    """So the assertion above is not passing because nothing was advertised."""
    kf = Kingfisher(with_skills, backend=default_backend)
    held = ("A",)
    built = kf._graph_for(
        Request(task="t", agent="skilled"),
        session_at(kf, "sk2"),
        capabilities=kf._effective_grants(held),
        checkpointer=None,
        source_ids=held,
    )
    narrowed = [m for m in built.middleware if type(m).__name__ == "NarrowedSkills"]
    advertised = {name for m in narrowed for name in m._allowed}

    assert any(name.endswith("::audit") for name in advertised)
    assert any(name.endswith("::review") for name in advertised)
