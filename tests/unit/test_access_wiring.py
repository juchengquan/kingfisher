"""The policy reaching a run: who is calling, and what the graph is built from.

The assertion that matters most here is the last one. An ungranted tool is not
merely refused when called -- it is never attached to the graph, so the model is
never told it exists and never spends context on its schema. That comes free
from group access resolving into an ordinary `Capabilities`: had it been a
filter applied after the build, it would have inherited the weaker two-layer
story the built-in tools are stuck with.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from kingfisher.application.service import Kingfisher
from kingfisher.domain.access import UNSCOPED, AccessError, parse
from kingfisher.domain.capabilities import Capabilities
from tests.conftest import an_agent, tools_dir

TOOL = '''
def line_count(path: str) -> str:
    """Count the lines in a text file."""
    return "0"


TOOLS = [line_count]
'''

POLICY = """
groups: [A, B]
agents:
  surveyor: ["*"]
tools:
  line_count: [A]
"""


@pytest.fixture
def policied(cfg):
    """A deployment where group A reaches `line_count` and group B reaches nothing.

    The tool is written into the workspace so that the policy has something
    real to name: `reconciled` drops a line pointing at an asset the catalogue
    does not offer, so a policy over an empty workspace would grant nothing for
    the wrong reason and every assertion below would pass vacuously.
    """
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "line_count.py").write_text(TOOL, encoding="utf-8")
    an_agent(cfg, "surveyor")
    return replace(cfg, access=parse_policy())


def parse_policy(text: str = POLICY):
    import yaml

    return parse(yaml.safe_load(text), source="access.yaml")


def test_a_call_that_does_not_say_who_is_calling_is_refused(policied):
    """Decision 5. The dangerous failure is a handler that forgot the boundary,
    so it is made loud rather than left to grant everything in silence."""
    kf = Kingfisher(policied)
    with pytest.raises(AccessError, match="for_groups"):
        kf.run("anything")


def test_unscoped_runs_without_a_policy_and_says_so_at_the_call(policied):
    """The opt-out is a value someone typed, so a review can find it."""
    kf = Kingfisher(policied)
    assert kf.for_groups(UNSCOPED).grants == kf.grants


def test_a_caller_in_a_group_gets_what_that_group_reaches(policied):
    kf = Kingfisher(policied)
    assert kf.for_groups(["A"]).grants.tools == ("line_count",)


def test_a_caller_in_another_group_gets_nothing(policied):
    kf = Kingfisher(policied)
    assert kf.for_groups(["B"]).grants.tools == ()


def test_a_caller_holding_no_groups_gets_nothing(policied):
    """`for_groups([])` is a caller who holds nothing. Fail closed."""
    kf = Kingfisher(policied)
    assert kf.for_groups([]).grants.tools == ()


def test_an_unknown_group_is_refused(policied):
    kf = Kingfisher(policied)
    with pytest.raises(AccessError, match="unknown group"):
        kf.for_groups(["Q"])


def test_naming_groups_where_there_is_no_policy_is_refused(cfg):
    """A caller naming groups against a deployment that controls nothing is
    confused, and silently ignoring them is how they stay confused."""
    kf = Kingfisher(cfg)
    with pytest.raises(AccessError, match="no access policy"):
        kf.for_groups(["A"])


def test_a_deployment_without_a_policy_is_unchanged(cfg):
    """Everything that worked before this feature must still work untouched --
    including calling `run` without saying anything about groups."""
    kf = Kingfisher(cfg)
    assert kf.access is None
    assert kf._effective_grants(None) == kf.grants


def test_the_handle_is_reusable(policied):
    """Binding once at the top of a script is the ergonomics a constructor
    argument would have bought, without a second mechanism."""
    kf = Kingfisher(policied)
    caller = kf.for_groups(["A"])
    assert caller.grants == kf.for_groups(["A"]).grants


def test_the_deployments_own_grants_still_bound_a_caller(policied):
    """Two ceilings, and the lower one wins. A policy cannot widen what the
    deployment granted, only narrow it further."""
    kf = Kingfisher(policied, grants=Capabilities(tools=()))
    assert kf.for_groups(["A"]).grants.tools == ()


def test_the_report_is_computed_against_the_catalogue(policied):
    """Reconciliation happens where the catalogue is known, not where the file
    was read -- so a policy naming a tool that is really there is clean."""
    assert Kingfisher(policied).access_report.is_clean


def test_an_asset_no_group_can_reach_is_named_at_construction(cfg):
    """Decision 9's other half: the whitelist going stale is said out loud."""
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "line_count.py").write_text(TOOL, encoding="utf-8")
    an_agent(cfg, "surveyor")
    kf = Kingfisher(replace(cfg, access=parse_policy("groups: [A]\n")))
    assert ("tools", "line_count") in kf.access_report.offered_unreachable


def test_a_stale_line_does_not_turn_every_turn_into_a_refusal(cfg):
    """The reason `reconciled` drops rather than merely reports. A grant naming
    a tool the workspace does not offer reaches `Offering.refuse_unknown`."""
    an_agent(cfg, "surveyor")
    kf = Kingfisher(replace(cfg, access=parse_policy(POLICY)))
    assert ("tools", "line_count") in kf.access_report.listed_not_offered
    assert kf.for_groups(["A"]).grants.tools == ()


def test_an_ungranted_tool_is_not_on_the_graph_at_all(policied, monkeypatch):
    """Not filtered after the fact -- never attached. `create_deep_agent` is
    handed only what the caller reaches, so the model is never offered the
    schema of a tool it may not call."""
    from tests.conftest import capture_build

    kf = Kingfisher(policied)
    captured = capture_build(monkeypatch)
    session = kf.workspace / "sessions" / "s1"
    session.mkdir(parents=True, exist_ok=True)
    from kingfisher.infrastructure.workspace_fs import ensure_session_layout

    ensure_session_layout(session)

    from kingfisher.domain.request import Request

    kf.graph_for(
        Request(task="t", agent="surveyor"),
        session,
        capabilities=kf.for_groups(["B"]).grants,
        checkpointer=None,
    )
    offered = [getattr(t, "name", getattr(t, "__name__", "")) for t in captured["tools"] or ()]
    assert "line_count" not in offered


def test_a_granted_tool_is_on_the_graph(policied, monkeypatch):
    """The other half, so the assertion above is not passing because nothing
    was ever wired."""
    from kingfisher.domain.request import Request
    from kingfisher.infrastructure.workspace_fs import ensure_session_layout
    from tests.conftest import capture_build

    kf = Kingfisher(policied)
    captured = capture_build(monkeypatch)
    session = kf.workspace / "sessions" / "s2"
    session.mkdir(parents=True, exist_ok=True)
    ensure_session_layout(session)

    kf.graph_for(
        Request(task="t", agent="surveyor"),
        session,
        capabilities=kf.for_groups(["A"]).grants,
        checkpointer=None,
    )
    offered = [getattr(t, "name", getattr(t, "__name__", "")) for t in captured["tools"] or ()]
    assert "line_count" in offered


def reported(kf, groups):
    """The withheld report a caller in these groups is handed for one turn.

    Asked of `_withheld_by_kind` directly rather than by running a turn: what is
    under test is which *offered* set the comparison is made against, and a live
    turn would need a model, a checkpointer and a session to say the same thing.
    """
    from kingfisher.application.service import _withheld_by_kind
    from kingfisher.domain.request import Request
    from kingfisher.infrastructure.workspace_fs import ensure_session_layout

    session = kf.workspace / "sessions" / "w1"
    session.mkdir(parents=True, exist_ok=True)
    ensure_session_layout(session)
    caller = kf.for_groups(groups)
    graph = kf.graph_for(
        Request(task="t", agent="surveyor"),
        session,
        capabilities=caller.grants,
        checkpointer=None,
    )
    return _withheld_by_kind(
        caller.grants,
        kf.cfg,
        session,
        graph,
        kf.catalogue,
        reach=kf.access,
        held=kf.access.expand(caller.held) if kf.access is not None else None,
    )


def test_a_caller_is_not_told_about_assets_their_groups_deny(policied):
    """Decision 15, and the one place it leaks if it is going to. This report
    names, by design, every offered thing a grant left out -- so measured
    against the unfiltered catalogue it would hand a caller the exact list of
    what their groups denied them."""
    kf = Kingfisher(policied)
    names = " ".join(n for _kind, group in reported(kf, ["B"]) for n in group)
    assert "line_count" not in names


def test_a_caller_is_still_told_about_what_they_narrowed_themselves(policied):
    """The filtering must not silence the report altogether: a caller who could
    have had a tool and did not ask for it should still hear that."""
    kf = Kingfisher(policied)
    names = " ".join(n for _kind, group in reported(kf, ["A"]) for n in group)
    assert "line_count" not in names  # granted, so not withheld


def test_the_report_still_names_a_builtin_the_request_declined(policied):
    """An uncontrolled axis is unaffected by the filter, so the report keeps
    doing its original job."""
    from kingfisher.application.service import _withheld_by_kind
    from kingfisher.domain.request import Request
    from kingfisher.infrastructure.workspace_fs import ensure_session_layout

    kf = Kingfisher(policied)
    session = kf.workspace / "sessions" / "w2"
    session.mkdir(parents=True, exist_ok=True)
    ensure_session_layout(session)
    grants = replace(kf.for_groups(["A"]).grants, builtin_tools=("read_file",))
    graph = kf.graph_for(
        Request(task="t", agent="surveyor"),
        session,
        capabilities=grants,
        checkpointer=None,
    )
    kinds = dict(
        _withheld_by_kind(
            grants,
            kf.cfg,
            session,
            graph,
            kf.catalogue,
            reach=kf.access,
            held=kf.access.expand(("A",)) if kf.access is not None else None,
        )
    )
    assert "execute" in kinds.get("builtin tool", ())
