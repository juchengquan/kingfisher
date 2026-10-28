"""`Kingfisher.sent` and `kingfisher prompt`: what a turn's first model call would be sent."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest
from deepagents import HarnessProfile, register_harness_profile
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, SystemMessage

from kingfisher import SENT_SOURCES, Capabilities, Kingfisher, ModelProfile, Request
from kingfisher.cli.__main__ import EMPTY_TELL, main
from kingfisher.core.agent.sent import _OPENINGS, labelled, sent_tool
from kingfisher.core.interfaces import TurnInstructions
from kingfisher.core.session.model import UnknownSessionError
from kingfisher.plugins import LocalSessions
from tests.conftest import EVERY_TOOL, FakeGraph, an_agent, pin, start, tools_dir
from tests.unit.scripted import Scripted
from tests.unit.test_interpreter import _close_every_interpreter, _quickjs_workers
from tests.unit.test_shell_paths import Here

WHERE = '''from langchain.tools import ToolRuntime

from kingfisher import ToolContext


def where(runtime: ToolRuntime[ToolContext]) -> str:
    """Say where this turn runs."""
    return "here"


TOOLS = [where]
'''

TOLD = "Today is the 26th of October."


class Told(TurnInstructions):
    """A `TurnInstructions` saying one thing to every turn."""

    def text(self, cfg, session_id, caller):
        del cfg, session_id, caller
        return TOLD


def _with_everything(cfg):
    """`cfg` with skills and memory on, a skill to index, and an agent holding it all."""
    skill = cfg.catalogue_roots["skills"] / "csv"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(
        "---\nname: csv\ndescription: Reading CSV files.\n---\nRead it.\n", encoding="utf-8"
    )
    an_agent(cfg, "only", **EVERY_TOOL, skills='["*"]')
    return replace(cfg, skills_enabled=True, memory_enabled=True)


def _kingfisher(cfg, **kwargs):
    return Kingfisher(cfg, sessions=LocalSessions(), turn_instructions=Told(), **kwargs)


def _files(session_dir):
    """Every file under a session, with its bytes and when it last changed."""
    return {
        path.relative_to(session_dir): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(session_dir.rglob("*"))
        if path.is_file()
    }


def test_what_is_shown_is_what_the_turn_sends(scripted):
    """Driven rather than inspected: the preview against the first call of a real turn in
    the same session, so a preview assembled any other way than the turn's -- another
    agent, another grant, a block left out -- shows a prompt no model is sent."""
    cfg = _with_everything(scripted)
    kf = _kingfisher(cfg)

    sent = kf.sent(Request("go", agent="only", session_id="s"))
    Scripted.script.append(AIMessage(content="done"))
    kf.run(Request("go", agent="only", session_id="s"))

    (system,) = [m for m in Scripted.heard[0] if isinstance(m, SystemMessage)]
    heard = [
        (block["text"], "cache_control" in block)
        for block in system.content
        if isinstance(block, dict)
    ]
    assert [(block.text, block.cached) for block in sent.system] == heard
    # The control: the comparison is over something, every source of it named.
    assert [block.source for block in sent.system] == [
        "kingfisher", "write_todos", "skills", "turn", "memory",
    ]


def test_no_model_is_called_and_a_session_made_to_look_is_gone(scripted):
    """The preview's whole promise: a model reached is a call paid for, and a session left
    behind is one every later listing shows and `reap` has to find."""
    an_agent(scripted, "only")
    kf = _kingfisher(scripted)

    kf.sent(Request("go", agent="only", session_id="peek"))

    assert Scripted.heard == []
    assert [s.id for s in kf.sessions()] == []
    assert not (scripted.workspace / "sessions" / "peek").exists()


def test_a_session_made_to_look_is_gone_when_the_look_is_refused(scripted):
    """The refusal comes after the session is opened, and so made: kept, a request naming
    an agent that is not there would leave a session behind each time it was asked."""
    an_agent(scripted, "only")
    kf = _kingfisher(scripted)

    with pytest.raises(Exception, match="ghost"):
        kf.sent(Request("go", agent="ghost", session_id="peek"))

    assert [s.id for s in kf.sessions()] == []


def test_a_session_already_there_is_left_as_it_was(scripted):
    """No claim, no transcript, no last-used time moved: a preview that wrote any of them
    would change when the session is reaped, or stop the next turn as busy."""
    an_agent(scripted, "only")
    kf = _kingfisher(scripted)
    Scripted.script.append(AIMessage(content="done"))
    kf.run(Request("go", agent="only", session_id="s"))
    session_dir = scripted.workspace / "sessions" / "s"
    before = _files(session_dir)

    kf.sent(Request("go", agent="only", session_id="s"))

    assert _files(session_dir) == before
    Scripted.script.append(AIMessage(content="again"))
    assert kf.run(Request("go", agent="only", session_id="s")).answer == "again"


class Fallback(AgentMiddleware):
    """Answers in the model's place whenever its call fails, with a call to save a file."""

    def wrap_model_call(self, request, handler):
        try:
            return handler(request)
        except Exception:  # noqa: BLE001 -- what a fallback is
            return AIMessage(
                content="",
                tool_calls=[{
                    "name": "write_file",
                    "args": {"file_path": "/outputs/fallback.txt", "content": "ran"},
                    "id": "call-fallback",
                }],
            )


def test_a_fallback_answering_for_the_model_does_not_carry_the_turn_on(scripted):
    """The stop is raised as something a fallback does not catch: caught, the fallback's
    answer would be taken for the model's, and the tools it calls would run and write."""
    an_agent(scripted, "only", **EVERY_TOOL, middlewares="[fallback]")
    kf = _kingfisher(scripted, middlewares={"fallback": Fallback})
    Scripted.script.append(AIMessage(content="done"))
    kf.run(Request("go", agent="only", session_id="s"))

    kf.sent(Request("go", agent="only", session_id="s"))

    assert not (scripted.workspace / "sessions" / "s" / "outputs" / "fallback.txt").exists()


def test_no_sandbox_outlives_the_look(cfg, monkeypatch):
    """The code interpreter's runtime is pinned to a worker thread, and one left open
    deadlocks the process at exit: `kingfisher prompt` printed its listing and never
    returned, with the interpreter on."""
    monkeypatch.setenv("FAKE_KEY", "not-a-real-key")
    wired = replace(cfg, interpreter_enabled=True)
    # Listed, not `["*"]`: only a grant that names its tools hands them to the sandbox,
    # and only then does the runtime start, on the way to the model.
    an_agent(wired, "only", builtin_tools="[read_file, eval]")
    try:
        sent = _kingfisher(wired).sent(Request("go", agent="only", session_id="s"))
        remaining = _quickjs_workers()
    finally:
        _close_every_interpreter()

    assert not remaining, "a sandbox outlived the look that opened it"
    # The control: the interpreter was in the stack, and its block is named for it.
    assert "interpreter" in [block.source for block in sent.system]


class Replaces(AgentMiddleware):
    """Sends the model a system message of its own in place of the agent's."""

    def wrap_model_call(self, request, handler):
        return handler(request.override(system_message=SystemMessage("Summarise this.")))


def test_a_look_refused_at_the_call_closes_its_sandbox_too(cfg, monkeypatch):
    """The refusal comes after the runtime has started, so the teardown has to run on the
    way out as well: missed, the refusal is printed and the process never exits."""
    monkeypatch.setenv("FAKE_KEY", "not-a-real-key")
    wired = replace(cfg, interpreter_enabled=True)
    an_agent(wired, "only", builtin_tools="[read_file, eval]", middlewares="[replaces]")
    try:
        with pytest.raises(ValueError, match="was not the agent's"):
            _kingfisher(wired, middlewares={"replaces": Replaces}).sent(
                Request("go", agent="only", session_id="s")
            )
        remaining = _quickjs_workers()
    finally:
        _close_every_interpreter()

    assert not remaining, "a sandbox outlived the refused look that opened it"


def test_a_session_running_one_agent_refuses_another_as_its_turn_would(scripted):
    """A session keeps the agent it opened with, so a preview of another would show an
    agent no turn there can run."""
    an_agent(scripted, "first")
    an_agent(scripted, "second")
    kf = _kingfisher(scripted)
    Scripted.script.append(AIMessage(content="done"))
    kf.run(Request("go", agent="first", session_id="s"))

    with pytest.raises(Exception) as previewed:
        kf.sent(Request("go", agent="second", session_id="s"))
    with pytest.raises(Exception) as ran:
        kf.run(Request("go", agent="second", session_id="s"))

    assert type(previewed.value) is type(ran.value)
    assert str(previewed.value) == str(ran.value)


def test_a_session_out_of_the_callers_reach_reads_as_one_that_is_not_there(cfg):
    """Without the check a turn makes first, a caller holding another's session id would
    be shown that session's memory and its agent's prompt."""
    an_agent(cfg, "assistant", source_ids="[A]")
    an_agent(cfg, "surveyor")
    kf = _kingfisher(cfg)
    session_id = start(cfg, "pinned-by-a")
    pin(kf, session_id, "assistant")

    with pytest.raises(UnknownSessionError):
        kf.sent(Request("again", session_id=session_id), source_ids=("B",))


def test_each_block_and_tool_is_named_for_what_wrote_it(cfg, monkeypatch):
    """On the catalogue's own `ChatAnthropic`, whose request carries the cache marks and
    the tools a scripted model never binds, and through a runner on this host that names
    no session root, the one shell whose agent is still sent deepagents' host paths."""
    monkeypatch.setenv("FAKE_KEY", "not-a-real-key")
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "where.py").write_text(WHERE, encoding="utf-8")
    kf = Kingfisher(
        _with_everything(cfg),
        sessions=LocalSessions(runner=lambda _where: Here()),
        turn_instructions=Told(),
    )

    sent = kf.sent(Request("go", agent="only", session_id="s"))

    assert [block.source for block in sent.system] == [
        "kingfisher", "host_paths", "write_todos", "skills", "turn", "memory",
    ]
    assert any(block.cached for block in sent.system)
    offered = {tool.name: tool.offered for tool in sent.tools}
    assert offered["where"] is True
    assert offered["ls"] is False
    assert sent.tools[-1].cached


def test_a_models_profile_is_named_apart_from_kingfishers_prompt(cfg, monkeypatch):
    """deepagents writes a model's profile onto the end of kingfisher's own block, so
    unsplit it would read as something kingfisher says. A profile registered here, for a
    model of this test's own, so it holds whatever profiles deepagents ships."""
    monkeypatch.setenv("FAKE_KEY", "not-a-real-key")
    register_harness_profile(
        "anthropic:kingfisher-profiled", HarnessProfile(system_prompt_suffix="Be brief.")
    )
    models = replace(
        cfg.models,
        models={
            **cfg.models.models,
            "kingfisher-profiled": ModelProfile(model="kingfisher-profiled", endpoint="fake"),
        },
        default="kingfisher-profiled",
    )
    an_agent(cfg, "only")
    kf = _kingfisher(replace(cfg, models=models))

    sent = kf.sent(Request("go", agent="only", session_id="s"))

    assert [block.source for block in sent.system[:2]] == ["kingfisher", "profile"]
    assert sent.system[1].text.strip() == "Be brief."


def test_a_tool_the_deployment_withholds_is_not_shown(cfg, monkeypatch):
    """The request is narrowed by the deployment's grant before the build, as a turn's is:
    shown the request alone, a preview would list a tool no turn here is offered."""
    monkeypatch.setenv("FAKE_KEY", "not-a-real-key")
    an_agent(cfg, "only", **EVERY_TOOL)
    withheld = replace(cfg, grants=Capabilities(builtin_tools=("read_file",)))

    sent = _kingfisher(withheld).sent(Request("go", agent="only", session_id="s"))

    assert [tool.name for tool in sent.tools] == ["read_file"]


def test_asent_answers_what_sent_does(cfg, monkeypatch):
    """The twin a caller on an event loop uses, built on kingfisher's pool."""
    monkeypatch.setenv("FAKE_KEY", "not-a-real-key")
    an_agent(cfg, "only", **EVERY_TOOL)
    kf = _kingfisher(cfg)
    request = Request("go", agent="only", session_id="s")

    assert asyncio.run(kf.asent(request)) == kf.sent(request)


def test_inputs_and_a_supplied_graph_are_refused(cfg):
    """Inputs are placed by writing them, and a graph the deployment built is one
    kingfisher cannot name the parts of; either taken silently would mislead."""
    an_agent(cfg, "only")

    with pytest.raises(ValueError, match="places no inputs"):
        _kingfisher(cfg).sent(Request("go", agent="only", inputs=(cfg.workspace,)))
    with pytest.raises(ValueError, match="supplied its own graph"):
        _kingfisher(cfg, graph=FakeGraph()).sent(Request("go", agent="only"))


def test_a_first_call_that_is_not_the_agents_is_refused():
    """A deployment's middleware calling a model first would otherwise be shown as what
    the agent is sent."""
    with pytest.raises(ValueError, match="was not the agent's"):
        labelled([("Summarise this conversation.", False)], "You are an agent.", None)

    # The control: the agent's own prompt passes.
    assert labelled([("You are an agent.", True)], "You are an agent.", None)[0].cached


def test_a_block_nothing_here_wrote_is_unknown_and_the_turns_text_is_matched_whole():
    """Matched on what each source writes, so a deployment's own block is not guessed at,
    and one that only quotes the turn's text is not taken for it."""
    named = labelled(
        [
            ("P", False),
            ("\n\nA deployment's own note.", False),
            (f"\n\nA note quoting it: {TOLD}", False),
            (f"\n\n{TOLD}", False),
        ],
        "P",
        TOLD,
    )

    assert [block.source for block in named] == ["kingfisher", "unknown", "unknown", "turn"]


def test_a_breakpoint_on_a_profiled_block_belongs_to_the_profile():
    """The mark sits at the end of the block, after the profile: on kingfisher's part it
    would say kingfisher's prompt is cached on its own, which no request marks."""
    named = labelled([("P\n\nBe brief.", True)], "P", None)

    assert [(block.source, block.cached) for block in named] == [
        ("kingfisher", False), ("profile", True),
    ]


def test_every_source_named_is_one_the_listing_can_describe():
    """`kingfisher prompt` looks each source up in `SENT_SOURCES`, so one named here and
    missing there fails the command at the block it reaches."""
    named = {name for name, _ in _OPENINGS} | {"kingfisher", "profile", "turn", "unknown"}

    assert named == set(SENT_SOURCES)


@pytest.mark.parametrize(
    ("tool", "parameters"),
    [
        ({"name": "ls", "description": "List.", "input_schema": {"a": 1}}, {"a": 1}),
        (
            {"type": "function", "function": {"name": "ls", "description": "List.",
                                              "parameters": {"a": 1}}},
            {"a": 1},
        ),
        ({"type": "function", "name": "ls", "description": "List.", "parameters": {"a": 1}},
         {"a": 1}),
    ],
    ids=["anthropic", "openai-chat", "openai-responses"],
)
def test_a_tool_is_read_as_each_provider_is_sent_it(tool, parameters):
    """Anthropic names the schema `input_schema` and OpenAI `parameters`, flat or under
    `function`: read one way only, the others show a tool with no name."""
    read = sent_tool(tool, frozenset({"ls"}))

    assert (read.name, read.description, read.parameters, read.offered) == (
        "ls", "List.", parameters, True,
    )


def test_the_command_prints_each_block_under_what_wrote_it(cfg, at_the_command_line, capsys):
    an_agent(cfg, "only", **EVERY_TOOL)

    assert main(["prompt", "--agent", "only", "--tell", TOLD]) == 0

    out = capsys.readouterr().out
    assert "No model was called." in out
    assert f"· {SENT_SOURCES['kingfisher']} ·" in out
    assert f"· {SENT_SOURCES['turn']} ·" in out
    assert TOLD in out
    assert "── ls · built in ·" in out


def test_the_command_answers_json_for_a_script(cfg, at_the_command_line, capsys):
    an_agent(cfg, "only", **EVERY_TOOL)

    assert main(["prompt", "--agent", "only", "--json"]) == 0

    document = json.loads(capsys.readouterr().out)
    assert document["agent"] == "only"
    assert document["system"][0]["source"] == "kingfisher"
    assert {tool["name"] for tool in document["tools"]} >= {"ls", "read_file"}


def test_the_command_refuses_an_empty_tell_and_an_agent_that_is_not_there(
    cfg, at_the_command_line, capsys
):
    """Exit 2 for both, as `run` gives a request that never ran."""
    an_agent(cfg, "only")

    assert main(["prompt", "--agent", "only", "--tell", "  "]) == 2
    assert EMPTY_TELL in capsys.readouterr().err
    assert main(["prompt", "--agent", "ghost"]) == 2
    assert "ghost" in capsys.readouterr().err
