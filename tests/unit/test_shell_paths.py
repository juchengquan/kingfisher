"""What whoever holds the shell is told of its paths, and told again on a miss."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest
from deepagents.backends import LocalShellBackend
from langchain.agents.middleware.types import ModelRequest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.types import Command

from kingfisher import Kingfisher, Request
from kingfisher.core.agent.middlewares.builtin_tools import (
    ABSOLUTE,
    BACKENDS_ONLY,
    MAPPINGS,
    SANDBOXED,
    BuiltinToolsMiddleware,
    hint,
    kept_note,
    stray_note,
    timeout_line,
)
from kingfisher.core.agent.prompting import execute_line
from kingfisher.core.interfaces import CommandResult, CommandRunner
from kingfisher.core.layout import DEFAULT_LAYOUT, Layout
from kingfisher.plugins import LocalSessions, ShellPrompt
from kingfisher.plugins.sessions.backend import WorkspaceScopedBackend
from kingfisher.plugins.sessions.shell import kingfishers_shell
from tests.conftest import EVERY_TOOL, an_agent, hosts_shell, start, subagents_dir
from tests.unit.scripted import Scripted
from tests.unit.test_shell_prompt import Plain

#: One skill, for a graph that must hold one to be told how the shell reaches them.
DEMO_SKILL = "---\nname: demo\ndescription: D.\n---\nGo.\n"

CHECKER = """name: checker
description: Checks.
builtin_tools: [execute]
system_prompt: |
  You check.
"""


@pytest.fixture
def shelled(scripted, monkeypatch):
    """An agent holding the shell, consulting a subagent that holds it too, with the tools
    each model call was offered kept in order.
    """
    offered: list[list[Any]] = []

    def bind(self, tools, **kwargs):
        offered.append(list(tools))
        return self

    monkeypatch.setattr(Scripted, "bind_tools", bind)
    subagents_dir(scripted).mkdir(parents=True, exist_ok=True)
    (subagents_dir(scripted) / "checker.yaml").write_text(CHECKER, encoding="utf-8")
    an_agent(scripted, subagents="[checker]", **EVERY_TOOL)
    start(scripted, "s")
    return scripted, offered


def _run(cfg, *script: AIMessage, backends: Any = None, session: str = "s") -> None:
    Scripted.script.extend(script)
    Kingfisher(cfg, sessions=backends or LocalSessions()).run(
        Request("go", agent="only", session_id=session)
    )


def _consulting(subagent: str) -> tuple[AIMessage, ...]:
    """The agent asking `subagent`, which answers, and the agent finishing: three model
    calls, so the subagent's is one of them rather than assumed.
    """
    return (
        _calls(("task", {"description": "check", "subagent_type": subagent}, "t1")),
        AIMessage(content="checked"),
        AIMessage(content="done"),
    )


def _calls(*made: tuple[str, dict[str, Any], str]) -> AIMessage:
    return AIMessage(
        content="", tool_calls=[{"name": name, "args": args, "id": at} for name, args, at in made]
    )


def _execute(tools: list[Any]) -> str:
    (described,) = [tool.description for tool in tools if getattr(tool, "name", None) == "execute"]
    return described


@pytest.mark.parametrize("subagent", ["checker", "general-purpose"])
def test_every_graph_holding_the_shell_is_told_its_paths_rather_than_absolute_ones(
    shelled, subagent
):
    """deepagents tells `execute`'s holder to use absolute paths, and the only absolute
    paths a model knows are the file tools': the shipped `reviewer`, a subagent and so
    never shown the harness's shell section, ran `open('/inputs/cars.csv')` in the shell
    and failed.
    """
    cfg, offered = shelled
    rule = execute_line(hosts_shell(cfg), DEFAULT_LAYOUT)
    assert rule is not None
    _run(cfg, *_consulting(subagent))
    assert len(offered) == 3
    for tools in offered:
        described = _execute(tools)
        assert ABSOLUTE not in described
        assert "absolute paths" not in described, "deepagents reworded its advice"
        assert rule in described


def test_the_shell_is_taught_one_spelling_and_not_a_relative_one(shelled):
    """Taught to drop the slash, the agent wrote relative paths, which miss once anything
    moves the working directory. Read off what the model was sent -- the system prompt and
    `execute`'s description -- since the shipped text reaches it only once filled in.
    """
    cfg, offered = shelled
    # A skill held, since only an agent holding one is told how the shell reaches them.
    skill = cfg.catalogue_roots["skills"] / "demo"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(DEMO_SKILL, encoding="utf-8")
    an_agent(cfg, subagents="[checker]", skills='["*"]', **EVERY_TOOL)
    _run(replace(cfg, skills_enabled=True), AIMessage(content="done"))
    (system,) = [m for m in Scripted.heard[0] if isinstance(m, SystemMessage)]
    told = " ".join(f"{system.text}\n{_execute(offered[0])}".split())

    relative = [
        span
        for span in re.findall(r"`([^`]*)`", told)
        if span.split("/")[0] in cfg.layout.session_dirs
    ]
    assert not relative
    for gone in ("leading slash", "avoid `cd`", "dropping the slash"):
        assert gone not in told, gone
    # The control: the rule is there to be read, the `/skills` exception included.
    assert '`"$KINGFISHER_SESSION_ROOT/inputs/data.csv"`' in told
    assert 'os.environ["KINGFISHER_SESSION_ROOT"]' in told
    assert "`$KINGFISHER_SESSION_ROOT/skills` names a directory that does not exist" in told


#: Where a shell says it sees the session, which the corrections are named against.
SESSION = "/host/sessions/s"


THEIRS = ShellPrompt(execute="- The shell holds this session at `/workspace/{{inputs}}`.")


@pytest.mark.parametrize("subagent", ["checker", "general-purpose"])
def test_a_shell_that_says_nothing_keeps_deepagents_line_in_every_graph(shelled, subagent):
    """Kingfisher's line says the shell starts in the session root, which a runner's
    shell is not known to: told it, every graph holding that shell was told something
    nobody knew to be true.
    """
    cfg, offered = shelled
    _run(cfg, *_consulting(subagent), backends=LocalSessions(runner=lambda _where: Plain()))

    assert len(offered) == 3
    for tools in offered:
        assert ABSOLUTE in _execute(tools)
    (system,) = [m for m in Scripted.heard[0] if isinstance(m, SystemMessage)]
    assert "check what the shell sees" in " ".join(system.text.split())
    assert "$TMPDIR" not in system.text


@pytest.mark.parametrize("subagent", ["checker", "general-purpose"])
def test_a_shell_s_own_line_reaches_every_graph_holding_it(shelled, subagent):
    """A subagent sees nothing of the system prompt, so the description is the one place
    a deployment's shell can tell it how paths are spelt there.
    """
    cfg, offered = shelled
    backends = LocalSessions(runner=lambda _where: Plain(), shell_prompt=THEIRS)
    _run(cfg, *_consulting(subagent), backends=backends)

    assert len(offered) == 3
    for tools in offered:
        described = _execute(tools)
        assert "- The shell holds this session at `/workspace/inputs`." in described
        assert ABSOLUTE not in described


@pytest.mark.parametrize("shell", ["own", "silent", "theirs"])
def test_no_shell_is_called_a_sandbox_or_said_to_need_a_protocol(shelled, shell):
    """deepagents called every shell "an isolated sandbox", which kingfisher's own runs on
    the host and is not, and ended on a note to whoever wires a backend. Asked by word as
    well as by the lines held here, so a rewording upstream is a failure, not a pass."""
    from deepagents.middleware.filesystem import EXECUTE_TOOL_DESCRIPTION

    cfg, offered = shelled
    backends = {
        "own": LocalSessions(),
        "silent": LocalSessions(runner=lambda _where: Plain()),
        "theirs": LocalSessions(runner=lambda _where: Plain(), shell_prompt=THEIRS),
    }[shell]

    _run(cfg, *_consulting("checker"), backends=backends)

    assert SANDBOXED in EXECUTE_TOOL_DESCRIPTION, "deepagents reworded it"
    assert BACKENDS_ONLY in EXECUTE_TOOL_DESCRIPTION, "deepagents reworded it"
    assert len(offered) == 3
    for tools in offered:
        described = _execute(tools)
        assert "sandbox" not in described.lower()
        # The control: the rest of deepagents' description is still there.
        assert "returns combined stdout/stderr with the exit code" in described


def test_a_subagent_is_told_the_inputs_folder_the_layout_names(shelled):
    """A subagent's guards were built without the layout, so under a renamed one its
    `execute` line went on saying `/inputs` while the session held `/in`.
    """
    cfg, offered = shelled
    _run(replace(cfg, layout=Layout(inputs="in")), *_consulting("checker"), session="moved")

    assert len(offered) == 3
    for tools in offered:
        assert '`/in/data.csv` is `"$KINGFISHER_SESSION_ROOT/in/data.csv"`' in _execute(tools)


class Here(Plain):
    """A runner a deployment supplies that runs its commands on this host."""

    @property
    def local(self) -> bool:
        return True


class Theirs(LocalShellBackend):
    """A deployment's own shell, of deepagents' local class, which deepagents gives this
    host's paths to whatever the shell is.
    """


class Sandboxed(LocalSessions):
    """The adjustment `ports.md` shows: kingfisher's routes, over a shell of its own."""

    def open(self, cfg_, session_id, /, *, catalogue=None):
        mine = super().open(cfg_, session_id, catalogue=catalogue)
        return WorkspaceScopedBackend(
            Theirs(root_dir=mine.default.cwd, virtual_mode=True),
            mine.routes,
            session_dir=mine._session_dir,
            workspace=mine.workspace,
            layout=DEFAULT_LAYOUT,
        )


def _systems() -> list[str]:
    """The system prompt each model call was sent, in order."""
    return [
        message.text
        for heard in Scripted.heard
        for message in heard
        if isinstance(message, SystemMessage)
    ]


@pytest.mark.parametrize(
    ("backends", "told"),
    [
        (LocalSessions(), False),
        (LocalSessions(runner=lambda _where: Here()), True),
        (
            LocalSessions(
                runner=lambda _where: Here(),
                session_root=lambda folder: f"/home/runner/job-{folder.name}",
            ),
            False,
        ),
        (
            LocalSessions(
                runner=lambda _where: Here(),
                shell_prompt=ShellPrompt(prompt=""),
                session_root=lambda folder: f"/home/runner/job-{folder.name}",
            ),
            False,
        ),
        (
            LocalSessions(
                runner=lambda _where: Here(),
                shell_prompt=ShellPrompt(prompt="", execute=""),
                session_root=lambda folder: f"/home/runner/job-{folder.name}",
            ),
            True,
        ),
        (LocalSessions(runner=lambda _where: Plain()), False),
        (Sandboxed(), False),
    ],
    ids=[
        "kingfishers-own",
        "local-runner",
        "local-runner-naming-its-root",
        "root-named-in-execute-alone",
        "root-named-nowhere",
        "remote-runner",
        "their-own-shell",
    ],
)
def test_deepagents_host_paths_reach_only_a_shell_on_this_host(shelled, backends, told):
    """deepagents gives each routed folder's path on this host to any shell of its local
    class, and kingfisher's is that class whatever its runner: an agent whose commands
    ran on another machine was told to use paths only this one has. And a graph whose
    `execute` line names the session root was handed every literal host path beside
    "never paste the path", which also made each session's system message its own. Driven
    through the agent and a subagent, because each is sent the section by a middleware of
    its own, and each reads `execute`'s line.
    """
    cfg, _ = shelled
    _run(cfg, *_consulting("checker"), backends=backends)

    systems = _systems()
    assert len(systems) == 3
    assert [MAPPINGS in system for system in systems] == [told, told, told]
    # Only the section goes: the rest of what each was told is still there.
    assert "## The session" in systems[0]
    assert "You check." in systems[1]


HOLDER = """name: holder
description: Holds a skill and the shell.
builtin_tools: [read_file, execute]
skills: [demo]
system_prompt: |
  You hold a skill.
"""


@pytest.mark.parametrize(
    ("backends", "told"),
    [
        (LocalSessions(), False),
        (
            LocalSessions(
                runner=lambda _where: Here(),
                session_root=lambda folder: f"/home/runner/job-{folder.name}",
            ),
            True,
        ),
    ],
    ids=["kingfishers-own", "local-runner-naming-its-root"],
)
# Both grants: every skill's index comes from beside the agent's stack, not from it.
@pytest.mark.parametrize("grant", ["[demo]", '["*"]'], ids=["named", "every skill"])
def test_a_graph_holding_skills_keeps_the_host_paths_until_told_how_the_shell_reaches_them(
    shelled, backends, told, grant
):
    """The list is the one place a shell is told where `/skills` is on this host. Taken
    from a graph holding skills whose shell says nothing of reaching them -- a runner's,
    which exports no `$KINGFISHER_SKILLS` -- it left no way to a skill's script; with
    kingfisher's own shell, every skills index now says how."""
    cfg, _ = shelled
    skill = cfg.catalogue_roots["skills"] / "demo"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(DEMO_SKILL, encoding="utf-8")
    (subagents_dir(cfg) / "holder.yaml").write_text(HOLDER, encoding="utf-8")
    an_agent(cfg, subagents="[holder]", skills=grant, **EVERY_TOOL)

    _run(replace(cfg, skills_enabled=True), *_consulting("holder"), backends=backends)

    agent, holder, _ = _systems()
    assert [MAPPINGS in agent, MAPPINGS in holder] == [told, told]
    # The control: both were handed an index, which says how only on kingfisher's shell.
    assert "## Skills" in agent and "## Skills" in holder
    assert ("$KINGFISHER_SKILLS" in holder) is not told


@pytest.mark.parametrize(
    ("backends", "shared"),
    [(LocalSessions(), True), (LocalSessions(runner=lambda _where: Here()), False)],
    ids=["kingfishers-own", "local-runner"],
)
def test_two_sessions_send_the_agent_one_system_message(scripted, backends, shared):
    """The host paths named the session, so each session's system message was its own and
    none was ever read from another's cache. The control is a runner that names no root,
    whose agent keeps the paths and so still differs by session."""
    an_agent(scripted, **EVERY_TOOL)
    kf = Kingfisher(scripted, sessions=backends)

    first, second = (
        [block.text for block in kf.sent(Request("go", agent="only", session_id=name)).system]
        for name in ("a", "b")
    )

    assert (first == second) is shared


def _asked(request: Any) -> Any:
    """The request `BuiltinToolsMiddleware` passes on.

    `awrap_model_call` is a copy of this hook, which `test_async_twins.py` holds: reached
    by nothing, making it pass the request on untold once left the whole suite green.
    """
    asked: list[Any] = []
    middleware = BuiltinToolsMiddleware(
        Layout(), kingfishers_shell(fenced=True), longest=120, here=False
    )
    middleware.wrap_model_call(request, asked.append)
    return asked[0]


def _handed_on(system: SystemMessage) -> SystemMessage:
    """The system message `BuiltinToolsMiddleware` passes to a shell that is not on this host.

    Driven through the hook rather than the helper under it, because `here=False` is
    the whole of what the parametrized rule above covers and the branch is reached
    from nowhere else.
    """
    return _asked(
        ModelRequest(
            model=GenericFakeChatModel(messages=iter([])),
            messages=[HumanMessage("go")],
            system_message=system,
        ),
    ).system_message


def test_a_mappings_section_merged_into_other_prose_is_left_whole():
    """Cutting the text instead of dropping the block leaves nothing red: the section
    goes either way, so the rule above passes, while the prose deepagents merged it into
    silently loses its tail. The first half is the control -- without it, a middleware
    that handed every request on untouched would pass the second.
    """
    section = f"{MAPPINGS}\n- /inputs -> /host/in\n"

    on_its_own = _handed_on(
        SystemMessage(
            content_blocks=[
                {"type": "text", "text": "You work."},
                {"type": "text", "text": section},
            ]
        ),
    )
    assert list(on_its_own.content_blocks) == [{"type": "text", "text": "You work."}]

    merged = _handed_on(
        SystemMessage(content_blocks=[{"type": "text", "text": f"You work.\n\n{section}"}]),
    )
    assert list(merged.content_blocks) == [
        {"type": "text", "text": f"You work.\n\n{section}"}
    ]


# -- which tool the rule and the correction are for ------------------------


@dataclass
class Call:
    """The shape `wrap_tool_call` receives, in the field this middleware reads."""

    tool_call: dict[str, Any] = field(default_factory=dict)


#: A shell miss of a path spelt the file tools' way, which is what `missed` looks for.
MISSED = "tail: /inputs/api.log: No such file or directory"


def _answered(name: str, result: Any) -> Any:
    """What `BuiltinToolsMiddleware` hands back for one tool's result.

    `awrap_tool_call` is a copy of this hook, which `test_async_twins.py` holds: reached
    by nothing, making it return the result uncorrected once left the whole suite green.
    """
    middleware = BuiltinToolsMiddleware(
        Layout(), kingfishers_shell(fenced=True), longest=120, root=SESSION
    )
    request = Call(tool_call={"name": name, "args": {}, "id": "c1"})
    return middleware.wrap_tool_call(request, lambda _: result)


def _told_tools(tools: list[Any]) -> list[Any]:
    """The tools `BuiltinToolsMiddleware` passes on."""
    return list(
        _asked(
            ModelRequest(
                model=GenericFakeChatModel(messages=iter([])),
                messages=[HumanMessage("go")],
                tools=tools,
            ),
        ).tools
    )


def test_a_tool_offered_as_a_mapping_is_told_the_rule_by_name():
    """deepagents may offer a tool as a mapping rather than a `BaseTool`, and no graph
    the suite builds offers one: mutating the mapping branch's name check away leaves
    the whole suite green, so a file tool offered that way would carry the shell's rule.
    """
    rule = execute_line(kingfishers_shell(fenced=True), DEFAULT_LAYOUT)
    assert rule is not None

    told = _told_tools(
        [
            {"name": "execute", "description": "Run a command."},
            {"name": "read_file", "description": "Read a file."},
        ],
    )

    assert told == [
        {"name": "execute", "description": f"Run a command.\n\n{rule}\n{timeout_line(120)}"},
        {"name": "read_file", "description": "Read a file."},
    ]


def test_a_correction_reaches_the_shells_result_and_no_other_tools():
    """Without the name gate any tool's result gains the shell's advice: an offered tool
    printing a path it could not open had the model told about shell spellings on a tool
    that is not a shell. The rule driving `execute` alone passes either way.
    """
    corrected = _answered("execute", ToolMessage(MISSED, tool_call_id="c1"))
    assert hint("/inputs/api.log") in corrected.text

    left = _answered("line_count", ToolMessage(MISSED, tool_call_id="c1"))
    assert left.text == MISSED


#: A fence's refusal of a write to a folder it keeps, as a command following the prompt
#: produces one.
KEPT = f"/bin/sh: {SESSION}/inputs/new.txt: Operation not permitted"


def test_a_fence_refusal_tells_the_shells_result_which_folder_is_kept():
    """The fence names a host path, and without the note a model cannot tell it is the
    kept folder its prompt named; a tool other than the shell is left alone."""
    corrected = _answered("execute", ToolMessage(KEPT, tool_call_id="c1"))
    assert kept_note("inputs", Layout()) in corrected.text

    left = _answered("line_count", ToolMessage(KEPT, tool_call_id="c1"))
    assert left.text == KEPT


#: The same fence refusing the folder the shell starts in, which no layout names.
STRAYED = f"/bin/sh: {SESSION}/out.csv: Operation not permitted"


def test_a_refusal_no_folder_holds_tells_the_shells_result_where_writes_go():
    """A refused path no folder holds is the same mistake one step out, and gets the same
    note; a tool other than the shell is left alone."""
    corrected = _answered("execute", ToolMessage(STRAYED, tool_call_id="c1"))
    assert stray_note("/out.csv", Layout()) in corrected.text

    left = _answered("line_count", ToolMessage(STRAYED, tool_call_id="c1"))
    assert left.text == STRAYED


@pytest.mark.parametrize(
    ("output", "folder"),
    [
        (
            f"/bin/sh: {SESSION}/scratchpad/conversation_history/f.md: Operation not permitted",
            "scratchpad/conversation_history",
        ),
        (f"/bin/sh: {SESSION}/outputs/ok.txt: Permission denied", None),
    ],
    ids=["history", "outputs"],
)
def test_the_note_names_the_folder_the_fence_keeps(output, folder):
    """Which folders the middleware hands `kept_under` is its own choice, and only `/inputs`
    was driven through it, which every candidate holds: handed the session's folders
    instead, a refused history write was told `/scratchpad/` is not the agent's to write,
    and a file under `/outputs` it could not replace that `/outputs/` is not.
    """
    corrected = _answered("execute", ToolMessage(output, tool_call_id="c1"))

    if folder is None:
        assert corrected.text == output
    else:
        assert corrected.text == f"{output}\n\n{kept_note(folder, Layout())}"


def test_a_command_that_ran_is_told_nothing():
    """The control: a note on a command that worked reads as a correction, and the rules
    above would pass a middleware that appended it to everything.
    """
    ran = ToolMessage("wrote 3 rows", tool_call_id="c1")

    assert _answered("execute", ran).text == "wrote 3 rows"


def test_a_result_the_shell_could_not_have_returned_is_handed_back_untouched():
    """Two shape checks, each load-bearing and each reached by nothing. `missed` calls
    `splitlines`, so content arriving as blocks raises `AttributeError`, and a `Command`
    has no `.content` to read at all. Neither shape comes from `execute`, but a
    deployment's own middleware runs below this one and may return either.
    """
    blocks = ToolMessage(content=[{"type": "text", "text": MISSED}], tool_call_id="c1")
    assert _answered("execute", blocks) is blocks

    command = Command(update={"messages": []})
    assert _answered("execute", command) is command


# -- how long a command may ask to run -------------------------------------


def _asking(seconds: int) -> AIMessage:
    return _calls(("execute", {"command": "touch scratchpad/ran", "timeout": seconds}, "x1"))


@pytest.mark.parametrize("graph", ["agent", "checker", "general-purpose"])
def test_no_graph_runs_a_command_asking_for_longer_than_the_setting(shelled, graph):
    """deepagents let `execute` ask for up to an hour whatever the setting, so a command
    started near the deadline outlived the claim `claim_stale_after` sized to it."""
    cfg, _ = shelled
    bounded = replace(cfg, execution_timeout_s=1)
    held = 1 if graph == "agent" else 2
    if graph == "agent":
        script: tuple[AIMessage, ...] = (_asking(5), AIMessage("done"))
    else:
        asked = {"description": "check", "subagent_type": graph}
        script = (_calls(("task", asked, "t1")), _asking(5), AIMessage("x"), AIMessage("done"))
    Scripted.script.extend(script)
    kf = Kingfisher(bounded, sessions=LocalSessions())
    turn = Request("go", agent="only", session_id="s")
    kf.run(turn)

    (said,) = [m for m in Scripted.heard[held] if isinstance(m, ToolMessage)][-1:]
    assert said.status == "error"
    assert "exceeds maximum allowed (1s)" in said.text
    assert not (bounded.sessions_root / "s" / "scratchpad" / "ran").exists()


def test_a_command_asking_for_the_setting_itself_runs(shelled):
    """The control: a refusal of every `timeout` would pass the rule above."""
    cfg, _ = shelled
    Scripted.script.extend((_asking(cfg.execution_timeout_s), AIMessage("done")))

    Kingfisher(cfg, sessions=LocalSessions()).run(Request("go", agent="only", session_id="s"))

    assert (cfg.sessions_root / "s" / "scratchpad" / "ran").exists()


@dataclass
class Timed(CommandRunner):
    """A runner keeping the timeout each command was handed."""

    handed: list[int | None] = field(default_factory=list)

    @property
    def local(self) -> bool:
        return True

    def run(self, command: str, *, timeout: int | None = None) -> CommandResult:
        self.handed.append(timeout)
        return CommandResult(output="", exit_code=0, truncated=False)


def test_kingfishers_shell_hands_no_runner_more_than_the_setting(cfg):
    """A runner was handed whatever `timeout` arrived, or `None`, which a fence's
    `subprocess.run` reads as no bound at all."""
    runner = Timed()
    start(cfg, "s")
    shell = LocalSessions(runner=lambda _where: runner).open(cfg, "s").default

    shell.execute("true")
    shell.execute("true", timeout=cfg.execution_timeout_s + 100)
    shell.execute("true", timeout=1)

    assert runner.handed == [cfg.execution_timeout_s, cfg.execution_timeout_s, 1]


# -- a runner's shell that exports the session root ------------------------


def _job(session_dir: Path) -> str:
    """Where a runner given `session_root=` says its shell sees the session."""
    return f"/home/runner/job-{session_dir.name}"


#: What only an agent told of `$KINGFISHER_SESSION_ROOT` reads, filled from the layout.
SPELT = '`"$KINGFISHER_SESSION_ROOT/inputs/data.csv"`'
READ = 'os.environ["KINGFISHER_SESSION_ROOT"]'


def _system() -> str:
    (system,) = [m for m in Scripted.heard[0] if isinstance(m, SystemMessage)]
    return " ".join(system.text.split())


@pytest.mark.parametrize("subagent", ["checker", "general-purpose"])
@pytest.mark.parametrize("given", [True, False], ids=["session-root", "none"])
def test_a_runner_given_its_session_root_is_told_the_variable_in_every_graph(
    shelled, subagent, given
):
    """A deployment's shell exporting the variable was described as one that might not
    see the session at all, so its agent went on guessing paths. In `execute`'s
    description as well, which is all a subagent reads; without `session_root=` the
    neutral wording and deepagents' line stay, as the control.
    """
    cfg, offered = shelled
    backends = LocalSessions(runner=lambda _where: Plain(), session_root=_job if given else None)
    _run(cfg, *_consulting(subagent), backends=backends)

    system = _system()
    assert (SPELT in system and READ in system) is given
    assert "check what the shell sees" in system
    assert len(offered) == 3
    for tools in offered:
        described = " ".join(_execute(tools).split())
        assert (SPELT in described and READ in described) is given
        assert (ABSOLUTE in described) is not given


def test_a_runner_s_own_wording_is_followed_by_the_variable_and_an_empty_part_by_nothing(
    shelled,
):
    """`""` is how a deployment says nothing, and a sentence added to it would speak for
    it; a part with wording of its own is told the variable after it, the control.
    """
    cfg, offered = shelled
    theirs = ShellPrompt(prompt="## The shell\n\nMine.", execute="")
    backends = LocalSessions(runner=lambda _where: Plain(), shell_prompt=theirs, session_root=_job)
    _run(cfg, *_consulting("checker"), backends=backends)

    system = _system()
    assert "## The shell Mine." in system
    assert system.index("Mine.") < system.index(SPELT)
    for tools in offered:
        assert "KINGFISHER_SESSION_ROOT" not in _execute(tools)


@dataclass
class Noting(CommandRunner):
    """A runner keeping each command it was handed."""

    handed: list[str] = field(default_factory=list)

    @property
    def local(self) -> bool:
        return False

    def run(self, command: str, *, timeout: int | None = None) -> CommandResult:
        self.handed.append(command)
        return CommandResult(output="", exit_code=0, truncated=False)


def test_a_runner_exporting_the_session_root_is_handed_the_command_as_written(shelled):
    """Kingfisher says the variable to the agent and never puts it in a command: an
    `export` in front assumes the runner's shell is `sh`, and shows its logs and its
    allow-list a command the model did not write.
    """
    cfg, _ = shelled
    runner = Noting()
    written = """python3 -c 'import os; print(os.environ["KINGFISHER_SESSION_ROOT"])' """
    _run(
        cfg,
        _calls(("execute", {"command": written}, "c1")),
        AIMessage(content="done"),
        backends=LocalSessions(runner=lambda _where: runner, session_root=_job),
    )

    assert runner.handed == [written]
