"""What a failed call tells the model about a path.

Driven through a graph kingfisher builds and the backend a turn gets, rather than against
the middleware alone: the spellings these rules are about are the composite's and an
`OSError`'s, and a stand-in would answer with whatever this file wrote into it.
"""

from __future__ import annotations

import asyncio
import stat
from typing import Any

import pytest
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, ToolMessage

from kingfisher import ToolContext
from kingfisher.core.access.capabilities import Capabilities
from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.agent.permitted_backend import PermittedBackend
from kingfisher.core.agent.session_paths import as_asked
from kingfisher.core.agent.tool_guards import guarded_tools
from kingfisher.core.definitions.tools.catalogue import LocalToolRepository
from kingfisher.core.layout import DEFAULT_LAYOUT
from kingfisher.plugins import backend_at
from tests.conftest import FakeToolCallingModel, paths_in, tools_dir

#: Every builtin these rules call, granted together so one agent can be driven once.
TOOLS = ("read_file", "write_file", "delete", "ls", "execute")


@pytest.fixture(params=["sync", "async"])
def way(request):
    """Both halves of `wrap_tool_call`: an async turn goes through the other one, and a
    suite asking only the sync half cannot see a fix landing on one of them."""
    return request.param


def _answers(cfg, session_dir, way: str, *calls: tuple[str, dict[str, Any]]) -> dict[str, str]:
    """Each call's answer by `<n>:<tool>`, from one turn of a real graph."""
    model = FakeToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": name, "args": args, "id": f"{n}:{name}"}
                    for n, (name, args) in enumerate(calls)
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    graph = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=model,
        capabilities=Capabilities(builtin_tools=TOOLS),
    ).graph
    sent = {"messages": [{"role": "user", "content": "go"}]}
    config: Any = {"recursion_limit": 8}
    out = (
        graph.invoke(sent, config=config)
        if way == "sync"
        else asyncio.run(graph.ainvoke(sent, config=config))
    )
    return {
        str(one.tool_call_id): str(one.content)
        for one in out["messages"]
        if isinstance(one, ToolMessage)
    }


@pytest.mark.parametrize(
    "asked",
    [
        "/inputs/nope.txt",
        "/memory/nope.md",
        "/skills/nope/SKILL.md",
        "/scratchpad/tool_results/nope.txt",
        # The control: a path under no route, which was right all along.
        "/scratchpad/nope.txt",
    ],
)
def test_a_miss_names_the_path_that_was_asked_for(cfg, session_dir, way, asked):
    """The composite strips a route before calling the backend holding it, and that
    backend reports its own root -- so `/inputs/nope.txt` came back as
    `File '/nope.txt' not found`, naming a path in a different place, which a model is
    left to retry or to write. Every route, because the one that matters most is
    `/inputs`, where a caller's files are and where a miss is ordinary.
    """
    said = _answers(cfg, session_dir, way, ("read_file", {"file_path": asked}))

    assert f"'{asked}'" in said["0:read_file"], said["0:read_file"]


def test_a_listing_that_misses_names_the_path_that_was_asked_for(cfg, session_dir, way):
    """`ls` says it in its own words, so the rule cannot be a rewrite of one message."""
    said = _answers(cfg, session_dir, way, ("ls", {"path": "/inputs/nowhere"}))

    assert "'/inputs/nowhere'" in said["0:ls"], said["0:ls"]


def test_a_failure_does_not_hand_over_the_host_path(cfg, session_dir, way):
    """An `OSError` carries the path it failed on, which is the host's: a refused write
    answered with `/private/.../sessions/<id>/scratchpad/...`, the spelling the file
    tools exist not to have and `reject_host_path` refuses when a model writes one.
    """
    shut = session_dir / "scratchpad" / "shut"
    shut.mkdir(parents=True, exist_ok=True)
    shut.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        said = _answers(
            cfg,
            session_dir,
            way,
            ("write_file", {"file_path": "/scratchpad/shut/x.txt", "content": "hi"}),
        )
    finally:
        shut.chmod(stat.S_IRWXU)

    answer = said["0:write_file"]
    assert str(session_dir) not in answer, answer
    # Not merely stripped: the path it failed on is still named, as the agent wrote it.
    assert answer.count("'/scratchpad/shut/x.txt'") == 2, answer


def test_a_file_whose_contents_name_a_path_comes_back_whole(cfg, session_dir, way):
    """The reason only a failure is rewritten: a file read whole is content, and a file
    saying `/notes.txt` is not a tool talking about a path.
    """
    (session_dir / "inputs").mkdir(exist_ok=True)
    (session_dir / "inputs" / "notes.txt").write_text(
        "see '/notes.txt' for the rest\n", encoding="utf-8"
    )

    said = _answers(cfg, session_dir, way, ("read_file", {"file_path": "/inputs/notes.txt"}))

    assert "'/notes.txt'" in said["0:read_file"], said["0:read_file"]


def test_the_shell_keeps_its_own_spelling(cfg, session_dir, way):
    """The shell's paths are the host's by design, so a command's own spelling is the one
    that would work again, and the model is handed what the shell said.

    `/bin/cat`, not this interpreter: the venv sits inside `$HOME` on a macOS runner,
    which the profile denies, so a command naming `sys.executable` never ran there and
    the rule passed on a message about permissions. A program both fences allow keeps the
    case the same on every machine.
    """
    said = _answers(
        cfg, session_dir, way, ("execute", {"command": "/bin/cat /scratchpad/missing.py"})
    )

    assert "/scratchpad/missing.py" in said["0:execute"], said["0:execute"]
    # Unquoted, as the shell wrote it: nothing here has turned it into a tool's spelling.
    assert "'/scratchpad/missing.py'" not in said["0:execute"], said["0:execute"]


def test_a_shell_message_is_what_the_rewrite_would_spoil():
    """Why the shell is kept out, pinned where no fence can refuse it: the string a shell
    call names is a command rather than a path, so a quoted path in its output is a tail
    of the whole command line. Applied to one, the rewrite puts the command where the
    path belongs -- which is what `status` keeps from happening, since a command that
    fails is a tool call that succeeded.
    """
    command = "/usr/bin/python3 /scratchpad/missing.py"
    said = "python3: can't open file '/scratchpad/missing.py': [Errno 2]"

    spoiled = as_asked(said, {"command": command})

    assert f"'{command}'" in spoiled
    assert "'/scratchpad/missing.py'" not in spoiled


# -- a write a deny rule refused -----------------------------------------------


@pytest.mark.parametrize("scope", DEFAULT_LAYOUT.denied_scopes())
def test_a_refused_write_says_where_a_write_may_go(cfg, session_dir, way, scope):
    """deepagents answers a deny rule with the code and the path and nothing else, while
    kingfisher's own refusal of a path *outside* the session names what to write instead
    -- so the ordinary case, a path inside the session that is kept, was the one taught
    nothing, and a model refused there spends the next call finding out where to go.

    Every scope the layout denies, read off `denied_scopes` rather than listed here: one
    sentence covers them all, and a scope the layout gains arrives with a case instead of
    passing uncovered. `blobs` is a folder nothing makes, and the rule refuses a write
    there all the same.
    """
    asked = scope.replace("**", "new.txt")

    said = _answers(cfg, session_dir, way, ("write_file", {"file_path": asked, "content": "x"}))

    answer = said["0:write_file"]
    assert f"on {asked}" in answer, answer
    assert "that path is not yours to write" in answer, answer
    assert "`/outputs/<name>`" in answer, answer
    assert "`/scratchpad/<name>`" in answer, answer


def test_a_refused_delete_says_it_too(cfg, session_dir, way):
    """`delete` is refused in its own words -- alone of the four it names the rule that
    refused it -- so the sentence cannot be a rewrite of one tool's message.
    """
    (session_dir / "inputs").mkdir(exist_ok=True)
    (session_dir / "inputs" / "given.txt").write_text("data", encoding="utf-8")

    said = _answers(cfg, session_dir, way, ("delete", {"file_path": "/inputs/given.txt"}))

    answer = said["0:delete"]
    assert "matches deny rule(s)" in answer, answer
    assert "that path is not yours to write" in answer, answer
    assert (session_dir / "inputs" / "given.txt").is_file(), "the file went anyway"


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        # A write that lands. Told where to write after writing there, a model reads the
        # sentence as a correction and moves the file.
        ("write_file", {"file_path": "/outputs/made.txt", "content": "x"}),
        # A read a rule refused, not a write: `/.harness` is denied both ways, and the two
        # folders a write may land in are no answer to a read.
        ("read_file", {"file_path": "/.harness/agent.yaml"}),
        # A failure that is no refusal: a path that is missing is not a path that is kept,
        # and the two want different next calls.
        ("read_file", {"file_path": "/inputs/nope.txt"}),
    ],
)
def test_nothing_but_a_refused_write_is_told_where_to_write(cfg, session_dir, way, tool, args):
    """The controls, one per message the rule must leave alone: a rule that appended the
    sentence to every error would pass the ones above.
    """
    said = _answers(cfg, session_dir, way, (tool, args))

    answer = said[f"0:{tool}"]
    assert "not yours to write" not in answer, answer


#: A tool that opens what it is given with Python's own `open`, which is handed the host
#: path by design and so fails naming one.
OPENS = '''from langchain.tools import ToolRuntime

from kingfisher import ToolContext


def opener(path: str, runtime: ToolRuntime[ToolContext]) -> str:
    """Open `path` and return it. Use never; this is a fixture."""
    with open(path) as handle:  # noqa: PTH123
        return handle.read()


TOOLS = [opener]
'''


def test_an_offered_tools_failure_names_the_path_the_model_wrote(cfg, session_dir, way):
    """`translated` makes a tool's `path` real on purpose, so the tool's own `OSError`
    carries the host spelling -- the one case where the leak is the common one rather
    than an edge, since every offered tool that opens a file does this.
    """
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "opener.py").write_text(OPENS, encoding="utf-8")
    model = FakeToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "opener", "args": {"path": "/inputs/missing.csv"}, "id": "c1"}
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    graph = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=model,
        capabilities=Capabilities(tools=("opener",)),
    ).graph
    sent = {"messages": [{"role": "user", "content": "go"}]}
    config: Any = {"recursion_limit": 8}
    out = (
        graph.invoke(sent, config=config)
        if way == "sync"
        else asyncio.run(graph.ainvoke(sent, config=config))
    )

    (answer,) = [one for one in out["messages"] if isinstance(one, ToolMessage)]
    assert str(session_dir) not in str(answer.content), answer.content
    assert "'/inputs/missing.csv'" in str(answer.content), answer.content


def test_a_carried_guard_says_it_the_same_way(cfg, session_dir):
    """`GuardedTool` is what answers where no middleware reaches -- a compiled subagent's
    tools and a call from `eval`'s code -- and the two must answer alike, which is what
    its own docstring claims and nothing held.
    """
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "opener.py").write_text(OPENS, encoding="utf-8")
    loaded = {one.name: one.tool for one in LocalToolRepository(tools_dir(cfg)).found}
    (wrapped,) = guarded_tools([loaded["opener"]], paths_in(cfg, session_dir))
    graph = create_agent(
        FakeToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "opener", "args": {"path": "/inputs/missing.csv"}, "id": "c1"}
                    ],
                ),
                AIMessage(content="done"),
            ]
        ),
        tools=[wrapped],
        context_schema=ToolContext,
    )

    out = graph.invoke(
        {"messages": [{"role": "user", "content": "go"}]},
        context=ToolContext(backend=PermittedBackend(backend_at(cfg, session_dir), [], cfg.layout)),
    )

    (answer,) = [one for one in out["messages"] if isinstance(one, ToolMessage)]
    assert str(session_dir) not in str(answer.content), answer.content
    assert "'/inputs/missing.csv'" in str(answer.content), answer.content


def test_a_path_the_call_never_named_is_left_alone():
    """The rule rewrites a path the call is a tail of, not every path in the message: a
    failure naming something else is reporting something else.
    """
    text = "Error: File '/elsewhere/other.txt' not found"

    assert as_asked(text, {"file_path": "/inputs/nope.txt"}) == text
