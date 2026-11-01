"""A subagent that consults another subagent."""

from __future__ import annotations

import pytest
from deepagents.middleware.filesystem import FilesystemMiddleware
from deepagents.middleware.summarization import (
    SummarizationMiddleware as DeepAgentsSummarization,
)
from langchain_core.messages import AIMessage, ToolMessage

from kingfisher import Kingfisher, Request
from kingfisher.core.access.capabilities import ALL, Capabilities, CapabilityError
from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.agent.middlewares.attachments import pdf_allowance
from kingfisher.core.agent.middlewares.clearing import UNSHOWN, high_mark
from kingfisher.core.agent.middlewares.standing import FILESYSTEM
from kingfisher.core.agent.models import SUMMARIZER, summarizer_count, summarizer_trigger
from kingfisher.core.definitions.agents.catalogue import LocalAgentRepository
from kingfisher.core.definitions.subagents.rules import refuse_cycles
from kingfisher.core.definitions.subagents.spec import SubagentError, SubagentSpec
from kingfisher.core.session.ledger import Ledger
from kingfisher.core.session.store import KeptLedger
from kingfisher.plugins import LocalSessions, backend_at
from tests.conftest import (
    EVERY_TOOL,
    FakeToolCallingModel,
    a_subagent,
    an_agent,
    middlewares_dir,
    start,
    subagents_dir,
    tools_dir,
)
from tests.unit.scripted import Scripted

REVIEWER = """name: reviewer
description: Checks figures.
builtin_tools: ["*"]
tools: ["*"]
subagents: [second-opinion]
system_prompt: |
  You check figures, and may ask for a second opinion.
"""

HELPER = """name: second-opinion
description: Answers again, elsewhere.
builtin_tools: ["*"]
tools: ["*"]
system_prompt: |
  You answer on your own.
"""


#: The same helper, naming `reviewer` back. Legal shape, illegal graph: this is
#: the two-cycle, and the only thing between it and an endless build.
CYCLIC_HELPER = HELPER.replace("system_prompt:", "subagents: [reviewer]\nsystem_prompt:")

#: A third level. `reviewer` consults `second-opinion`, which consults `checker`.
CHECKER = """name: checker
description: Checks the checker.
builtin_tools: ["*"]
tools: ["*"]
system_prompt: |
  You are the last word.
"""

NESTING_HELPER = HELPER.replace("system_prompt:", "subagents: [checker]\nsystem_prompt:")

#: Its built-ins as written, for a test that writes its own.
STAR = 'builtin_tools: ["*"]\n'

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
    # `.graph` here rather than inside the accessors below: every one of them walks
    # `.nodes`, so pushing the unwrap down would work and hide which layer holds a
    # record -- and each returns something truthy either way, so nothing would say so.
    return build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=subagents),
    ).graph


def _subagent(graph, name: str):
    """One of the agent's own subagents, compiled."""
    from tests.unit.test_delegation_ceiling import _subagent_graphs

    return _subagent_graphs(graph)[name]


def _helper(graph, subagent: str, name: str):
    """A subagent's *helper*, which is a different instance from the subagent of the
    same name the agent holds directly.

    Worth its own accessor because reaching for the top-level one instead is an easy
    mistake that passes: a standalone `second-opinion` has no helpers and no `task`
    either, so asserting against it proves nothing. A mutation that handed every
    helper the parent's `task` went undetected until this existed.
    """
    from tests.unit.test_delegation_ceiling import _subagent_graphs

    return _subagent_graphs(_subagent(graph, subagent))[name]


def _subagents_of(graph) -> set[str]:
    """The subagents this compiled agent can reach, by name."""
    from tests.unit.test_delegation_ceiling import _subagent_graphs

    return set(_subagent_graphs(graph))


def _tools_of(graph) -> set[str]:
    node = getattr(graph, "nodes", {}).get("tools")
    by_name = getattr(getattr(node, "bound", None), "tools_by_name", {})
    return set(by_name)


# -- the format ------------------------------------------------------------


def test_a_definition_may_name_subagents():
    """It was refused, with a reason that turned out to be wrong about what the format
    could express.
    """
    spec = a_subagent(REVIEWER, "reviewer.yaml")

    assert spec.subagents == ("second-opinion",)


def test_naming_none_is_the_default():
    """Like every grant field: a subagent that needed the whole catalogue would not have
    been worth defining.
    """
    spec = a_subagent(HELPER, "second-opinion.yaml")

    assert spec.subagents is None


@pytest.mark.parametrize("listed", ["[read_file, grep]", "[]"])
def test_naming_helpers_with_built_ins_that_leave_out_task_is_refused(listed):
    """The shipped `reviewer` named `profiler` and listed its built-ins without `task`,
    so every consultation was refused at run time and nothing said so at load.
    """
    body = REVIEWER.replace(STAR, f"builtin_tools: {listed}\n")

    with pytest.raises(SubagentError) as raised:
        a_subagent(body, "reviewer.yaml")

    assert str(raised.value).startswith("reviewer.yaml: subagents names second-opinion")
    assert "builtin_tools leaves out 'task'" in str(raised.value)
    assert "Add task to builtin_tools" in str(raised.value)


def test_naming_helpers_with_builtin_tools_left_out_is_refused():
    """Left out, `builtin_tools` grants no built-in, so `task` is missing as surely as
    from a list without it -- and "leaves out 'task'" would send the reader hunting for
    a list they never wrote.
    """
    with pytest.raises(SubagentError) as raised:
        a_subagent(REVIEWER.replace(STAR, ""), "reviewer.yaml")

    said = str(raised.value)
    assert said.startswith("reviewer.yaml: subagents names second-opinion")
    assert "builtin_tools is left out, which grants no built-in" in said
    assert "leaves out 'task'" not in said
    assert "builtin_tools: ['*'] for every built-in" in said


@pytest.mark.parametrize("written", [STAR, "builtin_tools: [read_file, task]\n"])
def test_naming_helpers_loads_wherever_task_is_held(written):
    """The control the refusal needs: a rule refusing every subagent with helpers passes
    the test above.
    """
    spec = a_subagent(REVIEWER.replace(STAR, written), "reviewer.yaml")

    assert spec.subagents == ("second-opinion",)


@pytest.mark.parametrize("written", ["", "subagents: []\n"])
def test_a_subagent_naming_no_helpers_may_leave_task_out(written):
    """A rule reading `builtin_tools` alone would refuse every narrow subagent there is."""
    body = HELPER.replace(STAR, f"builtin_tools: [read_file]\n{written}")

    assert a_subagent(body, "second-opinion.yaml").builtin_tools == ("read_file",)


# -- one level, structurally ----------------------------------------------


def test_a_helper_may_have_helpers_of_its_own(cfg, session_dir):
    """Three levels, which the format refused until now."""
    _define(cfg, REVIEWER, NESTING_HELPER, CHECKER)

    graph = _build(cfg, session_dir, subagents=("reviewer", "second-opinion", "checker"))
    helper = _helper(graph, "reviewer", "second-opinion")

    assert "checker" in _subagents_of(helper)


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


def test_a_loop_through_a_name_two_files_share_is_refused_before_it_is_built(
    cfg, session_dir
):
    """A build that settled a shared name on one file would recurse through a loop the
    walk never saw, since the walk follows only names that are keys.

    Both halves of the loop are activated so that such a build goes round it, which is
    the failure named. With `reviewer` alone, narrowing drops the settled edge and the
    build merely succeeds.
    """
    root = subagents_dir(cfg)
    for where, body in {
        "reviewer.yaml": REVIEWER,
        "a/second-opinion.yaml": CYCLIC_HELPER,
        "b/second-opinion.yaml": HELPER,
    }.items():
        (root / where).parent.mkdir(parents=True, exist_ok=True)
        (root / where).write_text(body, encoding="utf-8")

    with pytest.raises(CapabilityError, match="more than one source offers"):
        _build(cfg, session_dir, subagents=("reviewer", "a/second-opinion.yaml::second-opinion"))


def test_a_definition_reached_twice_is_not_a_cycle():
    """The distinction the check exists to draw."""
    specs = {
        "reviewer": a_subagent(REVIEWER, "reviewer.yaml"),
        "second-opinion": a_subagent(NESTING_HELPER, "second-opinion.yaml"),
        "checker": a_subagent(CHECKER, "checker.yaml"),
    }

    refuse_cycles(specs)  # no raise


def test_a_definition_naming_itself_is_a_cycle():
    """The one-node loop, which a check written around pairs would miss."""
    body = HELPER.replace("system_prompt:", "subagents: [second-opinion]\nsystem_prompt:")

    with pytest.raises(SubagentError, match="second-opinion -> second-opinion"):
        refuse_cycles({"second-opinion": a_subagent(body, "second-opinion.yaml")})


def test_delegation_stops_at_a_helpers_own_general_purpose(cfg, session_dir):
    """The depth bound is a call that is not made, so this is what proves it: a helper's
    `task` reaches only its own `general-purpose`, and no `general-purpose` holds `task`
    to go further -- `reviewer`'s included, whose caller names a helper it could inherit.

    Asked of the helper *inside* `reviewer`, not the standalone subagent of the same
    name, which is built apart and would pass whatever this change did.
    """
    from tests.unit.test_delegation_ceiling import _subagent_graphs

    _define(cfg, REVIEWER, HELPER)
    graph = _build(cfg, session_dir)

    reviewers = _subagent_graphs(_subagent(graph, "reviewer"))
    helpers = _subagent_graphs(_helper(graph, "reviewer", "second-opinion"))

    assert set(helpers) == {"general-purpose"}
    assert "task" not in _tools_of(reviewers["general-purpose"])
    assert "task" not in _tools_of(helpers["general-purpose"])


def test_a_helper_is_not_handed_the_parents_task_tool(cfg, session_dir):
    """The harvested `task` is bound to the *parent's* subagent list: a helper's own reaches
    its own `general-purpose` and nothing its parent can."""
    _define(cfg, REVIEWER, HELPER)

    graph = _build(cfg, session_dir)

    assert _subagents_of(_helper(graph, "reviewer", "second-opinion")) == {"general-purpose"}
    assert "read_file" in _tools_of(_helper(graph, "reviewer", "second-opinion"))


# -- the subagent can actually subagent ------------------------------------


def test_a_subagent_that_names_a_helper_gets_a_task_tool(cfg, session_dir):
    """What the whole change is for."""
    _define(cfg, REVIEWER, HELPER)

    graph = _build(cfg, session_dir)

    assert "task" in _tools_of(_subagent(graph, "reviewer"))


def test_a_subagent_that_names_none_gets_no_task_tool(cfg, session_dir):
    """A subagent whose line leaves `task` out gets none, and so no `general-purpose`:
    the line is the switch, and nothing else turns it on."""
    _define(cfg, HELPER.replace(STAR, "builtin_tools: [read_file, ls]\n"))

    graph = _build(cfg, session_dir, subagents=("second-opinion",))

    assert "task" not in _tools_of(_subagent(graph, "second-opinion"))


# -- the caller decides ----------------------------------------------------


def test_a_helper_the_caller_did_not_name_is_dropped(cfg, session_dir):
    """Not refused: dropped, leaving the subagent its own `general-purpose`."""
    _define(cfg, REVIEWER, HELPER)

    graph = _build(cfg, session_dir, subagents=("reviewer",))

    assert _subagents_of(_subagent(graph, "reviewer")) == {"general-purpose"}


def test_a_helper_nothing_defines_is_refused(cfg, session_dir):
    """The other half of the rule every field here follows: a name nothing defines is a
    mistake in the definition, not a narrower caller.
    """
    _define(cfg, REVIEWER.replace("second-opinion", "nobody"))

    with pytest.raises(CapabilityError, match="unknown subagent"):
        _build(cfg, session_dir, subagents=("reviewer",))


def test_granting_everything_reaches_the_helper_too(cfg, session_dir):
    """`ALL` is every subagent the definitions offer, so a caller who narrowed nothing
    has named the helper as much as anything else.
    """
    _define(cfg, REVIEWER, HELPER)

    graph = _build(cfg, session_dir, subagents=ALL)

    assert "task" in _tools_of(_subagent(graph, "reviewer"))


# -- and it actually runs --------------------------------------------------


def test_a_subagent_consults_its_helper_end_to_end(cfg, session_dir):
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
        backend=backend_at(cfg, session_dir),
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
    ).graph

    out = graph.invoke(
        {"messages": [{"role": "user", "content": "go"}]}, {"recursion_limit": 20}
    )
    transcript = "\n".join(str(getattr(m, "content", "")) for m in out["messages"])

    assert "REVIEWER-SUMMARISED" in transcript  # the subagent reported back
    assert "PARENT-DONE" in transcript
    # The helper's own words stayed inside the subagent, which is the property
    # that makes a helper worth having rather than just another tool call.
    assert "SECOND-OPINION-ANSWERED" not in transcript


# -- what nesting must not have loosened ------------------------------------


OWN_TOOL = '''
def own_tool(text: str) -> str:
    """A tool only this subagent's folder holds."""
    return "OWN:" + text


TOOLS = [own_tool]
'''


@pytest.mark.parametrize(
    ("line", "call", "runs"),
    [
        ("[read_file, task]", {"name": "own_tool", "args": {"text": "x"}}, "OWN:x"),
        ("[read_file, task]", {"name": "read_file", "args": {"file_path": "/inputs/a.txt"}}, "PUT"),
        ("[read_file, task]", {"name": "execute", "args": {"command": "echo ESCAPED"}}, None),
        ('["*"]', {"name": "write_todos", "args": {"todos": []}}, None),
    ],
    ids=["its-folder", "its-built-in", "not-the-agents-shell", "never-the-to-do-list"],
)
def test_a_subagents_general_purpose_holds_what_it_holds_and_no_more(scripted, line, call, runs):
    """deepagents' own `general-purpose` holds the agent's every tool, the shell and the
    to-do list among them, and none of a subagent's own: given that one, a subagent
    could reach past its line by handing work on."""
    folder = subagents_dir(scripted) / "reviewer"
    (folder / "tools").mkdir(parents=True)
    (folder / "reviewer.yaml").write_text(
        "name: reviewer\ndescription: Checks figures.\n"
        f"builtin_tools: {line}\ntools: [{{name: own_tool, source: bundled}}]\n"
        "system_prompt: |\n  REVIEWER-OWN-PROMPT.\n",
        encoding="utf-8",
    )
    (folder / "tools" / "own_tool.py").write_text(OWN_TOOL, encoding="utf-8")
    an_agent(scripted, subagents="[reviewer]", **EVERY_TOOL)

    def task(who: str) -> AIMessage:
        asked = {"description": "a job", "subagent_type": who}
        return AIMessage(content="", tool_calls=[{"name": "task", "args": asked, "id": who}])

    Scripted.script.extend(
        [
            task("reviewer"),
            task("general-purpose"),
            AIMessage(content="", tool_calls=[{**call, "id": "c"}]),
            *(AIMessage(content=f"done {n}") for n in range(3)),
        ]
    )
    start(scripted, "s")
    (scripted.workspace / "sessions" / "s" / "inputs" / "a.txt").write_text("PUT")

    Kingfisher(scripted, sessions=LocalSessions()).run(Request("go", agent="only", session_id="s"))

    (heard,) = [one for one in Scripted.heard if getattr(one[-1], "tool_call_id", None) == "c"]
    answer = heard[-1].text
    assert (runs in answer) if runs else answer.startswith("Error:"), answer
    # Told deepagents' text for a helper, and not the role its caller was given.
    assert "In order to complete the objective" in heard[0].text
    assert "REVIEWER-OWN-PROMPT" not in heard[0].text


def test_a_definition_is_compiled_once_for_each_position(cfg, session_dir, monkeypatch):
    """Once per definition, not once per path -- the difference between linear and
    exponential, and the thing that makes reuse affordable.
    """
    shared = HELPER.replace("second-opinion", "shared")
    left = REVIEWER.replace("reviewer", "left").replace("second-opinion", "shared")
    right = REVIEWER.replace("reviewer", "right").replace("second-opinion", "shared")
    _define(cfg, left, right, shared)

    from kingfisher.core.agent.kinds import subagents as delegation

    built: list[str] = []
    real = delegation.as_subagent
    monkeypatch.setattr(
        delegation,
        "as_subagent",
        lambda spec, *a, **k: (built.append(spec.name), real(spec, *a, **k))[1],
    )
    monkeypatch.setattr("kingfisher.core.agent.assembly.as_subagent",
                        delegation.as_subagent)

    _build(cfg, session_dir, subagents=("left", "right", "shared"))

    assert built.count("shared") == 2, (
        f"`shared` is reached by two parents and activated directly; it should "
        f"compile once per position, got {built}"
    )


# -- the star is an edge, not an absence -----------------------------------


def _spec(name, subagents=None):
    body = f"name: {name}\ndescription: A subagent.\nsystem_prompt: |\n  x\n"
    if subagents is not None:
        # `task`, or naming a helper is refused at load before any walk is reached.
        body += f"builtin_tools: [task]\nsubagents: {subagents}\n"
    return a_subagent(body, f"{name}.yaml")


def test_a_definition_may_not_ask_for_every_subagent():
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
    spec = a_subagent(
        "name: broad\ndescription: A subagent.\nsystem_prompt: |\n  x\n"
        'tools: ["*"]\n',
        "broad.yaml",
    )

    assert spec.tools == ALL
    assert spec.skills is None  # unwritten, which is what grants none


def test_a_subagent_may_not_ask_for_every_skill():
    """`skills: ["*"]` read as "all of them" and arrived as none.

    It resolves to whatever the request granted, and a subagent is handed an
    index only where its skills are *named* -- so under the ordinary request,
    which grants every skill, the subagent got no index at all. It worked only
    when the caller happened to narrow skills, which is a meaning no author of
    the file can see.
    """
    body = (
        "name: broad\ndescription: A subagent.\nsystem_prompt: |\n  x\n"
        'skills: ["*"]\n'
    )

    with pytest.raises(SubagentError, match=r"skills may not be"):
        a_subagent(body, "broad.yaml")


def test_the_skills_refusal_says_what_to_write_instead():
    """A refusal naming no remedy sends the reader to the source, and the habit it
    came from -- a request, where the star is the ordinary way to say everything --
    is the same one the `subagents` refusal above exists to catch.
    """
    body = (
        "name: broad\ndescription: A subagent.\nsystem_prompt: |\n  x\n"
        'skills: ["*"]\n'
    )

    with pytest.raises(SubagentError, match="Name the procedures this one uses"):
        a_subagent(body, "broad.yaml")


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
    """The helper specs kingfisher hung off one subagent, by name."""
    for middleware in spec.get("middleware", []):
        for attribute in ("subagents", "_subagents"):
            if found := getattr(middleware, attribute, None):
                return {helper["name"]: helper for helper in found}
    return {}


@pytest.mark.parametrize("below", ["second-opinion", "general-purpose"])
def test_a_helper_runs_the_model_of_the_subagent_that_summoned_it(cfg, session_dir, below):
    """A definition naming no model runs whatever reached it, and one level down that is
    the subagent above -- not the main agent, which for a subagent's own `general-purpose`
    would put its every call on the expensive model.
    """
    _define(cfg, CHEAP_REVIEWER, HELPER)
    main = FakeToolCallingModel(responses=[AIMessage(content="ok")])

    built = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=main,
        capabilities=Capabilities(subagents=("reviewer", "second-opinion")),
    )

    parent = {spec["name"]: spec for spec in built.subagents or ()}["reviewer"]
    helper = _helper_specs(parent)[below]

    assert helper["model"] is not main, "the helper inherited the main agent's model"
    assert helper["model"].model == "cheap-model"


def test_one_helper_under_two_parents_is_two_subagents(cfg, session_dir):
    """A helper naming no model runs whatever reached it, so the same name under two
    differently-pinned parents is two different agents.
    """
    _define(cfg, CHEAP_REVIEWER, ELSEWHERE_REVIEWER, HELPER)

    assembled = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=("reviewer", "auditor", "second-opinion")),
    )

    built = {spec["name"]: spec for spec in assembled.subagents or ()}
    under_cheap = _helper_specs(built["reviewer"])["second-opinion"]
    under_elsewhere = _helper_specs(built["auditor"])["second-opinion"]

    assert under_cheap["model"].model == "cheap-model"
    assert under_elsewhere["model"].model == "elsewhere-model"


def test_a_helper_under_an_unpinned_subagent_still_runs_the_agents_model(
    cfg, session_dir
):
    """The other half, and the one that must not have changed."""
    _define(cfg, REVIEWER, HELPER)
    main = FakeToolCallingModel(responses=[AIMessage(content="ok")])

    built = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=main,
        capabilities=Capabilities(subagents=("reviewer", "second-opinion")),
    )

    parent = {spec["name"]: spec for spec in built.subagents or ()}["reviewer"]
    assert _helper_specs(parent)["second-opinion"]["model"] is main


# -- a helper summarises, as every other graph does -----------------------------


def _summarizers(middleware) -> list:
    return [one for one in middleware if getattr(one, "name", None) == SUMMARIZER]


def _reviewers_helper(built) -> dict:
    parent = {spec["name"]: spec for spec in built.subagents or ()}["reviewer"]
    return _helper_specs(parent)["second-opinion"]


def test_a_helper_runs_deepagents_summarizer(cfg, session_dir):
    """A helper's conversation was never summarised, and grew until the model refused it:
    `create_sub_agent` builds its stack from the spec alone, where `create_deep_agent`
    gives every other graph deepagents' own summariser."""
    _define(cfg, REVIEWER, HELPER)
    model = FakeToolCallingModel(responses=[AIMessage(content="ok")])

    built = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=model,
        capabilities=Capabilities(subagents=("reviewer", "second-opinion")),
    )

    (summarizer,) = _summarizers(_reviewers_helper(built)["middleware"])
    assert isinstance(summarizer, DeepAgentsSummarization)
    assert summarizer._lc_helper.model is model, "on the model the helper runs"
    # The control: deepagents adds a top-level subagent's itself, so its spec carries none.
    parent = {spec["name"]: spec for spec in built.subagents or ()}["reviewer"]
    assert not _summarizers(parent["middleware"])


@pytest.mark.filterwarnings("ignore:.*a name deepagents uses:UserWarning")
def test_a_helper_naming_its_own_summarizer_runs_that_one_alone(cfg, session_dir):
    """Beside deepagents' own, a deployment's summariser answers to the same name, which
    langchain refuses to build; in every other graph the one a definition names replaces
    deepagents', and a helper's is no different."""
    from tests.unit.test_summarizer_trigger import Early

    _define(
        cfg, REVIEWER, HELPER.replace("system_prompt:", "middlewares: [early]\nsystem_prompt:")
    )
    an_agent(cfg, middlewares="[early]", subagents="[reviewer, second-opinion]", **EVERY_TOOL)

    built = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        middleware_registry={"early": Early},
        agent=LocalAgentRepository(cfg.catalogue_roots["agents"]).specs["only"],
    )

    (summarizer,) = _summarizers(_reviewers_helper(built)["middleware"])
    assert isinstance(summarizer, Early)


def test_a_helpers_clearing_and_pdfs_are_sized_by_the_summarizer_it_runs(cfg, session_dir):
    """A helper's clearing mark and PDF allowance were sized by a summariser that never ran
    there, so neither said where its conversation would be rewritten; read off the one in
    its stack, both are, on a window of its model's own."""
    from tests.unit.test_summarizer_trigger import _allowance, _high

    _define(cfg, REVIEWER, HELPER)
    model = FakeToolCallingModel(
        responses=[AIMessage(content="ok")], profile={"max_input_tokens": 100_000}
    )

    built = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=model,
        ledger=Ledger("t", KeptLedger()),
        capabilities=Capabilities(subagents=("reviewer", "second-opinion")),
    )

    helper = _reviewers_helper(built)["middleware"]
    (summarizer,) = _summarizers(helper)
    fires = summarizer_trigger(model, [summarizer])
    assert fires < 170_000, "a fraction of this window, not deepagents' fixed default"
    assert _allowance(helper) == pdf_allowance(fires, summarizer_count(model, [summarizer]))
    assert _high(helper) == high_mark(fires, None, subject="the helper")


def test_a_helpers_history_is_kept_apart_from_its_summoners(cfg, session_dir):
    """The task tool hands a helper its summoner's state, the summariser's own keys among
    them: had the session id that names a history file come too, the helper's evicted
    messages would have been appended to its summoner's. Driven, both summarising."""
    _define(cfg, REVIEWER, HELPER)
    for name, mark in (("a.txt", "REVIEWERS"), ("b.txt", "HELPERS")):
        line = f"{mark} " + "x" * 280 + "\n"
        (session_dir / "inputs" / name).write_text(line * 100, encoding="utf-8")

    def task(who: str) -> AIMessage:
        asked = {"description": f"TASK-FOR-{who}", "subagent_type": who}
        return AIMessage(content="", tool_calls=[{"name": "task", "args": asked, "id": who}])

    def read(path: str) -> AIMessage:
        asked = {"file_path": path}
        return AIMessage(content="", tool_calls=[{"name": "read_file", "args": asked, "id": path}])

    # A window this small puts one read past either graph's trigger, so each summarises on
    # its next call and asks this model for the summary first.
    model = FakeToolCallingModel(
        responses=[
            task("reviewer"),
            read("/inputs/a.txt"),
            AIMessage(content="REVIEWER-SUMMARY"),
            task("second-opinion"),
            read("/inputs/b.txt"),
            AIMessage(content="HELPER-SUMMARY"),
            AIMessage(content="SECOND-OPINION-ANSWERED"),
            AIMessage(content="REVIEWER-DONE"),
            AIMessage(content="PARENT-DONE"),
        ],
        profile={"max_input_tokens": 8_500},
    )
    graph = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=model,
        capabilities=Capabilities(subagents=("reviewer", "second-opinion")),
    ).graph

    out = graph.invoke({"messages": [{"role": "user", "content": "go"}]}, {"recursion_limit": 40})

    assert out["messages"][-1].content == "PARENT-DONE"
    histories = [
        path.read_text(encoding="utf-8")
        for path in (session_dir / "scratchpad" / "conversation_history").glob("*.md")
    ]
    whose = sorted(
        ("TASK-FOR-reviewer" in text, "TASK-FOR-second-opinion" in text) for text in histories
    )
    assert whose == [(False, True), (True, False)], "one history each, holding its own"


@pytest.mark.filterwarnings("ignore:.*a name deepagents uses:UserWarning")
def test_a_helpers_own_summarizer_stands_where_its_default_would(cfg, session_dir):
    """A summariser a helper's definition named sat innermost, inside clearing and every
    standing middleware, where `create_sub_agent` left it among the definition's own."""
    from tests.unit.test_summarizer_trigger import Early

    def helpers_stack(helper: str) -> list:
        _define(cfg, REVIEWER, helper)
        built = build_agent(
            cfg,
            backend=backend_at(cfg, session_dir),
            model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
            middleware_registry={"early": Early},
            agent=LocalAgentRepository(cfg.catalogue_roots["agents"]).specs["only"],
            ledger=Ledger("t", KeptLedger()),
        )
        return _reviewers_helper(built)["middleware"]

    an_agent(cfg, middlewares="[early]", subagents="[reviewer, second-opinion]", **EVERY_TOOL)
    default = helpers_stack(HELPER)
    named = helpers_stack(HELPER.replace("system_prompt:", "middlewares: [early]\nsystem_prompt:"))

    assert isinstance(named[0], Early)
    # The whole stack by name, so the one named took deepagents' place and nothing moved.
    assert [one.name for one in named] == [one.name for one in default]


OWN_SUMMARIZER = """
from deepagents.middleware.summarization import SummarizationMiddleware


class Own(SummarizationMiddleware):
    \"\"\"deepagents' summariser under a definition's own name, firing by size nowhere near.\"\"\"

    name = "own"
    wants = frozenset({"model", "backend"})

    def __init__(self, model, backend):
        super().__init__(model, backend=backend, trigger=("tokens", 150_000))
        self.name = "SummarizationMiddleware"


MIDDLEWARES = [Own]
"""


@pytest.mark.filterwarnings("ignore:.*a name deepagents uses:UserWarning")
@pytest.mark.parametrize("named", [False, True], ids=["deepagents", "its-own"])
def test_a_helpers_refusal_for_length_is_clearings_to_answer_first(scripted, named):
    """A summariser a helper's definition named heard a call refused for length before
    clearing did, and answered it itself, copying the batch under `large_tool_results/`.
    Driven through a turn: only the stack a turn builds decides who hears the refusal, and
    deepagents' own is the control."""
    from tests.unit.test_clearing import TOOLS, _too_long

    tools_dir(scripted).mkdir(parents=True, exist_ok=True)
    (tools_dir(scripted) / "probe.py").write_text(TOOLS, encoding="utf-8")
    middlewares_dir(scripted).mkdir(parents=True, exist_ok=True)
    (middlewares_dir(scripted) / "own.py").write_text(OWN_SUMMARIZER, encoding="utf-8")
    own = HELPER.replace("system_prompt:", "middlewares: [own]\nsystem_prompt:")
    _define(scripted, REVIEWER, own if named else HELPER)
    an_agent(scripted, middlewares="[own]", subagents="[reviewer, second-opinion]", **EVERY_TOOL)
    start(scripted, "s")

    def task(who: str) -> AIMessage:
        asked = {"description": "go", "subagent_type": who}
        return AIMessage(content="", tool_calls=[{"name": "task", "args": asked, "id": who}])

    batch = AIMessage(
        content="",
        tool_calls=[{"name": "bulky", "args": {"n": n}, "id": f"c{n}"} for n in (1, 2)],
    )
    Scripted.script.extend([
        task("reviewer"),
        task("second-opinion"),
        batch,
        _too_long(),
        AIMessage(content="HELPER-DONE"),
        AIMessage(content="REVIEWER-DONE"),
        AIMessage(content="done"),
    ])

    result = Kingfisher(scripted, sessions=LocalSessions()).run(
        Request("go", agent="only", session_id="s")
    )

    assert result.answer == "done"
    assert Scripted.summaries == 0, "the refusal cost a summary"
    session = scripted.workspace / "sessions" / "s"
    assert not list(session.rglob("large_tool_results/*")), "the batch was copied aside"
    retry = [m.content for m in Scripted.heard[4] if isinstance(m, ToolMessage)]
    assert len(retry) == 2 and all(UNSHOWN in one for one in retry), retry


class OwnFilesystem(FilesystemMiddleware):
    """A deployment's own fence over the filesystem, taking deepagents' place by its name."""

    wants = frozenset({"backend"})

    def __init__(self, backend) -> None:
        super().__init__(backend=backend)

    @property
    def name(self) -> str:
        return FILESYSTEM


#: A subagent written to travel, which since #1033 runs the agent's middleware line.
PORTABLE_HELPER = """
SUBAGENTS = [
    {
        "name": "surveyor",
        "description": "Surveys files.",
        "system_prompt": "You survey.",
        "builtin_tools": ["read_file"],
    }
]
"""


def _filesystems(middleware) -> list:
    return [one for one in middleware if one.name == FILESYSTEM]


def _built_with_own_filesystem(cfg, session_dir):
    return build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        middleware_registry={"own-fs": OwnFilesystem},
        agent=LocalAgentRepository(cfg.catalogue_roots["agents"]).specs["only"],
    )


@pytest.mark.filterwarnings("ignore:.*a name deepagents uses:UserWarning")
def test_a_helper_naming_its_own_filesystem_middleware_builds_with_that_one(cfg, session_dir):
    """A helper whose definition named its own `FilesystemMiddleware` failed to build, on
    "Please remove duplicate middleware instances": deepagents' merge keeps one of two
    sharing that name in every other stack, and a helper's meets no merge. It goes first,
    where that merge puts one."""
    _define(
        cfg, REVIEWER, HELPER.replace("system_prompt:", "middlewares: [own-fs]\nsystem_prompt:")
    )
    an_agent(cfg, middlewares="[own-fs]", subagents="[reviewer, second-opinion]", **EVERY_TOOL)

    helper = _reviewers_helper(_built_with_own_filesystem(cfg, session_dir))["middleware"]

    (own,) = _filesystems(helper)
    assert isinstance(own, OwnFilesystem)
    assert helper[0] is own


@pytest.mark.filterwarnings("ignore:.*a name deepagents uses:UserWarning")
def test_a_portable_helper_runs_the_agents_own_filesystem_middleware(cfg, session_dir):
    """Since #1033 a portable helper runs the agent's middleware line, so an agent naming
    its own `FilesystemMiddleware` failed at build every turn that reached one."""
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "surveyor.py").write_text(PORTABLE_HELPER, encoding="utf-8")
    _define(cfg, REVIEWER.replace("[second-opinion]", "[surveyor]"))
    an_agent(cfg, middlewares="[own-fs]", subagents="[reviewer, surveyor]", **EVERY_TOOL)

    built = _built_with_own_filesystem(cfg, session_dir)
    parent = {spec["name"]: spec for spec in built.subagents or ()}["reviewer"]
    helper = _helper_specs(parent)["surveyor"]["middleware"]

    (own,) = _filesystems(helper)
    assert isinstance(own, OwnFilesystem)
    assert helper[0] is own


def test_a_helper_naming_no_filesystem_middleware_runs_kingfishers(cfg, session_dir):
    """The control: with none named, a helper's one is kingfisher's own, which leaves a long
    result for the recorder to save rather than offloading a copy of deepagents', and stays
    among kingfisher's, behind the summariser clearing measures after."""
    _define(cfg, REVIEWER, HELPER)

    built = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=("reviewer", "second-opinion")),
    )

    helper = _reviewers_helper(built)["middleware"]
    (own,) = _filesystems(helper)
    assert type(own) is FilesystemMiddleware
    assert own._tool_token_limit_before_evict is None
    assert helper[0].name == SUMMARIZER


def _task(who: str, n: int) -> AIMessage:
    call = {"description": f"job {n}", "subagent_type": who}
    return AIMessage(content="", tool_calls=[{"name": "task", "args": call, "id": f"t{n}"}])


@pytest.mark.parametrize(
    ("who", "line", "plans"),
    [
        ("reviewer", '["*"]', True),
        ("reviewer", "[read_file, write_todos]", True),
        ("reviewer", "[read_file]", False),
        ("second-opinion", '["*"]', True),
        ("second-opinion", "[read_file]", False),
        ("general-purpose", '["*"]', False),
    ],
)
def test_a_subagent_holds_the_to_do_list_where_its_line_grants_it(scripted, who, line, plans):
    """A subagent granted `write_todos` was told it was no tool at all: only the agent's
    stack carried the list, and `general-purpose` must still carry none."""
    helper = HELPER.replace('builtin_tools: ["*"]', f"builtin_tools: {line}")
    reviewer = REVIEWER.replace("subagents: [second-opinion]\n", "")
    if who == "reviewer":
        reviewer = reviewer.replace('builtin_tools: ["*"]', f"builtin_tools: {line}")
    elif who == "second-opinion":
        reviewer = REVIEWER
    _define(scripted, reviewer, helper)
    an_agent(scripted, subagents="[reviewer, second-opinion]", **EVERY_TOOL)
    chain = {"reviewer": ["reviewer"], "second-opinion": ["reviewer", "second-opinion"]}
    plan = {"todos": [{"content": "x", "status": "pending"}]}
    Scripted.script.extend(
        [
            *(_task(one, n) for n, one in enumerate(chain.get(who, [who]))),
            AIMessage(content="", tool_calls=[{"name": "write_todos", "args": plan, "id": "w"}]),
            *(AIMessage(content=f"done {n}") for n in range(4)),
        ]
    )
    start(scripted, "s")

    Kingfisher(scripted, sessions=LocalSessions()).run(Request("go", agent="only", session_id="s"))

    (answer,) = [
        heard[-1] for heard in Scripted.heard if getattr(heard[-1], "tool_call_id", None) == "w"
    ]
    assert answer.text.startswith("Updated todo list") is plans, answer.text
