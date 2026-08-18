"""A delegate that consults another delegate.

The format refused this until now, on the stated grounds that "deepagents gives
it no `task` tool, so nesting is not something this format can express". Half
right: `create_sub_agent` does call `create_agent` with the spec's tools and no
`task`. But a spec carries `middleware`, and `SubAgentMiddleware` is precisely
what supplies `task` to the main agent -- so the format could express it all
along, through the one field it already had.

It nests to any depth now. What stops a catalogue building forever is
`refuse_cycles`, checked over the whole catalogue at load -- there is no depth
bound, because with each definition compiled once rather than once per path,
depth costs nothing to allow.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from kingfisher.domain.capabilities import ALL, Capabilities, CapabilityError
from kingfisher.domain.subagent import SubagentError, refuse_cycles
from kingfisher.infrastructure.definitions import read_subagent
from kingfisher.infrastructure.harness.agent import build_agent
from tests.conftest import FakeToolCallingModel, subagents_dir

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
    from tests.test_delegation_ceiling import _subagent_graphs

    return _subagent_graphs(graph)[name]


def _helper(graph, delegate: str, name: str):
    """A delegate's *helper*, which is a different instance from the delegate
    of the same name the agent holds directly.

    Worth its own accessor because reaching for the top-level one instead is an
    easy mistake that passes: a standalone `second-opinion` has no helpers and
    no `task` either, so asserting against it proves nothing. A mutation that
    handed every helper the parent's `task` went undetected until this existed.
    """
    from tests.test_delegation_ceiling import _subagent_graphs

    return _subagent_graphs(_delegate(graph, delegate))[name]


def _delegates_of(graph) -> set[str]:
    """The delegates this compiled agent can reach, by name.

    Read off the compiled graphs rather than the spec that asked for them: a
    spec carrying `middleware` proves what was requested, and the whole question
    at depth is whether the level below was actually built.
    """
    from tests.test_delegation_ceiling import _subagent_graphs

    return set(_subagent_graphs(graph))


def _tools_of(graph) -> set[str]:
    node = getattr(graph, "nodes", {}).get("tools")
    by_name = getattr(getattr(node, "bound", None), "tools_by_name", {})
    return set(by_name)


# -- the format ------------------------------------------------------------


def test_a_definition_may_name_delegates(tmp_path):
    """It was refused, with a reason that turned out to be wrong about what the
    format could express."""
    spec = read_subagent(REVIEWER, tmp_path / "reviewer.yaml")

    assert spec.subagents == ("second-opinion",)


def test_naming_none_is_the_default(tmp_path):
    """Like `skills` and unlike `tools`: a delegate that needed the whole
    catalogue would not have been worth defining."""
    spec = read_subagent(HELPER, tmp_path / "second-opinion.yaml")

    assert spec.subagents is None


# -- one level, structurally ----------------------------------------------


def test_a_helper_may_have_helpers_of_its_own(cfg, session_dir):
    """Three levels, which the format refused until now. `reviewer` consults
    `second-opinion`, which consults `checker`."""
    _define(cfg, REVIEWER, NESTING_HELPER, CHECKER)

    graph = _build(cfg, session_dir, subagents=("reviewer", "second-opinion", "checker"))
    helper = _helper(graph, "reviewer", "second-opinion")

    assert "checker" in _delegates_of(helper)


def test_a_cycle_is_refused_when_the_catalogue_loads(cfg, session_dir):
    """The only thing left standing between a catalogue and an endless build,
    now that depth is unbounded. Enforced on the definitions rather than on a
    request, because a set of files is either coherent or it is not."""
    _define(cfg, REVIEWER, CYCLIC_HELPER)

    with pytest.raises(SubagentError, match="reach themselves"):
        _build(cfg, session_dir)


def test_the_refusal_names_the_whole_loop(cfg, session_dir):
    """One edge does not say which link to cut, and whoever reads this may own
    none of the files in it."""
    _define(cfg, REVIEWER, CYCLIC_HELPER)

    with pytest.raises(SubagentError) as raised:
        _build(cfg, session_dir)

    assert "reviewer -> second-opinion -> reviewer" in str(raised.value)


def test_a_definition_reached_twice_is_not_a_cycle():
    """The distinction the check exists to draw. A diamond reaches `checker` by
    two routes and is perfectly coherent; a loop reaches a name already on the
    path being walked. Testing `seen` before the path is what made an earlier
    version of this pass every cycle."""
    specs = {
        "reviewer": read_subagent(REVIEWER, Path("reviewer.yaml")),
        "second-opinion": read_subagent(NESTING_HELPER, Path("second-opinion.yaml")),
        "checker": read_subagent(CHECKER, Path("checker.yaml")),
    }

    refuse_cycles(specs)  # no raise


def test_a_definition_naming_itself_is_a_cycle():
    """The one-node loop, which a check written around pairs would miss."""
    body = HELPER.replace("system_prompt:", "subagents: [second-opinion]\nsystem_prompt:")

    with pytest.raises(SubagentError, match="second-opinion -> second-opinion"):
        refuse_cycles({"second-opinion": read_subagent(body, Path("second-opinion.yaml"))})


def test_a_helper_is_built_without_a_task_tool(cfg, session_dir):
    """The depth bound is a call that is not made, so this is what proves it:
    the helper holds no `task`, so it could not delegate even if it tried.

    Asked of the helper *inside* `reviewer`, not the standalone delegate of the
    same name -- that one has no helpers either, so it would pass whatever this
    change did.
    """
    _define(cfg, REVIEWER, HELPER)

    graph = _build(cfg, session_dir)

    assert "task" not in _tools_of(_helper(graph, "reviewer", "second-opinion"))


def test_a_helper_is_not_handed_the_parents_task_tool(cfg, session_dir):
    """The harvested `task` is bound to the *parent's* delegate list.

    So handing it down would not merely give a helper delegation -- it would
    give it every delegate the agent itself can reach, one level below where
    anyone is looking. Excluding it is a rule, not tidiness.
    """
    _define(cfg, REVIEWER, HELPER)

    graph = _build(cfg, session_dir)

    assert "task" not in _tools_of(_helper(graph, "reviewer", "second-opinion"))
    assert "read_file" in _tools_of(_helper(graph, "reviewer", "second-opinion"))


# -- the delegate can actually delegate ------------------------------------


def test_a_delegate_that_names_a_helper_gets_a_task_tool(cfg, session_dir):
    """What the whole change is for. Measured on the compiled graph rather than
    on the spec, because a spec that looks right and compiles to an agent with
    no `task` is exactly the failure this replaces.
    """
    _define(cfg, REVIEWER, HELPER)

    graph = _build(cfg, session_dir)

    assert "task" in _tools_of(_delegate(graph, "reviewer"))


def test_a_delegate_that_names_none_gets_no_task_tool(cfg, session_dir):
    """Unchanged for every delegate that does not ask, which is all of them
    until someone writes the line."""
    _define(cfg, HELPER)

    graph = _build(cfg, session_dir, subagents=("second-opinion",))

    assert "task" not in _tools_of(_delegate(graph, "second-opinion"))


# -- the caller decides ----------------------------------------------------


def test_a_helper_the_caller_did_not_name_is_dropped(cfg, session_dir):
    """Not refused. `second-opinion` runs on another company's servers, so a
    caller declining it is often declining *that* -- and refusing would mean
    nobody can use `reviewer` without also accepting OpenAI."""
    _define(cfg, REVIEWER, HELPER)

    graph = _build(cfg, session_dir, subagents=("reviewer",))

    assert "task" not in _tools_of(_delegate(graph, "reviewer"))


def test_a_helper_nothing_defines_is_refused(cfg, session_dir):
    """The other half of the rule every field here follows: a name nothing
    defines is a mistake in the definition, not a narrower caller."""
    _define(cfg, REVIEWER.replace("second-opinion", "nobody"))

    with pytest.raises(CapabilityError, match="unknown subagent"):
        _build(cfg, session_dir, subagents=("reviewer",))


def test_granting_everything_reaches_the_helper_too(cfg, session_dir):
    """`ALL` is every delegate the workspace defines, so a caller who narrowed
    nothing has named the helper as much as anything else."""
    _define(cfg, REVIEWER, HELPER)

    graph = _build(cfg, session_dir, subagents=ALL)

    assert "task" in _tools_of(_delegate(graph, "reviewer"))


# -- and it actually runs --------------------------------------------------


def test_a_delegate_consults_its_helper_end_to_end(cfg, session_dir):
    """Building is not running, and the two have come apart before.

    One scripted model serves every level, so the responses are consumed in
    call order and the order itself is the assertion: parent delegates,
    reviewer delegates again, the helper answers, and each summary travels back
    up. If the helper never ran, `reviewer` would still be on its first reply.
    """
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

    That was sufficient while nothing below the top held `task`. It nests now,
    so the question is live: deepagents supplies a `general-purpose` delegate
    "with the same capabilities as the main agent" wherever it builds one, and
    a nested agent holding an unrestricted delegate would hand back everything
    the request withheld, one level down where nothing is watching.

    Measured rather than reasoned: `create_deep_agent` adds it, and
    `SubAgentMiddleware` does not. So the backstop stays where it is -- and this
    is what fails if an upgrade changes that.
    """
    _define(cfg, REVIEWER, NESTING_HELPER, CHECKER)

    graph = _build(cfg, session_dir, subagents=("reviewer", "second-opinion", "checker"))
    nested = _delegate(graph, "reviewer")

    assert "task" in _tools_of(nested), "a delegate with helpers needs task to reach them"
    assert "general-purpose" not in _delegates_of(nested)


def test_a_definition_is_compiled_once_for_each_position(cfg, session_dir, monkeypatch):
    """Once per definition, not once per path -- the difference between linear
    and exponential, and the thing that makes reuse affordable.

    Twice rather than once because a top-level delegate and a nested one are
    not the same agent: the first inherits its model and tools, the second is
    refused by deepagents without them.
    """
    shared = HELPER.replace("second-opinion", "shared")
    left = REVIEWER.replace("reviewer", "left").replace("second-opinion", "shared")
    right = REVIEWER.replace("reviewer", "right").replace("second-opinion", "shared")
    _define(cfg, left, right, shared)

    from kingfisher.infrastructure.harness import delegation

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
    return read_subagent(body, Path(f"{name}.yaml"))


def test_a_definition_that_consults_everything_is_a_loop():
    """`subagents: ['*']` is every definition in the catalogue, which includes
    the one saying it -- so it is always a cycle, and was always missed.

    `refuse_cycles` read `*` as *no* edges while `subagent_helpers` expands it to
    `tuple(defined)`. The two disagreeing is the whole bug: the catalogue passed
    the walk and `_with_helpers` then recursed into itself until the interpreter
    stopped it. That function has no re-entry guard and a comment saying it needs
    none "because `refuse_cycles` already ran".
    """
    with pytest.raises(SubagentError, match="reach themselves"):
        refuse_cycles({"greedy": _spec("greedy", '["*"]')})


def test_the_refusal_says_where_the_edge_came_from():
    """Whoever wrote `['*']` never typed the name in the loop, so a message that
    only prints `greedy -> greedy` sends them looking for an edge they cannot
    find."""
    with pytest.raises(SubagentError, match=r"names every subagent with `\*`"):
        refuse_cycles({"greedy": _spec("greedy", '["*"]')})


def test_the_star_reaches_the_others_too_not_only_itself():
    """`*` is every definition, so it is also an edge to each of them -- a loop
    running through a second delegate is caught for the same reason."""
    specs = {
        "hub": _spec("hub", '["*"]'),
        "spoke": _spec("spoke", "[hub]"),
    }

    with pytest.raises(SubagentError, match="reach themselves"):
        refuse_cycles(specs)


def test_a_catalogue_without_a_star_is_untouched():
    """The fix widens what counts as an edge, so the case it must not break is
    an ordinary chain: `a` consults `b` consults `c`, which N1 exists to allow."""
    specs = {
        "a": _spec("a", "[b]"),
        "b": _spec("b", "[c]"),
        "c": _spec("c"),
    }

    refuse_cycles(specs)  # no raise


def test_a_diamond_is_not_a_cycle():
    """A definition reached twice by different paths is a DAG, which N2 allows
    deliberately. Reading `seen` before `on_path` would call this a loop."""
    specs = {
        "top": _spec("top", "[left, right]"),
        "left": _spec("left", "[shared]"),
        "right": _spec("right", "[shared]"),
        "shared": _spec("shared"),
    }

    refuse_cycles(specs)  # no raise
