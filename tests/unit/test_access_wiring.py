"""The definitions' audiences reaching a run: who is calling, and what is built.

The assertion that matters most here is the graph one. An ungranted tool is not
merely refused when called -- it is never attached, so the model is never told
it exists and never spends context on its schema. That comes free from an
audience resolving into an ordinary `Capabilities`: had it been a filter applied
after the build, it would have inherited the weaker two-layer story the built-in
tools are stuck with.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
import yaml

from kingfisher.application.service import Kingfisher
from kingfisher.domain.access import UNSCOPED, AccessError, parse
from kingfisher.domain.capabilities import Capabilities
from kingfisher.domain.request import Request
from kingfisher.infrastructure.workspace_fs import ensure_session_layout
from tests.conftest import an_agent, capture_build, tools_dir

TOOL = '''
def line_count(path: str) -> str:
    """Count the lines in a text file."""
    return "0"


TOOLS = [line_count]
'''

VOCABULARY = "groups: [A, B]\n"

#: `surveyor` is for A and B; its one tool is for A alone. So a caller in B
#: reaches the agent and runs it with nothing -- the compounding case, in one
#: file.
AGENT = """name: surveyor
description: An agent.
groups: [A, B]
tools:
  line_count:
    groups: [A]
system_prompt: |
  You do the task.
"""


def vocabulary(text: str = VOCABULARY):
    return parse(yaml.safe_load(text), source="groups.yaml")


@pytest.fixture
def policied(cfg):
    """A deployment where group A reaches `line_count` through `surveyor`."""
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


def built(kf, monkeypatch, groups, name: str):
    """The tool names handed to `create_deep_agent` for this caller."""
    captured = capture_build(monkeypatch)
    caller = kf.for_groups(groups)
    kf.graph_for(
        Request(task="t", agent="surveyor"),
        session_at(kf, name),
        capabilities=caller.grants,
        checkpointer=None,
        groups=caller.held,
    )
    return [getattr(t, "name", getattr(t, "__name__", "")) for t in captured["tools"] or ()]


# -- who is calling ---------------------------------------------------------


def test_a_call_that_does_not_say_who_is_calling_is_refused(policied):
    """The dangerous failure is a handler that forgot the boundary, so it is
    made loud rather than left to grant everything in silence."""
    kf = Kingfisher(policied)
    with pytest.raises(AccessError, match="for_groups"):
        kf.run("anything")


def test_unscoped_runs_without_a_caller_and_says_so_at_the_call(policied):
    """The opt-out is a value someone typed, so a review can find it."""
    kf = Kingfisher(policied)
    assert kf.for_groups(UNSCOPED).held is UNSCOPED


def test_an_unknown_group_is_refused(policied):
    """The closed vocabulary, from the caller's end: a typo would otherwise
    reach nothing, which looks exactly like a caller who was denied."""
    kf = Kingfisher(policied)
    with pytest.raises(AccessError, match="unknown group"):
        kf.for_groups(["Q"])


def test_naming_groups_where_there_is_no_vocabulary_is_refused(cfg):
    """A caller naming groups against a deployment that declares none is
    confused, and silently ignoring them is how they stay confused."""
    kf = Kingfisher(cfg)
    with pytest.raises(AccessError, match="no access policy"):
        kf.for_groups(["A"])


def test_a_deployment_without_a_vocabulary_is_unchanged(cfg):
    """Everything that worked before this must still work untouched --
    including calling `run` without saying anything about groups."""
    kf = Kingfisher(cfg)
    assert kf.access is None
    assert kf.held_for(None) is None


def test_the_handle_is_reusable(policied):
    kf = Kingfisher(policied)
    assert kf.for_groups(["A"]).grants == kf.for_groups(["A"]).grants


def test_the_deployments_own_grants_still_bound_a_caller(policied):
    """Two ceilings, and the lower one wins."""
    kf = Kingfisher(policied, grants=Capabilities(tools=()))
    assert kf.for_groups(["A"]).grants.tools == ()


# -- what the graph is built from -------------------------------------------


def test_a_caller_the_audience_admits_gets_the_tool(policied, monkeypatch):
    assert "line_count" in built(policied_kf(policied), monkeypatch, ["A"], "s1")


def test_a_caller_the_audience_excludes_does_not(policied, monkeypatch):
    """Not filtered after the fact -- never attached."""
    assert "line_count" not in built(policied_kf(policied), monkeypatch, ["B"], "s2")


def test_unscoped_still_gets_everything(policied, monkeypatch):
    """No caller means no narrowing, which is what keeps `declares(None)` the
    exact answer it was before audiences existed."""
    assert "line_count" in built(policied_kf(policied), monkeypatch, UNSCOPED, "s3")


def policied_kf(cfg):
    return Kingfisher(cfg)


# -- the report -------------------------------------------------------------


def test_a_definition_with_no_groups_line_is_named(cfg):
    """Default-open must not also be silent."""
    an_agent(cfg, "assistant")
    kf = Kingfisher(replace(cfg, access=vocabulary()))
    assert ("agent", "assistant") in kf.access_report.unrestricted


def test_a_definition_that_restricts_is_not_named(policied):
    kf = Kingfisher(policied)
    assert kf.access_report.is_clean


def test_the_report_reads_as_a_sentence(cfg):
    an_agent(cfg, "assistant")
    kf = Kingfisher(replace(cfg, access=vocabulary()))
    rendered = "\n".join(kf.access_report.lines())
    assert "reachable by everyone" in rendered
    assert "assistant" in rendered


# -- what a caller is told --------------------------------------------------


def reported(kf, groups, name: str):
    """The withheld report a caller in these groups is handed for one turn."""
    from kingfisher.application.service import _withheld_by_kind

    session = session_at(kf, name)
    caller = kf.for_groups(groups)
    held = kf.held_for(caller.held)
    graph = kf.graph_for(
        Request(task="t", agent="surveyor"),
        session,
        capabilities=caller.grants,
        checkpointer=None,
        groups=caller.held,
    )
    return _withheld_by_kind(
        caller.grants,
        kf.cfg,
        session,
        graph,
        kf.catalogue,
        agent=kf.agent_named("surveyor", groups=caller.held),
        held=held,
    )


def test_a_caller_is_not_told_about_what_their_groups_took_away(policied):
    """This report names every offered thing a grant left out -- so measured
    against the unfiltered catalogue it would hand a caller the exact list of
    what their groups denied them."""
    kf = Kingfisher(policied)
    names = " ".join(n for _kind, group in reported(kf, ["B"], "w1") for n in group)
    assert "line_count" not in names


def test_the_report_still_names_a_builtin_the_request_declined(policied):
    """An axis no audience controls is unaffected, so the report keeps doing
    its original job."""
    kf = Kingfisher(policied)
    session = session_at(kf, "w2")
    caller = kf.for_groups(["A"])
    grants = replace(caller.grants, builtin_tools=("read_file",))
    graph = kf.graph_for(
        Request(task="t", agent="surveyor"),
        session,
        capabilities=grants,
        checkpointer=None,
        groups=caller.held,
    )
    from kingfisher.application.service import _withheld_by_kind

    kinds = dict(
        _withheld_by_kind(
            grants,
            kf.cfg,
            session,
            graph,
            kf.catalogue,
            agent=kf.agent_named("surveyor", groups=caller.held),
            held=kf.held_for(caller.held),
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
groups: [A, B]
skills:
  audit:
    groups: [A]
  review:
    groups: [A, B]
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
    kf = Kingfisher(with_skills)

    assert kf.agent_named("skilled", groups=UNSCOPED).declares(
        kf.held_for(("A",))
    ).skills == ("audit", "review")
    assert kf.agent_named("skilled", groups=UNSCOPED).declares(
        kf.held_for(("B",))
    ).skills == ("review",)


def test_a_skill_out_of_reach_is_not_advertised_to_the_model(with_skills, monkeypatch):
    """The half a selection alone does not prove. A skill reaches the model
    through a middleware rather than the tool list, so narrowing the grant is
    only half the claim -- this asserts what the agent is actually told about.
    """
    captured = capture_build(monkeypatch)
    kf = Kingfisher(with_skills)
    caller = kf.for_groups(["B"])
    kf.graph_for(
        Request(task="t", agent="skilled"),
        session_at(kf, "sk1"),
        capabilities=caller.grants,
        checkpointer=None,
        groups=caller.held,
    )
    narrowed = [m for m in captured["middleware"] if type(m).__name__ == "NarrowedSkills"]
    advertised = {name for m in narrowed for name in m._allowed}

    assert not any(name.endswith("::audit") for name in advertised)
    assert any(name.endswith("::review") for name in advertised)


def test_a_caller_the_audience_admits_is_told_about_both(with_skills, monkeypatch):
    """So the assertion above is not passing because nothing was advertised."""
    captured = capture_build(monkeypatch)
    kf = Kingfisher(with_skills)
    caller = kf.for_groups(["A"])
    kf.graph_for(
        Request(task="t", agent="skilled"),
        session_at(kf, "sk2"),
        capabilities=caller.grants,
        checkpointer=None,
        groups=caller.held,
    )
    narrowed = [m for m in captured["middleware"] if type(m).__name__ == "NarrowedSkills"]
    advertised = {name for m in narrowed for name in m._allowed}

    assert any(name.endswith("::audit") for name in advertised)
    assert any(name.endswith("::review") for name in advertised)
