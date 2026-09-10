"""A delegate that consults another delegate."""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from kingfisher.domain.capabilities import ALL, Capabilities, CapabilityError
from kingfisher.infrastructure.harness.agent import build_agent
from kingfisher.kinds.subagents import reading
from kingfisher.kinds.subagents.rules import refuse_cycles
from kingfisher.kinds.subagents.spec import SubagentError, SubagentSpec
from tests.conftest import FakeToolCallingModel, capture_build, subagents_dir

REVIEWER = """name: reviewer
description: Checks figures.
subagents: [second-opinion]
system_prompt: |
  You check figures, and may ask for a second opinion.
"""

HELPER = """name: second-opinion
description: Answers again, elsewhere.
system_prompt: |
  You answer on your own.
"""


#: The same helper, naming `reviewer` back. Legal shape, illegal graph: this is
#: the two-cycle, and the only thing between it and an endless build.
CYCLIC_HELPER = HELPER.replace("system_prompt:", "subagents: [reviewer]\nsystem_prompt:")

#: A third level. `reviewer` consults `second-opinion`, which consults `checker`.
CHECKER = """name: checker
description: Checks the checker.
system_prompt: |
  You are the last word.
"""

NESTING_HELPER = HELPER.replace("system_prompt:", "subagents: [checker]\nsystem_prompt:")

#: A parent that pinned a model, above a helper that named none. The pair is
#: the whole question of what "no model" means one level down.
CHEAP_REVIEWER = REVIEWER.replace("subagents:", "model: cheap-model\nsubagents:")

#: A second parent naming the same helper, on a different model. The pair is
#: what makes a helper's identity more than its name.
ELSEWHERE_REVIEWER = REVIEWER.replace("name: reviewer", "name: auditor").replace(
    "subagents:", "model: elsewhere-model\nsubagents:"
)


def _define(cfg, *definitions: str) -> None:
    directory = subagents_dir(cfg)
    directory.mkdir(parents=True, exist_ok=True)
    for body in definitions:
        name = body.split("\n")[0].removeprefix("name: ").strip()
        (directory / f"{name}.yaml").write_text(body, encoding="utf-8")


def _build(cfg, session_dir, *, subagents=("reviewer", "second-opinion")):
    return build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=subagents),
    )


def _delegate(graph, name: str):
    """One of the agent's own delegates, compiled."""
    from tests.unit.test_delegation_ceiling import _subagent_graphs

    return _subagent_graphs(graph)[name]


def _helper(graph, delegate: str, name: str):
    """A delegate's *helper*, which is a different instance from the delegate of the
    same name the agent holds directly.

    Worth its own accessor because reaching for the top-level one instead is an easy
    mistake that passes: a standalone `second-opinion` has no helpers and no `task`
    either, so asserting against it proves nothing. A mutation that handed every
    helper the parent's `task` went undetected until this existed.
    """
    from tests.unit.test_delegation_ceiling import _subagent_graphs

    return _subagent_graphs(_delegate(graph, delegate))[name]


def _delegates_of(graph) -> set[str]:
    """The delegates this compiled agent can reach, by name."""
    from tests.unit.test_delegation_ceiling import _subagent_graphs

    return set(_subagent_graphs(graph))


def _tools_of(graph) -> set[str]:
    node = getattr(graph, "nodes", {}).get("tools")
    by_name = getattr(getattr(node, "bound", None), "tools_by_name", {})
    return set(by_name)


# -- the format ------------------------------------------------------------


def test_a_definition_may_name_delegates(tmp_path):
    """It was refused, with a reason that turned out to be wrong about what the format
    could express.
    """
    spec = reading.read(REVIEWER, tmp_path / "reviewer.yaml")

    assert spec.subagents == ("second-opinion",)


def test_naming_none_is_the_default(tmp_path):
    """Like `skills` and unlike `tools`: a delegate that needed the whole catalogue
    would not have been worth defining.
    """
    spec = reading.read(HELPER, tmp_path / "second-opinion.yaml")

    assert spec.subagents is None


# -- one level, structurally ----------------------------------------------


def test_a_helper_may_have_helpers_of_its_own(cfg, session_dir):
    """Three levels, which the format refused until now."""
    _define(cfg, REVIEWER, NESTING_HELPER, CHECKER)

    graph = _build(cfg, session_dir, subagents=("reviewer", "second-opinion", "checker"))
    helper = _helper(graph, "reviewer", "second-opinion")

    assert "checker" in _delegates_of(helper)


def test_a_cycle_is_refused_when_the_catalogue_loads(cfg, session_dir):
    """The only thing left standing between a catalogue and an endless build, now that
    depth is unbounded.
    """
    _define(cfg, REVIEWER, CYCLIC_HELPER)

    with pytest.raises(SubagentError, match="reach themselves"):
        _build(cfg, session_dir)


def test_the_refusal_names_the_whole_loop(cfg, session_dir):
    """One edge does not say which link to cut, and whoever reads this may own none of
    the files in it.
    """
    _define(cfg, REVIEWER, CYCLIC_HELPER)

    with pytest.raises(SubagentError) as raised:
        _build(cfg, session_dir)

    assert "reviewer -> second-opinion -> reviewer" in str(raised.value)


def test_a_definition_reached_twice_is_not_a_cycle():
    """The distinction the check exists to draw."""
    specs = {
        "reviewer": reading.read(REVIEWER, Path("reviewer.yaml")),
        "second-opinion": reading.read(NESTING_HELPER, Path("second-opinion.yaml")),
        "checker": reading.read(CHECKER, Path("checker.yaml")),
    }

    refuse_cycles(specs)  # no raise


def test_a_definition_naming_itself_is_a_cycle():
    """The one-node loop, which a check written around pairs would miss."""
    body = HELPER.replace("system_prompt:", "subagents: [second-opinion]\nsystem_prompt:")

    with pytest.raises(SubagentError, match="second-opinion -> second-opinion"):
        refuse_cycles({"second-opinion": reading.read(body, Path("second-opinion.yaml"))})


def test_a_helper_is_built_without_a_task_tool(cfg, session_dir):
    """The depth bound is a call that is not made, so this is what proves it: the helper
    holds no `task`, so it could not delegate even if it tried.

    Asked of the helper *inside* `reviewer`, not the standalone delegate of the same
    name -- that one has no helpers either, so it would pass whatever this change did.
    """
    _define(cfg, REVIEWER, HELPER)

    graph = _build(cfg, session_dir)

    assert "task" not in _tools_of(_helper(graph, "reviewer", "second-opinion"))


def test_a_helper_is_not_handed_the_parents_task_tool(cfg, session_dir):
    """The harvested `task` is bound to the *parent's* delegate list."""
    _define(cfg, REVIEWER, HELPER)

    graph = _build(cfg, session_dir)

    assert "task" not in _tools_of(_helper(graph, "reviewer", "second-opinion"))
    assert "read_file" in _tools_of(_helper(graph, "reviewer", "second-opinion"))


# -- the delegate can actually delegate ------------------------------------


def test_a_delegate_that_names_a_helper_gets_a_task_tool(cfg, session_dir):
    """What the whole change is for."""
    _define(cfg, REVIEWER, HELPER)

    graph = _build(cfg, session_dir)

    assert "task" in _tools_of(_delegate(graph, "reviewer"))


def test_a_delegate_that_names_none_gets_no_task_tool(cfg, session_dir):
    """Unchanged for every delegate that does not ask, which is all of them until
    someone writes the line.
    """
    _define(cfg, HELPER)

    graph = _build(cfg, session_dir, subagents=("second-opinion",))

    assert "task" not in _tools_of(_delegate(graph, "second-opinion"))


# -- the caller decides ----------------------------------------------------


def test_a_helper_the_caller_did_not_name_is_dropped(cfg, session_dir):
    """Not refused."""
    _define(cfg, REVIEWER, HELPER)

    graph = _build(cfg, session_dir, subagents=("reviewer",))

    assert "task" not in _tools_of(_delegate(graph, "reviewer"))


def test_a_helper_nothing_defines_is_refused(cfg, session_dir):
    """The other half of the rule every field here follows: a name nothing defines is a
    mistake in the definition, not a narrower caller.
    """
    _define(cfg, REVIEWER.replace("second-opinion", "nobody"))

    with pytest.raises(CapabilityError, match="unknown subagent"):
        _build(cfg, session_dir, subagents=("reviewer",))


def test_granting_everything_reaches_the_helper_too(cfg, session_dir):
    """`ALL` is every delegate the workspace defines, so a caller who narrowed nothing
    has named the helper as much as anything else.
    """
    _define(cfg, REVIEWER, HELPER)

    graph = _build(cfg, session_dir, subagents=ALL)

    assert "task" in _tools_of(_delegate(graph, "reviewer"))


# -- and it actually runs --------------------------------------------------


def test_a_delegate_consults_its_helper_end_to_end(cfg, session_dir):
    """Building is not running, and the two have come apart before."""
    _define(cfg, REVIEWER, HELPER)

    def calls(who: str) -> AIMessage:
        return AIMessage(
            content="",
            tool_calls=[
                {"name": "task", "args": {"description": "go", "subagent_type": who}, "id": who}
            ],
        )

    graph = build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(
            responses=[
                calls("reviewer"),
                calls("second-opinion"),
                AIMessage(content="SECOND-OPINION-ANSWERED"),
                AIMessage(content="REVIEWER-SUMMARISED"),
                AIMessage(content="PARENT-DONE"),
            ]
        ),
        capabilities=Capabilities(subagents=("reviewer", "second-opinion")),
    )

    out = graph.invoke(
        {"messages": [{"role": "user", "content": "go"}]}, {"recursion_limit": 20}
    )
    transcript = "\n".join(str(getattr(m, "content", "")) for m in out["messages"])

    assert "REVIEWER-SUMMARISED" in transcript  # the delegate reported back
    assert "PARENT-DONE" in transcript
    # The helper's own words stayed inside the delegate, which is the property
    # that makes a helper worth having rather than just another tool call.
    assert "SECOND-OPINION-ANSWERED" not in transcript


# -- what nesting must not have loosened ------------------------------------


def test_a_nested_agent_gets_no_unrestricted_delegate(cfg, session_dir):
    """`DeclaredDelegatesOnly` is applied to the main agent and nowhere else.

    Measured rather than reasoned: `create_deep_agent` adds it, and
    `SubAgentMiddleware` does not. So the backstop stays where it is -- and this is
    what fails if an upgrade changes that.
    """
    _define(cfg, REVIEWER, NESTING_HELPER, CHECKER)

    graph = _build(cfg, session_dir, subagents=("reviewer", "second-opinion", "checker"))
    nested = _delegate(graph, "reviewer")

    assert "task" in _tools_of(nested), "a delegate with helpers needs task to reach them"
    assert "general-purpose" not in _delegates_of(nested)


def test_a_definition_is_compiled_once_for_each_position(cfg, session_dir, monkeypatch):
    """Once per definition, not once per path -- the difference between linear and
    exponential, and the thing that makes reuse affordable.
    """
    shared = HELPER.replace("second-opinion", "shared")
    left = REVIEWER.replace("reviewer", "left").replace("second-opinion", "shared")
    right = REVIEWER.replace("reviewer", "right").replace("second-opinion", "shared")
    _define(cfg, left, right, shared)

    from kingfisher.kinds.subagents import harness as delegation

    built: list[str] = []
    real = delegation.as_subagent
    monkeypatch.setattr(
        delegation,
        "as_subagent",
        lambda spec, *a, **k: (built.append(spec.name), real(spec, *a, **k))[1],
    )
    monkeypatch.setattr("kingfisher.infrastructure.harness.agent.as_subagent",
                        delegation.as_subagent)

    _build(cfg, session_dir, subagents=("left", "right", "shared"))

    assert built.count("shared") == 2, (
        f"`shared` is reached by two parents and activated directly; it should "
        f"compile once per position, got {built}"
    )


# -- the star is an edge, not an absence -----------------------------------


def _spec(name, subagents=None):
    body = f"name: {name}\ndescription: A delegate.\nsystem_prompt: |\n  x\n"
    if subagents is not None:
        body += f"subagents: {subagents}\n"
    return reading.read(body, Path(f"{name}.yaml"))


def test_a_definition_may_not_ask_for_every_delegate():
    """The refusal that makes the rest of this moot: `subagents` must be named."""
    with pytest.raises(SubagentError, match=r"subagents may not be"):
        _spec("greedy", '["*"]')


def test_the_refusal_says_why_rather_than_only_no():
    """Whoever wrote `['*']` was copying the habit from a request, where it is the
    ordinary way to say everything.
    """
    with pytest.raises(SubagentError, match="includes this one"):
        _spec("greedy", '["*"]')


def test_tools_still_takes_a_star():
    """The refusals are two fields, not a change to the format.

    This asserted `skills: ["*"]` parsed too, on the reasoning that the
    `subagents` refusal was scoped to one field. It was, until the star on
    `skills` was measured rather than assumed -- see the two tests below.
    """
    spec = reading.read(
        "name: broad\ndescription: A delegate.\nsystem_prompt: |\n  x\n"
        'tools: ["*"]\n',
        Path("broad.yaml"),
    )

    assert spec.tools == ALL
    assert spec.skills is None  # unwritten, which is what grants none


def test_a_delegate_may_not_ask_for_every_skill():
    """`skills: ["*"]` read as "all of them" and arrived as none.

    It resolves to whatever the request granted, and a delegate is handed an
    index only where its skills are *named* -- so under the ordinary request,
    which grants every skill, the delegate got no index at all. It worked only
    when the caller happened to narrow skills, which is a meaning no author of
    the file can see.
    """
    body = (
        "name: broad\ndescription: A delegate.\nsystem_prompt: |\n  x\n"
        'skills: ["*"]\n'
    )

    with pytest.raises(SubagentError, match=r"skills may not be"):
        reading.read(body, Path("broad.yaml"))


def test_the_skills_refusal_says_what_to_write_instead():
    """A refusal naming no remedy sends the reader to the source, and the habit it
    came from -- a request, where the star is the ordinary way to say everything --
    is the same one the `subagents` refusal above exists to catch.
    """
    body = (
        "name: broad\ndescription: A delegate.\nsystem_prompt: |\n  x\n"
        'skills: ["*"]\n'
    )

    with pytest.raises(SubagentError, match="Name the procedures this one uses"):
        reading.read(body, Path("broad.yaml"))


def test_the_cycle_walk_still_reads_a_star_as_every_edge():
    """The backstop, and worth keeping now that the parser refuses this."""
    greedy = SubagentSpec(
        name="greedy", description="Consults everything.", system_prompt="x", subagents=ALL
    )

    with pytest.raises(SubagentError, match="reach themselves") as refusal:
        refuse_cycles({"greedy": greedy})

    # The loop a star makes never names itself, so only the message can say why
    # `greedy -> greedy` is one. Asserted here because the clause is the whole
    # reason a reader believes the refusal.
    assert "names every subagent with `*`" in str(refusal.value)


def test_a_loop_written_out_by_name_is_not_blamed_on_a_star():
    """The negative control for the clause above, which would otherwise read as
    always-on and explain a loop the author did write.
    """
    specs = {"a": _spec("a", "[b]"), "b": _spec("b", "[a]")}

    with pytest.raises(SubagentError, match="reach themselves") as refusal:
        refuse_cycles(specs)

    assert "names every subagent" not in str(refusal.value)


def test_a_catalogue_without_a_star_is_untouched():
    """The fix widens what counts as an edge, so the case it must not break is an
    ordinary chain: `a` consults `b` consults `c`, which N1 exists to allow.
    """
    specs = {
        "a": _spec("a", "[b]"),
        "b": _spec("b", "[c]"),
        "c": _spec("c"),
    }

    refuse_cycles(specs)  # no raise


def test_a_diamond_is_not_a_cycle():
    """A definition reached twice by different paths is a DAG, which N2 allows
    deliberately.
    """
    specs = {
        "top": _spec("top", "[left, right]"),
        "left": _spec("left", "[shared]"),
        "right": _spec("right", "[shared]"),
        "shared": _spec("shared"),
    }

    refuse_cycles(specs)  # no raise


def _helper_specs(spec) -> dict:
    """The helper specs kingfisher hung off one delegate, by name."""
    for middleware in spec.get("middleware", []):
        for attribute in ("subagents", "_subagents"):
            if found := getattr(middleware, attribute, None):
                return {helper["name"]: helper for helper in found}
    return {}


def test_a_helper_runs_the_model_of_the_delegate_that_summoned_it(
    cfg, session_dir, monkeypatch
):
    """A definition naming no model runs whatever reached it, and one level down that is
    the delegate above -- not the main agent.
    """
    _define(cfg, CHEAP_REVIEWER, HELPER)
    captured = capture_build(monkeypatch)
    main = FakeToolCallingModel(responses=[AIMessage(content="ok")])

    build_agent(
        cfg,
        session_dir=session_dir,
        model=main,
        capabilities=Capabilities(subagents=("reviewer", "second-opinion")),
    )

    parent = {spec["name"]: spec for spec in captured["subagents"]}["reviewer"]
    helper = _helper_specs(parent)["second-opinion"]

    assert helper["model"] is not main, "the helper inherited the main agent's model"
    assert helper["model"].model == "cheap-model"


def test_one_helper_under_two_parents_is_two_delegates(cfg, session_dir, monkeypatch):
    """A helper naming no model runs whatever reached it, so the same name under two
    differently-pinned parents is two different agents.
    """
    _define(cfg, CHEAP_REVIEWER, ELSEWHERE_REVIEWER, HELPER)
    captured = capture_build(monkeypatch)

    build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=("reviewer", "auditor", "second-opinion")),
    )

    built = {spec["name"]: spec for spec in captured["subagents"]}
    under_cheap = _helper_specs(built["reviewer"])["second-opinion"]
    under_elsewhere = _helper_specs(built["auditor"])["second-opinion"]

    assert under_cheap["model"].model == "cheap-model"
    assert under_elsewhere["model"].model == "elsewhere-model"


def test_a_helper_under_an_unpinned_delegate_still_runs_the_agents_model(
    cfg, session_dir, monkeypatch
):
    """The other half, and the one that must not have changed."""
    _define(cfg, REVIEWER, HELPER)
    captured = capture_build(monkeypatch)
    main = FakeToolCallingModel(responses=[AIMessage(content="ok")])

    build_agent(
        cfg,
        session_dir=session_dir,
        model=main,
        capabilities=Capabilities(subagents=("reviewer", "second-opinion")),
    )

    parent = {spec["name"]: spec for spec in captured["subagents"]}["reviewer"]
    assert _helper_specs(parent)["second-opinion"]["model"] is main
