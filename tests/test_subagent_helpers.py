"""A delegate that consults another delegate.

The format refused this until now, on the stated grounds that "deepagents gives
it no `task` tool, so nesting is not something this format can express". Half
right: `create_sub_agent` does call `create_agent` with the spec's tools and no
`task`. But a spec carries `middleware`, and `SubAgentMiddleware` is precisely
what supplies `task` to the main agent -- so the format could express it all
along, through the one field it already had.

One level, and the bound is structural: a helper is built by a call that is not
passed helpers of its own. There is no counter and no cycle detection, because
the code that would build a second level never runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from kingfisher.domain.capabilities import ALL, Capabilities, CapabilityError
from kingfisher.domain.subagent import SubagentError, refuse_helpers_with_helpers
from kingfisher.infrastructure.agent import build_agent
from kingfisher.infrastructure.definitions import read_subagent
from tests.conftest import FakeToolCallingModel

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


#: The same helper, but with helpers of its own -- which is what the one-level
#: rule refuses, and the only shape a cycle could be written in.
NESTED_HELPER = HELPER.replace("system_prompt:", "subagents: [reviewer]\nsystem_prompt:")


def _define(cfg, *definitions: str) -> None:
    directory = cfg.subagents_dir
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


def test_a_helper_with_helpers_is_refused_when_the_catalogue_loads(cfg, session_dir):
    """The bound, and where it is enforced: on the definitions, not on a
    request, because a set of files is either coherent or it is not."""
    _define(cfg, REVIEWER, NESTED_HELPER)

    with pytest.raises(SubagentError, match="delegation goes one level"):
        _build(cfg, session_dir)


def test_the_refusal_names_both_files(cfg, session_dir):
    """Whoever reads it may own neither: adding one line to `reviewer.yaml` is
    what made `second-opinion.yaml` invalid, and it was not edited."""
    _define(cfg, REVIEWER, NESTED_HELPER)

    with pytest.raises(SubagentError) as raised:
        _build(cfg, session_dir)

    assert "reviewer" in str(raised.value)
    assert "second-opinion" in str(raised.value)


def test_a_cycle_cannot_be_written_at_all():
    """`reviewer` -> `second-opinion` -> `reviewer` needs a helper with helpers.

    Asserted against the rule directly, because the interesting claim is that
    the shape is unspellable rather than that one instance is caught.
    """
    specs = {
        "reviewer": read_subagent(REVIEWER, Path("reviewer.yaml")),
        "second-opinion": read_subagent(NESTED_HELPER, Path("second-opinion.yaml")),
    }

    with pytest.raises(SubagentError, match="one level"):
        refuse_helpers_with_helpers(specs)


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

