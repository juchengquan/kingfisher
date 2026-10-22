"""What whoever holds the shell is told of its paths, and told again on a miss."""

from __future__ import annotations

import asyncio
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
    MAPPINGS,
    BuiltinToolsMiddleware,
    _virtual,
    hint,
    missed,
    timeout_line,
)
from kingfisher.core.agent.prompting import execute_line
from kingfisher.core.interfaces import CommandResult
from kingfisher.core.layout import DEFAULT_LAYOUT, Layout
from kingfisher.plugins import LocalSessions, ShellPrompt, backend_at
from kingfisher.plugins.sessions.backend import WorkspaceScopedBackend
from kingfisher.plugins.sessions.shell import kingfishers_shell
from tests.conftest import EVERY_TOOL, an_agent, start, subagents_dir
from tests.unit.scripted import Scripted
from tests.unit.test_shell_prompt import Plain

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
    never shown `system.md`, ran `open('/inputs/cars.csv')` in the shell and failed.
    """
    cfg, offered = shelled
    rule = execute_line(kingfishers_shell(), DEFAULT_LAYOUT)
    assert rule is not None
    _run(cfg, *_consulting(subagent))
    assert len(offered) == 3
    for tools in offered:
        described = _execute(tools)
        assert ABSOLUTE not in described
        assert "absolute paths" not in described, "deepagents reworded its advice"
        assert rule in described


def test_a_shell_call_missing_a_file_tool_path_is_told_the_shell_spelling(shelled):
    """Told the rule, the agent still wrote `open("/inputs/cars.csv")` into a script and
    ran it, and the shell's own error names the path but never the reason.
    """
    cfg, _ = shelled
    _run(
        cfg,
        _calls(
            ("execute", {"command": "cat /inputs/cars.csv"}, "c1"),
            ("execute", {"command": "cat inputs/cars.csv"}, "c2"),
        ),
        AIMessage(content="done"),
    )
    answers = {
        message.tool_call_id: message.text
        for message in Scripted.heard[-1]
        if isinstance(message, ToolMessage)
    }
    assert hint("/inputs/cars.csv") in answers["c1"]
    # The control: a miss spelt the shell's way is a missing file, and says only that.
    assert "No such file" in answers["c2"]
    assert "file-tool path" not in answers["c2"]


def test_the_correction_gives_a_spelling_the_shell_reaches_from_anywhere(shelled):
    """The correction said `inputs/cars.csv`, the relative spelling that misses after any
    `cd`. What it now gives is run as written, from `/`, through the session's own shell.
    """
    cfg, _ = shelled
    _run(
        cfg,
        _calls(("execute", {"command": "cat /outputs/cars.csv"}, "c1")),
        AIMessage(content="done"),
    )
    (answer,) = [m.text for m in Scripted.heard[-1] if isinstance(m, ToolMessage)]
    suggested = re.search(r'`("\$KINGFISHER_SESSION_ROOT/[^`]+")`', answer)
    assert suggested, answer
    session = cfg.sessions_root / "s"
    (session / cfg.layout.outputs / "cars.csv").write_text("make,model\n", encoding="utf-8")

    ran = backend_at(cfg, session).execute(f"cd / && cat {suggested.group(1)}")

    assert "make,model" in ran.output, ran.output


def test_the_shell_is_taught_one_spelling_and_not_a_relative_one(shelled):
    """Taught to drop the slash, the agent wrote relative paths, which miss once anything
    moves the working directory. Read off what the model was sent -- the system prompt and
    `execute`'s description -- since the shipped text reaches it only once filled in.
    """
    cfg, offered = shelled
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


@pytest.mark.parametrize(
    ("output", "path"),
    [
        ("FileNotFoundError: [Errno 2] No such file or directory: '/inputs/cars.csv'",
         "/inputs/cars.csv"),
        ("ls: /inputs/: No such file or directory", "/inputs/"),
        ("python3: can't open file '/scratchpad/x.py': [Errno 2] No such file or directory",
         "/scratchpad/x.py"),
        ("sh: 1: /scratchpad/run.sh: not found", "/scratchpad/run.sh"),
        ("bash: /outputs/a.csv: Read-only file system", "/outputs/a.csv"),
        # The host's spelling of a session folder, which is right.
        ("cat: /Users/me/sessions/s/inputs/x.csv: No such file or directory", None),
        ("cat: inputs/x.csv: No such file or directory", None),
        ("cat: ./inputs/x.csv: No such file or directory", None),
        # A name that only starts like a session folder.
        ("cat: /inputs.csv: No such file or directory", None),
        # A path printed beside a miss of something else.
        ("wrote /outputs/a.csv\nsh: rg: command not found", None),
    ],
)
def test_a_miss_is_read_off_the_line_naming_the_path(output, path):
    """A host path ending in a session folder's name was read as a file-tool one, and a
    correct command was told to drop a slash it never had.
    """
    assert missed(output, _virtual(DEFAULT_LAYOUT.session_dirs)) == path


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
def test_every_shell_says_its_timeout_is_in_seconds(shelled, shell):
    """Models passed milliseconds, 5000 to 40000, each refused as past deepagents' longest,
    and kingfisher's line had replaced the one that mentioned the timeout at all."""
    cfg, offered = shelled
    backends = {
        "own": LocalSessions(),
        "silent": LocalSessions(runner=lambda _where: Plain()),
        "theirs": LocalSessions(runner=lambda _where: Plain(), shell_prompt=THEIRS),
    }[shell]

    _run(cfg, *_consulting("checker"), backends=backends)

    assert len(offered) == 3
    for tools in offered:
        assert timeout_line(cfg.execution_timeout_s) in _execute(tools)


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
    ("backends", "here"),
    [
        (LocalSessions(), True),
        (LocalSessions(runner=lambda _where: Here()), True),
        (LocalSessions(runner=lambda _where: Plain()), False),
        (Sandboxed(), False),
    ],
    ids=["kingfishers-own", "local-runner", "remote-runner", "their-own-shell"],
)
def test_deepagents_host_paths_reach_only_a_shell_on_this_host(shelled, backends, here):
    """deepagents gives each routed folder's path on this host to any shell of its local
    class, and kingfisher's is that class whatever its runner: an agent whose commands
    ran on another machine was told to use paths only this one has. Driven through the
    agent and a subagent, because each is sent the section by a middleware of its own.
    """
    cfg, _ = shelled
    _run(cfg, *_consulting("checker"), backends=backends)

    systems = _systems()
    assert len(systems) == 3
    for system in systems:
        assert (MAPPINGS in system) is here
    # Only the section goes: the rest of what each was told is still there.
    assert "You are kingfisher" in systems[0]
    assert "You check." in systems[1]

def _asked(way: str, request: Any) -> Any:
    """The request `BuiltinToolsMiddleware` passes on, through whichever model hook `way` names.

    Both ways, because the hook is two methods and the async one was reached by nothing:
    making `awrap_model_call` pass the request on untold left the whole suite green,
    while the same change to its sync twin reddened the rules above.
    """
    asked: list[Any] = []
    middleware = BuiltinToolsMiddleware(Layout(), kingfishers_shell(), longest=120, here=False)
    if way == "sync":
        middleware.wrap_model_call(request, asked.append)
        return asked[0]

    async def noting(passed: Any) -> None:
        asked.append(passed)

    asyncio.run(middleware.awrap_model_call(request, noting))
    return asked[0]


def _handed_on(way: str, system: SystemMessage) -> SystemMessage:
    """The system message `BuiltinToolsMiddleware` passes to a shell that is not on this host.

    Driven through the hook rather than the helper under it, because `here=False` is
    the whole of what the parametrized rule above covers and the branch is reached
    from nowhere else.
    """
    return _asked(
        way,
        ModelRequest(
            model=GenericFakeChatModel(messages=iter([])),
            messages=[HumanMessage("go")],
            system_message=system,
        ),
    ).system_message


def test_a_mappings_section_merged_into_other_prose_is_left_whole(way):
    """Cutting the text instead of dropping the block leaves nothing red: the section
    goes either way, so the rule above passes, while the prose deepagents merged it into
    silently loses its tail. The first half is the control -- without it, a middleware
    that handed every request on untouched would pass the second.
    """
    section = f"{MAPPINGS}\n- /inputs -> /host/in\n"

    on_its_own = _handed_on(
        way,
        SystemMessage(
            content_blocks=[
                {"type": "text", "text": "You work."},
                {"type": "text", "text": section},
            ]
        ),
    )
    assert list(on_its_own.content_blocks) == [{"type": "text", "text": "You work."}]

    merged = _handed_on(
        way,
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


def _answered(way: str, name: str, result: Any) -> Any:
    """What `BuiltinToolsMiddleware` hands back for one tool's result, through the hook `way` names.

    Both ways, because the correction is two methods and the async one was reached by
    nothing: making `awrap_tool_call` return the result uncorrected left the whole suite
    green, while the same change to its sync twin reddened the rules above.
    """
    middleware = BuiltinToolsMiddleware(Layout(), kingfishers_shell(), longest=120, rooted=True)
    request = Call(tool_call={"name": name, "args": {}, "id": "c1"})
    if way == "sync":
        return middleware.wrap_tool_call(request, lambda _: result)

    async def answering(_: Any) -> Any:
        return result

    return asyncio.run(middleware.awrap_tool_call(request, answering))


def _told_tools(way: str, tools: list[Any]) -> list[Any]:
    """The tools `BuiltinToolsMiddleware` passes on, through whichever model hook `way` names."""
    return list(
        _asked(
            way,
            ModelRequest(
                model=GenericFakeChatModel(messages=iter([])),
                messages=[HumanMessage("go")],
                tools=tools,
            ),
        ).tools
    )


def test_the_shell_rule_reaches_execute_and_no_other_tool(shelled):
    """Without the name gate the rule lands on every tool offered, and what it says --
    that a path is spelt from `$KINGFISHER_SESSION_ROOT` in the shell -- is wrong advice for
    `read_file`, whose paths are the virtual ones. The rule asserting `execute` carries
    it passes just as well when every tool does.
    """
    cfg, offered = shelled
    rule = execute_line(kingfishers_shell(), DEFAULT_LAYOUT)
    assert rule is not None
    _run(cfg, *_consulting("checker"))

    every, carrying = set(), set()
    for tools in offered:
        every |= {getattr(tool, "name", None) for tool in tools}
        carrying |= {
            getattr(tool, "name", None)
            for tool in tools
            if rule in (getattr(tool, "description", "") or "")
        }
    # The control is only a control while the tool it names is on offer.
    assert "read_file" in every
    assert carrying == {"execute"}


def test_a_tool_offered_as_a_mapping_is_told_the_rule_by_name(way):
    """deepagents may offer a tool as a mapping rather than a `BaseTool`, and no graph
    the suite builds offers one: mutating the mapping branch's name check away leaves
    the whole suite green, so a file tool offered that way would carry the shell's rule.
    """
    rule = execute_line(kingfishers_shell(), DEFAULT_LAYOUT)
    assert rule is not None

    told = _told_tools(
        way,
        [
            {"name": "execute", "description": "Run a command."},
            {"name": "read_file", "description": "Read a file."},
        ],
    )

    assert told == [
        {"name": "execute", "description": f"Run a command.\n\n{rule}\n{timeout_line(120)}"},
        {"name": "read_file", "description": "Read a file."},
    ]


def test_a_correction_reaches_the_shells_result_and_no_other_tools(way):
    """Without the name gate any tool's result gains the shell's advice: an offered tool
    printing a path it could not open had the model told about shell spellings on a tool
    that is not a shell. The rule driving `execute` alone passes either way.
    """
    corrected = _answered(way, "execute", ToolMessage(MISSED, tool_call_id="c1"))
    assert hint("/inputs/api.log") in corrected.text

    left = _answered(way, "line_count", ToolMessage(MISSED, tool_call_id="c1"))
    assert left.text == MISSED


def test_a_result_the_shell_could_not_have_returned_is_handed_back_untouched(way):
    """Two shape checks, each load-bearing and each reached by nothing. `missed` calls
    `splitlines`, so content arriving as blocks raises `AttributeError`, and a `Command`
    has no `.content` to read at all. Neither shape comes from `execute`, but a
    deployment's own middleware runs below this one and may return either.
    """
    blocks = ToolMessage(content=[{"type": "text", "text": MISSED}], tool_call_id="c1")
    assert _answered(way, "execute", blocks) is blocks

    command = Command(update={"messages": []})
    assert _answered(way, "execute", command) is command


# -- how long a command may ask to run -------------------------------------


def _asking(seconds: int) -> AIMessage:
    return _calls(("execute", {"command": "touch scratchpad/ran", "timeout": seconds}, "x1"))


@pytest.mark.parametrize("graph", ["agent", "checker", "general-purpose"])
def test_no_graph_runs_a_command_asking_for_longer_than_the_setting(shelled, way, graph):
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
    if way == "sync":
        kf.run(turn)
    else:
        asyncio.run(kf.arun(turn))

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


def test_execute_is_told_the_setting_as_its_longest(shelled):
    """The description said 3,600 seconds while the setting was what a command ran for."""
    cfg, offered = shelled
    bounded = replace(cfg, execution_timeout_s=7)

    _run(bounded, AIMessage("done"))

    assert timeout_line(7) in _execute(offered[0])


@dataclass
class Timed:
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


@pytest.mark.parametrize("asked", [0, -1])
def test_kingfishers_shell_refuses_a_timeout_that_is_not_positive(cfg, asked):
    """Handed on, a zero timeout came back "timed out after 0 seconds" from a command
    that never ran, where deepagents' own shell is refused and its tool says "Invalid
    parameter"."""
    runner = Timed()
    start(cfg, "s")
    shell = LocalSessions(runner=lambda _where: runner).open(cfg, "s").default

    with pytest.raises(ValueError, match="timeout must be positive"):
        shell.execute("true", timeout=asked)

    assert runner.handed == []


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


def test_kingfishers_own_shell_is_not_told_the_variable_twice(shelled):
    """Its wording already teaches the variable, and the sentence for a runner's shell
    would follow it -- the same rule twice, in two wordings, in every graph.
    """
    cfg, offered = shelled
    _run(cfg, *_consulting("checker"))

    assert _system().count(READ) == 1
    assert len(offered) == 3
    for tools in offered:
        assert _execute(tools).count(READ) == 1


@pytest.mark.parametrize("given", [True, False], ids=["session-root", "none"])
def test_a_miss_is_corrected_only_on_a_shell_exporting_the_session_root(shelled, given):
    """The correction was gated on `starts_in_session`, which a deployment's shell could
    claim without exporting the variable the correction names. Without `session_root=`,
    the control, the miss is left as the shell said it.
    """
    cfg, _ = shelled
    _run(
        cfg,
        _calls(("execute", {"command": "cat /inputs/cars.csv"}, "c1")),
        AIMessage(content="done"),
        backends=LocalSessions(
            runner=lambda _where: Plain(), session_root=_job if given else None
        ),
    )
    (answer,) = [m.text for m in Scripted.heard[-1] if isinstance(m, ToolMessage)]

    assert "No such file" in answer
    assert (hint("/inputs/cars.csv") in answer) is given


@dataclass
class Noting:
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
