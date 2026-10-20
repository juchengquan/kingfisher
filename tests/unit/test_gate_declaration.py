"""An agent file that says which tools need a person, and what the wiring does with it.

The other half of the approval gate. `test_approval_gates.py` drives the pause and
the answer through an injected graph; this one starts from the YAML and goes through
`build_agent`, which is the path a real deployment takes and the only one that can
show a declared gate reaching deepagents at all.

The rules at the end go one step further and run a whole turn through the service,
because what a gate *name* may be is only answerable where the call is dispatched: a
subagent is where a tool two files define, or a tool a bundle holds, is held at all.
"""

from __future__ import annotations

from dataclasses import replace as replace_cfg
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from kingfisher import Kingfisher
from kingfisher.core.access.capabilities import Capabilities
from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.definitions.agents.reading import read
from kingfisher.core.definitions.agents.spec import AgentError
from kingfisher.core.definitions.documents import DefinitionText
from kingfisher.core.request import Decision, Request, Resume
from kingfisher.core.result import AWAITING, END_TURN
from kingfisher.plugins import LocalSessions, backend_at
from tests.conftest import (
    EVERY_TOOL,
    FakeToolCallingModel,
    an_agent,
    start,
    subagents_dir,
    tools_dir,
)
from tests.unit.scripted import Scripted
from tests.unit.test_bundles import compiled_bundle
from tests.unit.test_subagent_helpers import HELPER, REVIEWER

WRITES = [
    AIMessage(
        content="",
        tool_calls=[
            {
                "name": "write_file",
                "args": {"file_path": "/outputs/a.txt", "content": "x"},
                "id": "c1",
            }
        ],
    ),
    AIMessage(content="done"),
]

A_TOOL = '''
def line_count(path: str) -> str:
    """Count the lines in a text file."""
    return "0"


TOOLS = [line_count]
'''

#: "Nothing was passed", so a test can ask for *no* checkpointer without `None`
#: meaning "give me the usual one".
_DEFAULT: Any = object()


def _agent(**fields: str) -> Any:
    # Every tool, so a gate has a held tool to fire on and a call it lets through runs.
    written = "".join(f"{key}: {value}\n" for key, value in {**EVERY_TOOL, **fields}.items())
    return read(
        DefinitionText(
            f"name: gatekeeper\ndescription: An agent.\n{written}system_prompt: |\n  Go.\n",
            Path("gatekeeper.yaml"),
        )
    )


def _run(cfg, session_dir, spec, *, checkpointer: Any = _DEFAULT, **kwargs: Any) -> Any:
    agent = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir, catalogue=kwargs.get("catalogue")),
        agent=spec,
        model=FakeToolCallingModel(responses=list(WRITES)),
        checkpointer=InMemorySaver() if checkpointer is _DEFAULT else checkpointer,
        **kwargs,
    ).graph
    return agent.invoke(
        {"messages": [{"role": "user", "content": "go"}]},
        config={"configurable": {"thread_id": session_dir.name}, "recursion_limit": 12},
    )


def test_a_declared_gate_stops_the_call(cfg, session_dir):
    """The whole slice: a line in a YAML file reaches deepagents as an interrupt."""
    out = _run(cfg, session_dir, _agent(interrupt_on="[write_file]"))

    assert "__interrupt__" in out, "the gated call ran without stopping for anyone"
    assert not (session_dir / "outputs" / "a.txt").exists()


def test_the_same_agent_without_the_line_does_not_stop(cfg, session_dir):
    """The control beside it. Without this, the assertion above passes against an
    agent that was never going to reach `write_file` in the first place.
    """
    out = _run(cfg, session_dir, _agent())

    assert "__interrupt__" not in out
    assert (session_dir / "outputs" / "a.txt").read_text() == "x"


def test_only_the_named_tool_is_gated(cfg, session_dir):
    """A gate on one tool that stopped another would be a gate nobody could reason
    about, and `write_file` running here is what shows the list is read as a list.
    """
    out = _run(cfg, session_dir, _agent(interrupt_on="[execute]"))

    assert "__interrupt__" not in out
    assert (session_dir / "outputs" / "a.txt").read_text() == "x"


def test_a_gate_on_a_name_no_tool_answers_to_is_refused(cfg, session_dir):
    """A typo'd gate is a gate that never fires, and an author who believes a call is
    gated while it runs is worse off than one whose workspace refused to start.
    """
    with pytest.raises(AgentError, match="no tool here answers to"):
        _run(cfg, session_dir, _agent(interrupt_on="[wrtie_file]"))


def test_a_gate_with_no_checkpointer_is_refused(cfg, session_dir):
    """Without a saver the pause cannot be kept, and langgraph suppresses the
    interrupt rather than raising it -- so the gated call is skipped in silence.
    That is the outcome `interrupt_on` was written to prevent, so it is refused.
    """
    stateless = replace_cfg(cfg, conversation_enabled=False)

    with pytest.raises(AgentError, match="no checkpointer"):
        _run(stateless, session_dir, _agent(interrupt_on="[write_file]"), checkpointer=None)


def test_an_agent_with_no_gates_needs_no_checkpointer(cfg, session_dir):
    """The refusal above must not reach every stateless deployment, which is most of
    the reason it is asked of the gate list rather than of the flag.
    """
    stateless = replace_cfg(cfg, conversation_enabled=False)

    out = _run(stateless, session_dir, _agent(), checkpointer=None)

    assert "__interrupt__" not in out


def test_the_gates_reach_deepagents_as_the_argument_a_subagent_inherits(
    cfg, session_dir
):
    """A subagent cannot declare `interrupt_on` -- the field is `REFUSED` in the
    subagent format -- so what it stops on has to arrive from its parent, or a
    workspace could gate the shell and lose the gate the moment work was delegated.

    What this checks is the agent's own argument. The pause itself is driven at the end
    of this file, where a turn runs through the service, a helper two levels down
    included: a change in how the mapping reaches a subagent passes here and fails there.
    """
    directory = cfg.definitions / "subagents"
    directory.mkdir(exist_ok=True)
    (directory / "scribe.yaml").write_text(
        "name: scribe\ndescription: Writes.\nsystem_prompt: |\n  Write.\n", encoding="utf-8"
    )

    built = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        agent=_agent(interrupt_on="[write_file]", subagents="[scribe]"),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        checkpointer=InMemorySaver(),
    )

    assert built.interrupt_on == {"write_file": True}


def test_an_agent_that_gates_nothing_passes_no_gate_argument(cfg, session_dir):
    """Passing an empty mapping would make deepagents install the middleware for every
    agent in every workspace, for gates that are not there.
    """
    built = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        agent=_agent(),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        checkpointer=InMemorySaver(),
    )

    # `is None` rather than a key check: the record declares every keyword, so
    # "not passed" is the default value rather than an absent entry.
    assert built.interrupt_on is None


def test_a_gate_on_a_tool_this_caller_was_not_granted_is_allowed(cfg, session_dir):
    """A grant narrows per request, so an agent gating a workspace tool that this
    caller did not ask for has a gate with nothing to fire on -- which is fine.
    Refusing it would break a legitimately narrow run for a danger that is not there.
    """
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "line_count.py").write_text(A_TOOL, encoding="utf-8")

    out = _run(
        cfg,
        session_dir,
        _agent(interrupt_on="[line_count]"),
        capabilities=Capabilities(tools=None),
    )

    assert "__interrupt__" not in out, "a tool nobody was granted still gated something"
    assert (session_dir / "outputs" / "a.txt").read_text() == "x"


FETCHES = '''
def fetch(what: str) -> str:
    """Fetch a thing, from VENDOR."""
    return "from VENDOR"


TOOLS = [fetch]
'''

MASKS = '''
def mask(what: str) -> str:
    """Mask a thing."""
    return "masked"


TOOLS = [mask]
'''


def _two_vendors(cfg) -> None:
    """Two files each defining a `fetch`, which is what makes the name ambiguous."""
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    for vendor in ("vendor_a", "vendor_b"):
        (tools_dir(cfg) / f"{vendor}.py").write_text(
            FETCHES.replace("VENDOR", vendor), encoding="utf-8"
        )


def _a_subagent_holding(cfg, grant: str) -> None:
    """A subagent that holds the tool and calls it.

    Where the holder has to be for the ambiguous case: an agent carries nothing a
    second file also defines, so the only graph a `fetch` exists in is a subagent's.
    """
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "runner.yaml").write_text(
        f"name: runner\ndescription: Fetches.\ntools: [{grant}]\nbuiltin_tools: []\n"
        f"system_prompt: |\n  Fetch it.\n",
        encoding="utf-8",
    )


def _a_bundle(cfg) -> None:
    """A subagent with a tool of its own, in the folder named after it."""
    folder = subagents_dir(cfg) / "redactor"
    (folder / "tools").mkdir(parents=True, exist_ok=True)
    (folder / "tools" / "mask.py").write_text(MASKS, encoding="utf-8")
    (folder / "redactor.yaml").write_text(
        "name: redactor\ndescription: Masks.\ntools:\n  - name: mask\n    source: bundled\n"
        "builtin_tools: []\nsystem_prompt: |\n  Mask it.\n",
        encoding="utf-8",
    )


def _delegated(cfg, subagent: str, tool: str, *, gate: str | None) -> Any:
    """One turn, through the service: the agent delegates and the subagent calls `tool`.

    Through `Kingfisher` rather than `build_agent`, because a subagent resolves its own
    model and only the service's catalogue row can script it.
    """
    gated = {"interrupt_on": f"[{gate}]"} if gate is not None else {}
    an_agent(cfg, subagents=f"[{subagent}]", **gated, **EVERY_TOOL)
    Scripted.script.extend(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {"description": "do it", "subagent_type": subagent},
                        "id": "p1",
                    }
                ],
            ),
            AIMessage(
                content="", tool_calls=[{"name": tool, "args": {"what": "x"}, "id": "d1"}]
            ),
            AIMessage(content="subagent done"),
            AIMessage(content="parent done"),
        ]
    )
    start(cfg, "s")
    service = Kingfisher(cfg, sessions=LocalSessions())
    return service.run(Request("go", agent="only", session_id="s"))


def test_a_gate_on_a_name_two_files_define_stops_the_subagent_that_holds_one(scripted):
    """A gate was judged against the spelling a *grant* uses, which is the long
    `vendor_a.py::fetch` once two files define `fetch` -- so the bare name was refused,
    the long one accepted, and deepagents, which keys an interrupt by the name the call
    carries, matched neither: the subagent ran the gated tool unapproved, in the only
    graph it exists in.
    """
    _two_vendors(scripted)
    _a_subagent_holding(scripted, "vendor_a.py::fetch")

    result = _delegated(scripted, "runner", "fetch", gate="fetch")

    assert result.stop_reason == AWAITING, "the subagent's gated call ran unapproved"
    assert [call.tool for call in result.pending] == ["fetch"]
    # The ledger, not the pause alone: a turn can stop at a gate after the call it
    # gated has already returned, and that is the failure, not the fix.
    assert not result.ledger, "the gated tool returned a result, so it had run"


def test_the_same_delegation_without_the_gate_runs_it(scripted):
    """The control. Without it the rule above passes against a subagent that never
    reached `fetch` -- which is what a grant it could not spell would do.
    """
    _two_vendors(scripted)
    _a_subagent_holding(scripted, "vendor_a.py::fetch")

    result = _delegated(scripted, "runner", "fetch", gate=None)

    assert result.stop_reason != AWAITING
    assert [(entry.role, entry.tool) for entry in result.ledger] == [
        ("working", "fetch"),
        ("main", "task"),
    ]


def test_a_gate_naming_the_file_a_tool_comes_from_is_refused(scripted):
    """`interrupt_on: [vendor_a.py::fetch]` is the spelling a `tools:` entry takes, and
    it built -- then gated nothing. It cannot be honoured rather than merely resolved:
    a call carries the name alone, so a gate on one of two files is a gate on both.
    """
    _two_vendors(scripted)
    _a_subagent_holding(scripted, "vendor_a.py::fetch")

    with pytest.raises(AgentError, match="write fetch"):
        _delegated(scripted, "runner", "fetch", gate="vendor_a.py::fetch")


def test_a_subagents_own_bundled_tool_can_be_gated(scripted):
    """A bundle's tool was judged against what the catalogue offers, so every spelling
    of it was refused as a name nothing answers to -- while deepagents hands a subagent
    the parent's gates, so the one graph it can be called in was the one place no gate
    could reach it.
    """
    _a_bundle(scripted)

    result = _delegated(scripted, "redactor", "mask", gate="mask")

    assert result.stop_reason == AWAITING, "a bundled tool ran unapproved under a gate"
    assert [call.tool for call in result.pending] == ["mask"]
    assert not result.ledger, "the gated tool returned a result, so it had run"


def test_the_same_bundle_without_the_gate_runs_it(scripted):
    """The control for the rule above."""
    _a_bundle(scripted)

    result = _delegated(scripted, "redactor", "mask", gate=None)

    assert result.stop_reason != AWAITING
    assert [(entry.role, entry.tool) for entry in result.ledger] == [
        ("working", "mask"),
        ("main", "task"),
    ]


def test_a_gate_on_a_tool_only_a_compiled_subagent_holds_is_refused(cfg, session_dir):
    """A gate on a name only a compiled subagent's own folder holds cannot fire:
    deepagents runs a compiled graph as it was given and hands it none of the parent's
    gates, and kingfisher adds no middleware to one either. Judging a gate against every
    bundled name accepts it, and a gate that is accepted and never fires is the whole
    failure `interrupt_on` is checked to prevent.
    """
    compiled_bundle(cfg)

    with pytest.raises(AgentError, match="runs a compiled graph as it was given"):
        _run(cfg, session_dir, _agent(interrupt_on="[probe]"))


def test_the_same_workspace_still_gates_a_built_in(cfg, session_dir):
    """The control: a compiled subagent is handed workspace tools and no built-in, so
    its presence must not cost the deployment the gates it can have.
    """
    compiled_bundle(cfg)

    out = _run(cfg, session_dir, _agent(interrupt_on="[write_file]"))

    assert "__interrupt__" in out


#: A catalogue tool that leaves a trace on the host when it runs, so "it did not run" is
#: a measurement rather than the absence of a ledger row -- a compiled subagent's calls go
#: unrecorded, so the ledger says nothing either way about what happened inside one.
MARKS = '''
import os
import pathlib


def shared(what: str) -> str:
    "A tool, taking the argument `_delegated` writes."
    pathlib.Path(os.environ["GATE_MARKER"]).write_text("ran")
    return "from the catalogue"


TOOLS = [shared]
'''


def _marked(cfg, tmp_path, monkeypatch):
    """`compiled_bundle`'s workspace, with the catalogue's `shared` leaving a trace."""
    compiled_bundle(cfg)
    marker = tmp_path / "ran"
    monkeypatch.setenv("GATE_MARKER", str(marker))
    (cfg.definitions / "tools" / "shared.py").write_text(MARKS, encoding="utf-8")
    return marker


def _surveyed(cfg, *, gate: str | None) -> Any:
    """One turn: the agent delegates to the compiled `surveyor`, which calls `shared`."""
    return _delegated(cfg, "surveyor", "shared", gate=gate)


def test_a_gated_catalogue_tool_is_not_handed_to_a_compiled_subagent(
    scripted, tmp_path, monkeypatch
):
    """The gate cannot fire inside a graph deepagents was handed, so the tool is kept out
    of it instead -- `eval`'s rule, in the only other place a call meets no gate. Left in,
    it ran with nobody asked: measured, the tool wrote its marker and the turn ended.
    """
    marker = _marked(scripted, tmp_path, monkeypatch)

    result = _surveyed(scripted, gate="shared")

    assert not marker.exists(), "the gated tool ran inside the compiled subagent"
    # And the turn goes on rather than dying: the subagent is told the tool is not one of
    # its own, which is what `eval` answers a program asking for a gated tool.
    assert result.stop_reason != AWAITING, "nothing was gated, so nothing should pause"
    assert result.answer == "parent done"


def test_the_same_compiled_subagent_without_the_gate_is_handed_it(
    scripted, tmp_path, monkeypatch
):
    """The control. Without it the rule above passes against a compiled subagent that
    was never handed `shared` in the first place, which is most ways of getting the
    fixture wrong.
    """
    marker = _marked(scripted, tmp_path, monkeypatch)

    _surveyed(scripted, gate=None)

    assert marker.exists(), "the compiled subagent never called the catalogue's tool"


def _consulted(cfg) -> Any:
    """The agent delegates to `reviewer`, which consults `second-opinion`, which writes."""
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    for body in (REVIEWER, HELPER):
        name = body.split("\n")[0].removeprefix("name: ")
        (subagents_dir(cfg) / f"{name}.yaml").write_text(body, encoding="utf-8")
    an_agent(cfg, subagents="[reviewer, second-opinion]", interrupt_on="[write_file]", **EVERY_TOOL)
    Scripted.script.extend(
        [
            *(
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "task",
                            "args": {"description": "check it", "subagent_type": who},
                            "id": f"t-{who}",
                        }
                    ],
                )
                for who in ("reviewer", "second-opinion")
            ),
            WRITES[0],
            AIMessage(content="helper done"),
            AIMessage(content="reviewer done"),
            AIMessage(content="parent done"),
        ]
    )
    start(cfg, "s")
    return Kingfisher(cfg, sessions=LocalSessions())


@pytest.mark.parametrize(("action", "written"), [("approve", True), ("reject", False)])
def test_a_helper_two_levels_down_stops_at_the_agents_gate(scripted, action, written):
    """A helper is built by a `SubAgentMiddleware` kingfisher constructs, which deepagents
    hands none of the agent's gates, so `second-opinion` wrote the gated file unpaused.
    Answered as well as raised: a pause nobody can resume is no better than none.
    """
    service = _consulted(scripted)
    written_to = scripted.workspace / "sessions" / "s" / "outputs" / "a.txt"

    paused = service.run(Request("go", agent="only", session_id="s"))

    assert paused.stop_reason == AWAITING, "the helper's gated write ran unapproved"
    assert [call.tool for call in paused.pending] == ["write_file"]
    assert not written_to.exists()

    answered = service.run(
        Resume(
            session_id="s",
            decisions=(Decision(call_id=paused.pending[0].call_id, action=action),),
        )
    )

    assert answered.stop_reason == END_TURN
    assert answered.answer == "parent done"
    assert written_to.exists() is written
