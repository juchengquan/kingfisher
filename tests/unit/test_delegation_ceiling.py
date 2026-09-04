"""A subagent may never do more than the request that reached it.

`ToolAllowlist` refuses at `wrap_tool_call`, and that held -- for the parent.
A subagent has its own middleware stack and inherits none of the parent's, so
a request that withheld `execute` handed it straight to any delegate. The
restriction looked like a wall with a door beside it.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage

from kingfisher.domain.agent import AgentSpec
from kingfisher.domain.capabilities import ALL, Capabilities, CapabilityError, ceiling, narrowed
from kingfisher.infrastructure.catalogue.documents import read_subagent
from kingfisher.infrastructure.harness.agent import build_agent
from kingfisher.infrastructure.harness.narrowing import ToolAllowlist
from kingfisher.subagents.harness import as_subagent, subagent_skills
from kingfisher.subagents.spec import SubagentError
from tests.conftest import FakeToolCallingModel, capture_build, subagents_dir

HELPER = """name: helper
description: Declares no tools, so it inherits whatever it is given.
system_prompt: |
  You help.

"""


def _with_helper(cfg, definition: str = HELPER, name: str = "helper.yaml"):
    directory = subagents_dir(cfg)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(definition, encoding="utf-8")
    return cfg


def _subagent_graphs(graph):
    """The compiled subagents, which live in the `task` tool's closure."""
    node = getattr(graph, "nodes", {}).get("tools")
    task_tool = getattr(getattr(node, "bound", None), "tools_by_name", {}).get("task")
    found: dict = {}

    def walk(obj, depth=0):
        if depth > 4 or found:
            return
        for attribute in ("func", "__closure__"):
            value = getattr(obj, attribute, None)
            if attribute == "__closure__":
                for cell in value or ():
                    try:
                        contents = cell.cell_contents
                    except ValueError:
                        continue
                    if isinstance(contents, dict) and any(
                        hasattr(v, "nodes") for v in contents.values()
                    ):
                        found.update(contents)
                        return
                    walk(contents, depth + 1)
            elif value is not None:
                walk(value, depth + 1)

    if task_tool is not None:
        walk(task_tool)
    return found


def _model():
    return FakeToolCallingModel(responses=[AIMessage(content="ok")])



def tool_ceiling(spec, *, builtin, workspace):
    """The rule as `as_subagent` calls it, with the spec's two axes unpacked.

    An adapter rather than four rewritten call sites: what these tests are about
    is the narrowing, and threading five keyword arguments through each one would
    bury it. `ceiling` takes the axes rather than the spec because it narrows by
    the *request's grants*, which a spec knows nothing about.
    """
    return ceiling(
        spec.builtin_tools,
        spec.tools,
        granted_builtin=builtin,
        granted_tools=workspace,
        subject=f"subagent {spec.name!r}",
    )


def test_a_delegate_may_not_use_what_its_caller_was_denied(cfg, session_dir):
    """The escape, driven rather than inspected.

    The delegate inherits the parent's model, so a scripted one reaches it: it
    calls `execute`, and the question is whether anything stops it. Asserting
    on what the delegate's ToolNode *registers* would prove nothing -- that is
    identical either way, which is how this went unnoticed.
    """
    responses = [
        AIMessage(
            content="",
            tool_calls=[{"name": "execute", "args": {"command": "echo escaped"}, "id": "c1"}],
        ),
        AIMessage(content="done"),
    ]

    graph = build_agent(
        _with_helper(cfg),
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=responses),
        capabilities=Capabilities(builtin_tools=("read_file", "task"), subagents=("helper",)),
    )

    delegate = _subagent_graphs(graph).get("helper")
    assert delegate is not None, "the declared subagent was not compiled"

    out = delegate.invoke(
        {"messages": [{"role": "user", "content": "go"}]},
        config={"recursion_limit": 6},
    )
    transcript = "\n".join(str(getattr(m, "content", "")) for m in out["messages"])

    assert "escaped" not in transcript, "the delegate ran a command its caller could not"
    # Named for the delegate, not for the request, even though the request is
    # what withheld it here. A delegate's allowlist is two layers intersected
    # and the refusal cannot tell them apart, so it says whose surface the tool
    # is missing from rather than guessing at which layer removed it. Blaming
    # the request is right in this test and was wrong in the one that prompted
    # the change -- see `ToolAllowlist`.
    assert "not available for the 'helper' subagent" in transcript


READ_ONLY = """name: reader
description: Declares its own tools, so it holds less than its caller.
builtin_tools: [read_file, ls]
tools: []
system_prompt: |
  You read.

"""


def test_a_delegate_that_withheld_a_tool_itself_is_the_one_named(cfg, session_dir):
    """The other direction, and the one that reads wrong: the request granted
    everything and the delegate's own definition is what is missing the tool.

    `extractor` ships `builtin_tools: [read_file, ls, glob, grep]`. An
    unrestricted run reached it, asked for `execute`, and was told `execute is
    not available for this request` -- which sent the reader to the command
    line, where nothing was narrowed and nothing was wrong. It asked a second
    time before giving up on the shell.
    """
    responses = [
        AIMessage(
            content="",
            tool_calls=[{"name": "execute", "args": {"command": "echo escaped"}, "id": "c1"}],
        ),
        AIMessage(content="done"),
    ]

    graph = build_agent(
        _with_helper(cfg, READ_ONLY, "reader.yaml"),
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=responses),
        capabilities=Capabilities(subagents=("reader",)),
    )

    delegate = _subagent_graphs(graph).get("reader")
    assert delegate is not None, "the declared subagent was not compiled"

    out = delegate.invoke(
        {"messages": [{"role": "user", "content": "go"}]},
        config={"recursion_limit": 6},
    )
    transcript = "\n".join(str(getattr(m, "content", "")) for m in out["messages"])

    assert "escaped" not in transcript, "the delegate ran what its definition withheld"
    assert "not available for the 'reader' subagent" in transcript
    assert "this request" not in transcript, "the request granted execute -- it is not the wall"


def test_the_builtin_delegate_arrives_with_the_ceiling_on(cfg, session_dir):
    """deepagents supplies a `general-purpose` delegate with "the same
    capabilities as the main agent" and none of our middleware, present
    whenever `task` is -- including for a request that declared none.

    It is not withheld. Supplying one by the same name *replaces* it, since
    the specs are keyed by name, so it keeps working and arrives under the
    caller's ceiling. Withholding it would have cost delegation to every
    narrowed request that had not named a delegate, for no extra safety.
    """
    responses = [
        AIMessage(
            content="",
            tool_calls=[{"name": "execute", "args": {"command": "echo escaped"}, "id": "c1"}],
        ),
        AIMessage(content="done"),
    ]

    graph = build_agent(
        _with_helper(cfg),
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=responses),
        capabilities=Capabilities(builtin_tools=("read_file", "task"), subagents=("helper",)),
    )

    delegate = _subagent_graphs(graph).get("general-purpose")
    assert delegate is not None, "the built-in delegate should still be reachable"

    out = delegate.invoke(
        {"messages": [{"role": "user", "content": "go"}]},
        config={"recursion_limit": 8},
    )
    transcript = "\n".join(str(getattr(m, "content", "")) for m in out["messages"])
    assert "escaped" not in transcript, "the built-in ran what its caller could not"
    assert "not available for this request" in transcript


def test_the_builtin_survives_when_no_delegates_are_named(cfg, session_dir):
    """`subagents=None` means "no opinion about delegates", and a narrowed
    request that never named one still gets the built-in -- limited, not gone.
    """
    graph = build_agent(
        _with_helper(cfg),
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(builtin_tools=("read_file", "task")),
    )

    delegate = _subagent_graphs(graph).get("general-purpose")
    assert delegate is not None

    out = delegate.invoke(
        {
            "messages": [{"role": "user", "content": "go"}],
        },
        config={"recursion_limit": 8},
    )
    assert out["messages"][-1].content == "ok"


def test_an_unnamed_delegate_is_still_refused(cfg, session_dir):
    """The backstop. Only the names we supplied are reachable, so a delegate
    deepagents adds in some future version cannot arrive unrestricted."""
    responses = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "task",
                    "args": {"description": "escape", "subagent_type": "something-else"},
                    "id": "c1",
                }
            ],
        ),
        AIMessage(content="done"),
    ]

    graph = build_agent(
        _with_helper(cfg),
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=responses),
        capabilities=Capabilities(builtin_tools=("read_file", "task"), subagents=("helper",)),
    )

    out = graph.invoke(
        {"messages": [{"role": "user", "content": "go"}]},
        config={"recursion_limit": 12},
    )
    transcript = "\n".join(str(getattr(m, "content", "")) for m in out["messages"])
    assert "is not a delegate this request may use" in transcript
    assert "general-purpose" in transcript  # and it names what may be reached


def test_an_unrestricted_request_delegates_as_before(cfg, session_dir):
    """The rule attaches to *tool* narrowing. A caller that restricted nothing
    already has everything, so its delegates having everything is not an
    escalation, and deepagents' own spec is left untouched."""
    responses = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "task",
                    "args": {"description": "help", "subagent_type": "general-purpose"},
                    "id": "c1",
                }
            ],
        ),
        AIMessage(content="done"),
    ]

    graph = build_agent(
        _with_helper(cfg),
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=responses),
    )

    out = graph.invoke(
        {"messages": [{"role": "user", "content": "go"}]},
        config={"recursion_limit": 12},
    )
    transcript = "\n".join(str(getattr(m, "content", "")) for m in out["messages"])
    assert "is not a delegate this request may use" not in transcript


# -- one rule, applied at two levels --------------------------------------
#
# `delegation` carried its own copy of the narrowing rule -- identical to
# `capabilities._narrow` across every input pair, with the arguments in the
# other order, and nothing comparing them. It is `capabilities.narrowed` now,
# and this table is checked against both levels from one source: a request
# clamped by what the deployment granted, and a definition clamped by what its
# caller was granted. A second copy that drifted would fail one and not the
# other.
#
# Two things it cannot catch, both verified rather than assumed:
#
# A copy that changes only the *order* passes at the delegate level, because
# the ceiling reaches nothing but `ToolAllowlist`, which keeps a set. The old
# swapped-argument copy fails nothing here, and there is nothing for it to fail
# -- same names, same set, same behaviour.
#
# A copy that is *verbatim* passes everywhere, which is what a behaviour test
# is: `subagent_skills` carried one for months, equal on every input, and only
# a structural search found it. These tests catch a copy that drifts; finding
# one that has not drifted yet is an audit, not a test.

NARROWING = [
    (ALL, ALL, ALL),             # neither end names anything
    (ALL, ("a",), ("a",)),       # only the cap does
    (("a",), ALL, ("a",)),       # only the selection does
    (None, ALL, None),           # nothing, narrowed by everything, is nothing
    (ALL, None, None),           # and the other way round
    (None, ("a",), None),        # `None` absorbs whatever it meets
    (("a",), None, None),
    (("a", "b"), ("b",), ("b",)),  # both name things, the overlap survives
    (("a",), ("b",), ()),        # both name things, nothing overlaps
    (("a",), (), ()),            # an empty cap permits nothing, and is not None
    (("b", "a"), ("a", "b"), ("b", "a")),  # the selection's order is kept
]
CASES = pytest.mark.parametrize(("selection", "cap", "expected"), NARROWING)


@CASES
def test_the_rule_itself(selection, cap, expected):
    assert narrowed(selection, by=cap) == expected


@CASES
def test_a_request_is_narrowed_by_it(selection, cap, expected):
    granted = Capabilities(tools=cap)

    assert granted.intersect(Capabilities(tools=selection)).tools == expected


@CASES
def test_a_delegate_is_narrowed_by_it(cfg, selection, cap, expected):
    """The level that carried the copy.

    Both axes together, because a delegate now narrows two and a flat allowlist
    is their union. Driving them with the same pair keeps this a test of the
    rule rather than of the union: whatever `narrowed` yields once, it yields
    twice, and the allowlist is a set.

    `ALL` means no allowlist at all; `None` means an empty one. They are the
    two ends and the difference is the whole point of spelling them apart.
    """
    spec = replace(
        read_subagent(HELPER, Path("helper.md")), tools=selection, builtin_tools=selection
    )

    built = as_subagent(spec, cfg, tools=cap, builtin_tools=cap)

    allowlists = [m for m in built.get("middleware", []) if isinstance(m, ToolAllowlist)]
    if expected == ALL:
        assert allowlists == []  # no allowlist at all, which is not an empty one
    elif expected is None:
        assert allowlists[0]._allowed == set()  # an empty one, which is not absent
    else:
        # It keeps a set, so order is not observable here; the rule test above
        # is where that case is pinned.
        assert allowlists[0]._allowed == set(expected)


# -- the two axes are resolved apart --------------------------------------


def test_naming_a_workspace_tool_costs_a_delegate_no_builtin():
    """The whole reason the definition's list was split.

    One flat list meant a delegate could not ask for `http_fetch` without
    giving up `read_file`, and nothing in the file showed it happening. #77
    fixed exactly this for a *request*; this is the same fix one level in.
    """
    spec = replace(
        read_subagent(HELPER, Path("helper.md")), tools=("http_fetch",), builtin_tools=ALL
    )

    ceiling = tool_ceiling(spec, builtin=("read_file", "ls"), workspace=("http_fetch", "sql_query"))

    assert isinstance(ceiling, tuple)  # concrete, never `ALL`
    assert set(ceiling) == {"read_file", "ls", "http_fetch"}


def test_naming_a_builtin_costs_a_delegate_no_workspace_tool():
    """And the mirror, which is the direction the presets go."""
    spec = replace(
        read_subagent(HELPER, Path("helper.md")), builtin_tools=("read_file",), tools=ALL
    )

    ceiling = tool_ceiling(spec, builtin=("read_file", "ls"), workspace=("http_fetch",))

    assert isinstance(ceiling, tuple)  # concrete, never `ALL`
    assert set(ceiling) == {"read_file", "http_fetch"}


def test_an_empty_list_is_how_a_delegate_says_none_of_them():
    """`tools: []` is none; omitting the line is all. The presets rely on the
    difference -- read-only means read-only, not "plus whatever ships"."""
    spec = replace(read_subagent(HELPER, Path("helper.md")), builtin_tools=("read_file",), tools=())

    ceiling = tool_ceiling(spec, builtin=("read_file", "ls"), workspace=("http_fetch",))

    assert isinstance(ceiling, tuple)  # concrete, never `ALL`
    assert set(ceiling) == {"read_file"}


def test_one_axis_unresolved_is_refused_rather_than_guessed():
    """`ALL` is the string `"*"`. Unpacked into the union it contributes a tool
    *named* `*` and silently drops the axis it stood for -- which is what this
    did before the guard, and how the first draft of the split passed its own
    tests while granting `{'*', 'b'}`.
    """
    spec = replace(read_subagent(HELPER, Path("helper.md")), builtin_tools=ALL, tools=("a",))

    with pytest.raises(ValueError, match="one tool axis resolved"):
        tool_ceiling(spec, builtin=ALL, workspace=("a",))


#: The rows where the definition declared something. `skills` cannot use the
#: other two: an undeclared `skills` means *none* rather than no opinion, which
#: is the one place this rule is deliberately not the one that applies.
DECLARED = [case for case in NARROWING if case[0] is not None]


@pytest.mark.parametrize(("selection", "cap", "expected"), DECLARED)
def test_a_delegates_skills_are_narrowed_by_it(selection, cap, expected):
    """The fourth place, and the one the whole-function hash could not see.

    The dropping was two lines inlined here, equal to `narrowed` across every
    input pair. Only the refusal above it -- a name nothing offers at all -- is
    this function's own.
    """
    spec = replace(read_subagent(HELPER, Path("helper.yaml")), skills=selection)

    assert subagent_skills(spec, ("a", "b", "c"), cap) == expected


def test_undeclared_skills_mean_none_and_undeclared_tools_inherit():
    """Where `tools` inherits, `skills` does not. That asymmetry is older than
    the shared rule, and it is two defaults now rather than two readings of one
    value -- the reader no longer special-cases either."""
    parsed = read_subagent(HELPER, Path("helper.yaml"))

    assert parsed.skills is None  # declared none, so none
    assert parsed.tools == ALL  # declared nothing, so whatever the caller has
    assert subagent_skills(parsed, ("a", "b"), ("a", "b")) is None
    assert narrowed(parsed.tools, by=("a", "b")) == ("a", "b")


def _built_with(cfg, session_dir, capabilities) -> dict:
    """The arguments `create_deep_agent` was handed, letting the call through.

    `conftest.capture_build` does this with a monkeypatch fixture; these two
    want it without one, and letting the call happen still matters -- it is
    what makes deepagents validate the spec we supply.
    """
    import kingfisher.infrastructure.harness.agent as agent_module

    seen: dict = {}
    real = agent_module.create_deep_agent

    def spy(**kwargs):
        seen.update(kwargs)
        return real(**kwargs)

    agent_module.create_deep_agent = spy
    try:
        build_agent(
            _with_helper(cfg),
            session_dir=session_dir,
            model=_model(),
            capabilities=capabilities,
        )
    finally:
        agent_module.create_deep_agent = real
    return seen


def test_the_builtin_delegate_gets_exactly_the_main_agents_tools(cfg, session_dir):
    """deepagents ships it described to the model as having "access to all tools
    as the main agent", and that description is handed straight through.

    The sentence is true only because of the ceiling attached here, and only if
    the two sets are *equal*. Broader makes it a lie in the dangerous direction
    -- delegation as a way past a restriction, which is the escape this file is
    about. Narrower makes it a lie in the confusing one: the model delegates on
    the strength of it and is refused mid-task.

    Equality rather than "the delegate is restricted", because a test for
    restriction passes for a ceiling that is merely *different*.
    """
    seen = _built_with(cfg, session_dir, Capabilities(builtin_tools=("read_file", "task")))

    (parent,) = [m for m in seen["middleware"] if isinstance(m, ToolAllowlist)]
    (builtin,) = [s for s in seen["subagents"] if s["name"] == "general-purpose"]
    (delegate,) = [m for m in builtin["middleware"] if isinstance(m, ToolAllowlist)]

    assert parent._allowed == delegate._allowed == {"read_file", "task"}


def test_the_builtin_delegate_is_supplied_exactly_once(cfg, session_dir):
    """Supplying the spec is the only hook there is.

    deepagents builds this delegate itself and takes no middleware for it --
    `GeneralPurposeSubagentProfile` offers `enabled`, `description` and
    `system_prompt`, and nothing else. An explicit spec by the same name is the
    documented override, and it is what carries the ceiling: measured, removing
    it lets a request granted only `read_file` and `task` run `execute` through
    the delegate.

    Once, not twice: deepagents skips adding its own when the caller supplied
    one, so a second would mean it stopped honouring that and the unrestricted
    version was back alongside ours.
    """
    seen = _built_with(cfg, session_dir, Capabilities(builtin_tools=("read_file", "task")))

    names = [s["name"] for s in seen["subagents"]]
    assert names.count("general-purpose") == 1


def test_an_unrestricted_request_supplies_no_ceiling_and_needs_none(cfg, session_dir):
    """Nothing was narrowed, so the delegate having everything the main agent
    has is what deepagents would have done anyway.

    The spec is still supplied, which this used to assert the opposite of. The
    ceiling was the only thing riding on it then, and an unrestricted request
    has no ceiling to attach; two things ride on it now that are owed to every
    run -- the deployment's own middleware, and the `DeclaredDelegatesOnly`
    backstop against a delegate a future deepagents adds. What this test is
    about is the ceiling, and the ceiling is still absent.
    """
    seen = _built_with(cfg, session_dir, Capabilities())

    assert not [m for m in seen["middleware"] if isinstance(m, ToolAllowlist)]
    supplied = [s for s in (seen.get("subagents") or ()) if s["name"] == "general-purpose"]
    assert len(supplied) == 1
    assert supplied[0]["middleware"] == [], "nothing was narrowed and nothing registered"


# -- a tool name nothing offers -------------------------------------------
#
# The other half of this module's rule, which `tools` never had. A skill
# nothing defines has always raised; a tool nothing defines went straight into
# `narrowed`, where an unknown name is simply absent from the intersection.

TYPO = """name: helper
description: Asks for a tool by a name that does not exist.
builtin_tools: [reed_file]
system_prompt: |
  You help.
"""

STAR = """name: helper
description: Writes the wildcard, which is the list form and only the list form.
builtin_tools: ["*"]
tools: ["*"]
system_prompt: |
  You help.
"""

BARE_STAR = STAR.replace('builtin_tools: ["*"]', 'builtin_tools: "*"')
MIXED = STAR.replace('builtin_tools: ["*"]', 'builtin_tools: ["*", read_file]')


def _build(cfg, session_dir, definition):
    return build_agent(
        _with_helper(cfg, definition),
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=("helper",)),
    )


def test_a_misspelled_tool_is_refused_not_dropped(cfg, session_dir):
    """`tools: [reed_file]` built a delegate with *no* tools, silently.

    Not "a delegate missing one tool" -- the intersection of one bad name with
    the offered set is empty, so the allowlist admitted nothing. The failure
    looked like a delegate that would not act rather than like a typo.
    """
    with pytest.raises(CapabilityError, match="unknown builtin_tool"):
        _build(cfg, session_dir, TYPO)


def test_the_message_names_the_tool_and_what_is_offered(cfg, session_dir):
    """A refusal nobody can act on is barely better than the silence."""
    with pytest.raises(CapabilityError) as raised:
        _build(cfg, session_dir, TYPO)

    assert "reed_file" in str(raised.value)
    assert "read_file" in str(raised.value)  # what it should have said


def test_the_wildcard_means_everything(cfg, session_dir):
    """`["*"]` used to mean the opposite of what it says: `*` matched no tool,
    so the obvious spelling of "all of them" produced a delegate with none.

    A list, because every selection in this format is a list and a field whose
    type changes with its value is one more thing to know.
    """
    graph = _build(cfg, session_dir, STAR)

    delegate = _subagent_graphs(graph).get("helper")
    assert delegate is not None

    spec = read_subagent(STAR, Path("helper.yaml"))
    assert spec.builtin_tools == ALL
    assert spec.tools == ALL


def test_the_bare_star_is_refused_by_name(cfg, session_dir):
    """A request spells this `"*"`, so someone will carry the habit across.

    Refused rather than accepted, the same trade `system_prompt` makes by
    taking one block style and naming the others: two spellings both end up in
    the wild and every reader has to know both.
    """
    with pytest.raises(SubagentError, match=r"write \['\*'\] instead"):
        read_subagent(BARE_STAR, Path("helper.yaml"))


def test_mixing_the_wildcard_with_a_name_is_refused(cfg, session_dir):
    """`["*", read_file]` has no reading that is not a guess, and it used to
    have the worst one -- `*` matched nothing, so the star contributed nothing
    and the line quietly meant `[read_file]`.
    """
    with pytest.raises(SubagentError, match="mixes"):
        read_subagent(MIXED, Path("helper.yaml"))


def test_a_tool_the_request_withheld_is_still_dropped(cfg, session_dir):
    """The half that must *not* change. A name that exists but this request did
    not grant is a caller being narrower than the definition, which is not a
    mistake -- so it is dropped, exactly as before.
    """
    definition = TYPO.replace("reed_file", "read_file, execute")

    graph = build_agent(
        _with_helper(cfg, definition),
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(builtin_tools=("read_file", "task"), subagents=("helper",)),
    )

    delegate = _subagent_graphs(graph).get("helper")
    assert delegate is not None  # built, not refused


def test_a_definition_naming_no_tools_is_unaffected(cfg, session_dir):
    """The check costs an extra assembly, so it only runs when a definition
    actually names a tool. This is the path that still skips it."""
    graph = _build(cfg, session_dir, HELPER)

    assert _subagent_graphs(graph).get("helper") is not None


def test_a_builtin_named_under_tools_says_which_list_it_belongs_in(cfg, session_dir):
    """The mistake this split creates, and the one every definition written
    before it will make: `tools: [read_file]` was correct until now.

    Falling through to "unknown tool: read_file" would send someone looking for
    a bug in kingfisher, because `read_file` plainly exists. `_refuse_unknown_tools`
    says the same thing to a request, for the same reason.
    """
    definition = TYPO.replace("builtin_tools: [reed_file]", "tools: [read_file]")

    with pytest.raises(CapabilityError, match="name it in builtin_tools"):
        _build(cfg, session_dir, definition)


def test_the_wrong_list_message_agrees_in_number(cfg, session_dir):
    """Five names and "it is a builtin tool" reads as machine output.

    This is the message every definition written before the two tool lists
    will hit, so it is the one worth reading like a sentence. Found by running
    a real migration, not by review.
    """
    one = TYPO.replace("builtin_tools: [reed_file]", "tools: [read_file]")
    many = TYPO.replace("builtin_tools: [reed_file]", "tools: [read_file, ls, glob]")

    with pytest.raises(CapabilityError, match="that is a builtin tool -- name it in"):
        _build(cfg, session_dir, one)

    with pytest.raises(CapabilityError, match="those are builtin tools -- name them in"):
        _build(cfg, session_dir, many)


def test_a_missing_subagent_type_says_so_rather_than_naming_none():
    """A missing argument is a different mistake from a refused name.

    Found by a live run. The model sent `subagentType`, so the tool saw no
    `subagent_type` at all, and the refusal read: "None is not a delegate this
    request may use. Available: general-purpose, reviewer, second-opinion." The
    model reported the tool as broken -- "despite listing reviewer as
    available" -- and answered around it instead of retrying with the right
    key. The name it could not find was its own typo, and nothing said so.
    """
    from kingfisher.infrastructure.harness.narrowing import DeclaredDelegatesOnly

    class _Call:
        tool_call = {"name": "task", "args": {"subagentType": "reviewer"}, "id": "c1"}

    refusal = DeclaredDelegatesOnly(("reviewer",))._refuse(_Call())

    assert refusal is not None
    assert "no subagent_type was given" in refusal.content
    assert "`subagent_type`" in refusal.content  # the spelling it needs
    assert "None" not in refusal.content  # never the value it did not send


def test_a_delegate_that_does_not_exist_still_names_it():
    """The other half, unchanged: a real name that is not on the list."""
    from kingfisher.infrastructure.harness.narrowing import DeclaredDelegatesOnly

    class _Call:
        tool_call = {"name": "task", "args": {"subagent_type": "nobody"}, "id": "c1"}

    refusal = DeclaredDelegatesOnly(("reviewer",))._refuse(_Call())

    assert refusal is not None
    assert "'nobody' is not a delegate" in refusal.content



# -- the delegate nobody declared ------------------------------------------


class _Audit(AgentMiddleware):
    """Stands in for what a deployment registers: an audit hook, a rate limit."""

    name = "_Audit"


def _gp(captured) -> dict:
    """The `general-purpose` spec handed to deepagents, or `{}` if absent."""
    for spec in captured.get("subagents", ()):
        if spec.get("name") == "general-purpose":
            return spec
    return {}


def _audited_build(cfg, monkeypatch, session_dir, **caps):
    captured = capture_build(monkeypatch)
    build_agent(
        _with_helper(cfg),
        agent=AgentSpec(
            name="probed",
            description="names the deployment's middleware",
            system_prompt="You work.",
            middleware=("audit",),
            subagents=("helper",),
        ),
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        middleware_registry={"audit": _Audit},
        capabilities=Capabilities(**caps),
    )
    return captured


def test_the_builtin_delegate_carries_the_deployments_middleware(cfg, monkeypatch, session_dir):
    """An audit hook that can be stepped around by naming one delegate is not
    an audit hook.

    `general-purpose` had its middleware list *replaced* with the caller's tool
    ceiling and nothing else, so a deployment's hook reached the agent and every
    delegate it declared, and missed the one delegate nobody has to declare --
    which is reachable whenever `task` is, and named in that tool's own
    description.
    """
    captured = _audited_build(cfg, monkeypatch, session_dir, builtin_tools=("read_file", "task"))

    kinds = [type(m).__name__ for m in _gp(captured).get("middleware", ())]
    assert "_Audit" in kinds, f"the built-in delegate runs unaudited: {kinds}"
    assert "ToolAllowlist" in kinds, "the ceiling it already had must survive"


def test_the_builtin_delegate_is_supplied_when_nothing_was_narrowed(cfg, monkeypatch, session_dir):
    """The replacement used to happen only for a request that narrowed
    something, because it was written to carry a ceiling. It carries the
    deployment's middleware now too, and that is owed to every run.

    Left as it was, the way to run unaudited was to ask for nothing in
    particular.
    """
    captured = _audited_build(cfg, monkeypatch, session_dir)

    kinds = [type(m).__name__ for m in _gp(captured).get("middleware", ())]
    assert "_Audit" in kinds, f"an unrestricted request runs it unaudited: {kinds}"
    assert "ToolAllowlist" not in kinds, "nothing was narrowed, so nothing to narrow it by"


def test_the_backstop_is_on_an_unrestricted_request_too(cfg, monkeypatch, session_dir):
    """`DeclaredDelegatesOnly` exists so a delegate deepagents adds in a future
    version does not arrive unnoticed. That reason has nothing to do with
    whether this caller narrowed anything, and it used to be wired only when
    they had."""
    captured = _audited_build(cfg, monkeypatch, session_dir)

    assert "DeclaredDelegatesOnly" in {type(m).__name__ for m in captured["middleware"]}


def test_the_builtin_delegate_gets_its_own_instances(cfg, monkeypatch, session_dir):
    """Built per graph, like every declared delegate's -- `as_subagent` calls
    the factory again for each one rather than sharing.

    Worth pinning because it is a real choice and the other reading is
    defensible: one shared rate limiter bounds a whole turn, where one per graph
    bounds each. This follows what delegates already do; changing it should
    change it for them too.
    """
    captured = _audited_build(cfg, monkeypatch, session_dir)

    on_agent = [m for m in captured["middleware"] if type(m).__name__ == "_Audit"]
    on_builtin = [m for m in _gp(captured).get("middleware", ()) if type(m).__name__ == "_Audit"]

    assert on_agent and on_builtin
    assert on_agent[0] is not on_builtin[0], "one instance is shared between two graphs"
