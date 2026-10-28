from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from kingfisher import Kingfisher, Request
from kingfisher.core.access.capabilities import Capabilities
from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.agent.kinds.subagents import as_subagent
from kingfisher.core.agent.middlewares.builtin_tools import MAPPINGS, TODOS
from kingfisher.core.agent.prompting import (
    memory_guidelines,
    render_system_prompt,
    session_section,
    skills_index,
    system_prompt,
)
from kingfisher.core.interfaces import Instructions
from kingfisher.core.layout import DEFAULT_LAYOUT, INPUTS, OUTPUTS, SCRATCH, Layout
from kingfisher.plugins import LocalSessions, ShellSettings, backend_at
from kingfisher.plugins.sessions.shell import kingfishers_shell
from tests.conftest import FakeToolCallingModel, a_subagent, an_agent, start, subagents_dir
from tests.unit.scripted import Scripted


def shipped(**described: Any) -> str:
    """The prompt under the shipped layout, which most rules here are about."""
    return render_system_prompt(layout=DEFAULT_LAYOUT, **described)


def test_base_prompt_names_no_dataset_and_no_domain():
    """A general agent's base prompt must read the same whatever the project is."""
    text = shipped()
    for leaked in ("sales", ".csv", "revenue", "region"):
        assert leaked not in text.lower(), f"domain-specific token {leaked!r} in base prompt"


def test_skills_and_memory_are_described_by_their_blocks_and_nowhere_else():
    """Described in the cached prompt as well, each was described twice where it was on,
    and to a request that had declined it or held no index, and so was handed no block."""
    text = shipped(shell=kingfishers_shell(fenced=True))

    assert "/skills" not in text
    assert "/memory" not in text
    assert "/memory" in memory_guidelines(None, DEFAULT_LAYOUT)
    shell = kingfishers_shell(fenced=True)
    assert "$KINGFISHER_SKILLS" in skills_index(DEFAULT_LAYOUT, shell, shelled=True)


def test_the_memory_block_says_memory_lasts_only_this_conversation():
    """`/memory` goes with its session, and a prompt promising "a future session" had the
    agent writing for a reader who never arrives. The block is the one place it is said
    now, to an agent that saves and to one that only reads."""
    for tools in (None, frozenset({"read_file"})):
        told = " ".join(memory_guidelines(tools, DEFAULT_LAYOUT).split())
        assert "kept for later turns of this conversation" in told
        assert "future" not in told.lower()


def test_structural_contract_survives_every_combination():
    """The workspace layout is what every turn needs to know, whatever else is switched
    on.
    """
    text = shipped()
    assert "/inputs" in text
    assert "/outputs" in text


def test_the_prompt_s_path_table_spells_the_directories_the_session_has():
    """A renamed directory whose old name the prompt still teaches: every write the
    agent makes there is refused, or lands where nothing looks.

    The names come from `layout` rather than being written out here, since a literal
    copy would be renamed along with the prompt, or not at all, and pass either way.
    """
    text = shipped(shell=kingfishers_shell(fenced=True))

    for name in (INPUTS, OUTPUTS, SCRATCH):
        assert f'| `/{name}/<name>` | `"$KINGFISHER_SESSION_ROOT/{name}/<name>"` |' in text, name


def test_the_shell_section_says_where_its_writes_go():
    """The shell starts at the session's top, where a fence refuses a write: measured,
    `echo x > out.csv` came back `Operation not permitted` and nothing else, and the
    section said only how to spell a path, not which folders would take one.

    Under a renamed layout, because the sentence has to name this deployment's folders:
    written out, it would send every agent to the default names the backend no longer
    routes. What the sentence claims is already held against the real fence, either way
    round -- `test_the_shell_cannot_save_at_the_top_of_a_session` refuses the top and
    `test_the_agent_can_still_write_everything_it_is_meant_to` keeps the two folders the
    agent's, both in `test_confinement.py`.
    """
    moved = Layout(
        inputs="in", outputs="out", memory="mem", scratchpad="scratch", tool_results="results"
    )
    text = render_system_prompt(layout=moved, shell=kingfishers_shell(fenced=True))
    joined = " ".join(text.split())

    assert "It writes in `/out`, for anything that should outlive the run" in joined
    assert "in `/scratch`, for anything throwaway" in joined
    assert "the folder the shell starts in, which is the session's top" in joined
    assert '`"$KINGFISHER_SESSION_ROOT/out/<name>"`' in joined


def test_a_renamed_layout_reaches_every_path_in_the_prompt():
    """The prompt is the one thing the model reads about where its files are. A renamed
    layout that does not reach it leaves the agent told to read and write the default
    names the backend no longer routes -- so every templated path must move together.
    """
    moved = Layout(
        inputs="in", outputs="out", memory="mem", scratchpad="scratch", tool_results="results"
    )
    text = render_system_prompt(layout=moved, shell=kingfishers_shell(fenced=True))
    remembered = memory_guidelines(None, moved)

    # The renamed leaves, spelled where the default names used to be.
    assert '| `/in/<name>` | `"$KINGFISHER_SESSION_ROOT/in/<name>"` |' in text
    assert '| `/out/<name>` | `"$KINGFISHER_SESSION_ROOT/out/<name>"` |' in text
    assert '| `/scratch/<name>` | `"$KINGFISHER_SESSION_ROOT/scratch/<name>"` |' in text
    assert "/scratch/results/" in text
    # The fixed sub-leaves stay fixed, under the renamed scratchpad.
    assert "/scratch/conversation_history/" in text
    # The memory block names the renamed memory leaf.
    assert "`/mem`" in remembered

    # And none of the default spellings the rename replaced.
    for gone in ("/inputs", "/outputs", "/scratchpad", "/memory", "/tool_results"):
        assert gone not in text, gone
        # Backticked, as the block writes a path: its own closing tag holds `/memory`.
        assert f"`{gone}" not in remembered, gone


def test_the_prompt_names_the_folder_deepagents_writes_on_its_own(cfg, session_dir):
    """A prompt naming a folder the backend no longer has deepagents write sends the
    agent's search somewhere empty, as deepagents' own `grep` description did.

    One folder, not two: its offloading is off, and what its summariser still writes to
    `large_tool_results` is read through the stub that names it. The root is read off what
    `backend_at` builds rather than spelled here, so moving the setting or renaming the
    scratchpad fails this until the prompt follows.
    """
    from kingfisher.plugins.sessions.backend import backend_at

    root = backend_at(cfg, session_dir).artifacts_root.rstrip("/")
    text = shipped()

    assert f"`{root}/conversation_history/`" in text
    assert "large_tool_results" not in text, "a folder read through its stubs is named"


@pytest.mark.parametrize("layout", [DEFAULT_LAYOUT, Layout(outputs="results")])
def test_a_requested_file_name_is_saved_in_the_outputs(layout):
    """Told to follow a request's file names exactly, with nothing in the prompt
    overriding them, a model asked for `/cleaned.csv` copied its files to the session's
    top through the shell (#1066), where every fence now refuses the write and nothing is
    handed back -- so the prompt says where a requested name goes, in this layout's name.
    """
    said = " ".join(render_system_prompt(layout=layout).split())

    assert f"asked for `/<name>`, save `/{layout.outputs}/<name>`" in said
    assert "nothing here overrides" not in said


def test_a_deployment_that_does_not_cite_is_not_told_it_may(cfg):
    """Read off the `Config` and not passed on, the switch would leave every deployment
    told it may cite; dropped with its paragraph, the id would go unexplained.
    """
    citing = " ".join(system_prompt(cfg).split())
    silent = " ".join(system_prompt(replace(cfg, cite_results=False)).split())

    sentence = "You can cite it by that id when you rely on that result."
    assert sentence in citing
    assert sentence not in silent
    assert silent == citing.replace(f" {sentence}", "")
    assert "is recorded under that id, which stays the same for the rest of the session." in silent


def test_nothing_the_agent_is_told_sends_it_to_a_run_directory():
    """The run directory went when a turn stopped being a place, and the prompt and the
    smoke task went on sending files there for days: a place the agent cannot find is
    one it guesses at, and the smoke's driver never read where it guessed.
    """
    from evals.task import SMOKE_TASK

    for text in (
        shipped(),
        skills_index(DEFAULT_LAYOUT, kingfishers_shell(fenced=True), shelled=True),
        memory_guidelines(None, DEFAULT_LAYOUT),
        SMOKE_TASK,
    ):
        assert "run directory" not in text


def test_no_instructions_leave_the_base_prompt_untouched(cfg):
    assert system_prompt(cfg) == shipped()


def test_switching_skills_and_memory_on_leaves_the_cached_prompt_alone(cfg):
    """Each is described in a block of its own, so the static prompt a deployment's every
    turn is compared against is one string whichever is on."""
    enabled = replace(cfg, skills_enabled=True, memory_enabled=True)

    assert system_prompt(enabled) == system_prompt(cfg)


def test_assembly_leaves_no_blank_line_runs_or_markers():
    """Every marker is removed cleanly; the prefix ships on every step."""
    import re

    text = shipped()
    assert "<!--" not in text
    assert not re.search(r"\n{3,}", text)


def test_shell_section_does_not_contradict_the_injected_host_mappings():
    """FilesystemMiddleware injects host path mappings whenever a CompositeBackend is
    used, and instructs the agent to substitute them in shell commands.
    """
    # Collapse wrapping: the prompt is hard-wrapped, so phrases span newlines.
    text = " ".join(shipped(shell=kingfishers_shell(fenced=True)).lower().split())
    assert "do not use host absolute paths" not in text
    # And it must not repeat the middleware's misleading implication that
    # unmapped mounts are unreachable from the shell.
    assert "nothing in the workspace is out of the shell's reach" in text


def test_prompt_warns_about_host_paths_in_file_tools():
    """virtual_mode makes `write_file('/tmp/x')` succeed by creating
    `<workspace>/tmp/x`, which the backend now refuses, and the prompt says so first.
    """
    text = " ".join(shipped().lower().split())
    assert "a host path is not a file-tool path" in text
    assert "is refused, and the error names the virtual path to use instead" in text


def test_the_system_prompt_demands_no_artifacts():
    """A general agent is sometimes just answering, and this prompt is the cached prefix
    for both kinds of turn -- the one place that cannot tell them apart. What a turn
    should answer with is the agent's own prompt's to say, not this one's.
    """
    text = shipped()
    assert "report.md" not in text
    assert "result.json" not in text
    assert "deliverable" not in text


def test_the_shell_mapping_the_prompt_promises_is_the_one_the_backend_implements(
    cfg, session_dir
):
    """The prompt tells the agent a virtual path is the same path under the session root,
    which the shell exports as `$KINGFISHER_SESSION_ROOT`.
    """
    from kingfisher.plugins.sessions.backend import backend_at

    backend = backend_at(cfg, session_dir)
    cwd = Path(backend.default.cwd).resolve()

    for virtual in ("/scratchpad/x.txt", "/outputs/x.txt", "/inputs/x.txt"):
        backend.write(virtual, "x")
        landed = (cwd / virtual.lstrip("/")).resolve()
        assert landed.is_file(), (
            f"the prompt promises {virtual} is {virtual.lstrip('/')} from the shell, "
            f"but nothing is at {landed}"
        )


def test_the_skills_exception_is_still_an_exception(cfg, session_dir):
    """The skills section warns that `/skills` is the one path not under
    `$KINGFISHER_SESSION_ROOT`.
    """
    from kingfisher.plugins.sessions.backend import backend_at

    backend = backend_at(cfg, session_dir)
    cwd = Path(backend.default.cwd).resolve()
    backend.write("/skills/demo/SKILL.md", "hello")

    assert not (cwd / "skills" / "demo").exists(), (
        "the catalogue is now under the session root -- drop the warning"
    )

    # The escape hatch is checked by running it through the agent's own shell,
    # since it depends on `shell_env` exporting the variable. It used to be
    # spelled `$HOME/skills`, which held only while HOME was the workspace; HOME
    # is now per session, and this test is what caught that.
    result = backend.execute('cat "$KINGFISHER_SKILLS/demo/SKILL.md"')
    assert result.exit_code == 0, (
        f"$KINGFISHER_SKILLS does not reach the catalogue: {result}"
    )
    assert "hello" in result.output


# -- what a subagent's prompt carries -------------------------------------
#
# A deployment's instructions once reached the main agent and nothing the main agent
# delegated to, so a house rule applied right up until the moment the work was handed
# on. Nothing stated that as a decision; the asymmetry was just there.


SUBAGENT = """name: reviewer
description: Checks an analysis.
system_prompt: |
  You review analyses for arithmetic errors.
"""


def _subagent_prompt(cfg, instructions: str = ""):
    spec = a_subagent(SUBAGENT, "reviewer.yaml")
    runs = FakeToolCallingModel(responses=[])
    return as_subagent(spec, cfg, runs=runs, instructions=instructions)["system_prompt"]


def test_a_subagent_is_given_the_deployments_instructions(cfg):
    """A house rule that reached the agent and not the work it handed on is one delegated
    work escapes."""
    prompt = _subagent_prompt(cfg, "Always cite the file.")

    assert prompt.index("You review analyses") < prompt.index("Always cite the file.")


def test_the_agent_and_its_subagents_are_told_the_same_folders(cfg):
    """Two copies of the section would drift, and a subagent would be told a folder the
    agent was not."""
    assert session_section(cfg.layout) in system_prompt(cfg)


def test_both_levels_separate_the_addition_the_same_way(cfg):
    """One joining rule, and one order: the instructions, then the harness."""
    main = system_prompt(replace(cfg, skills_enabled=True), instructions="Always cite the file.")
    subagent = _subagent_prompt(cfg, "Always cite the file.")

    assert "Always cite the file.\n\n---\n\n## The session" in main
    assert "Always cite the file.\n\n---\n\n## The session" in subagent


@pytest.mark.parametrize("sandbox", ["auto", "off", "external"])
def test_the_half_the_prompt_still_claims_holds_with_no_fence_at_all(
    sandbox, cfg, session_dir
):
    """The prompt's claim that every file tool refuses a write under the inputs folder,
    driven in the settings where its older claim, that the filesystem refused it too, was
    false -- which is where a wording fix would otherwise just move the untruth.

    A real build, a real `write_file`, one confinement mode each. The file tools carry
    the deny rule whatever runs the shell, because it is a rule on the backend and not
    on the host.
    """
    (session_dir / INPUTS / "given.csv").write_text("caller,data\n", encoding="utf-8")
    call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "write_file",
                "id": "1",
                "args": {"file_path": f"/{INPUTS}/given.csv", "content": "forged"},
            }
        ],
    )
    graph = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir, shell=ShellSettings(sandbox=sandbox)),
        capabilities=Capabilities(builtin_tools=("write_file",)),
        model=FakeToolCallingModel(responses=[call, AIMessage(content="done")]),
    ).graph

    answered = graph.invoke(
        {"messages": [("user", "go")]},
        config={"configurable": {"thread_id": f"t-{sandbox}"}, "recursion_limit": 8},
    )

    said = " ".join(
        str(m.content) for m in answered["messages"] if type(m).__name__ == "ToolMessage"
    )
    assert "denied" in said.lower() or "not allowed" in said.lower(), said
    assert (session_dir / INPUTS / "given.csv").read_text(encoding="utf-8") == "caller,data\n"


# -- a deployment's own instructions ---------------------------------------------


def test_the_instructions_come_before_the_harness(cfg):
    """A deployment's standing instruction reaches the main agent, ahead of the harness
    prompt rather than in place of it.
    """
    with_it = system_prompt(cfg, instructions="House rule: cite every figure.")

    assert with_it.index("House rule:") < with_it.index("## The session")
    # The structural contract is still there -- the addition did not replace it.
    assert f"/{INPUTS}" in with_it


def test_the_agents_own_line_leads_the_instructions(cfg):
    """Order: the agent, then the deployment's instructions, then the harness -- most
    specific to most general, so what the client wrote opens the prompt.
    """
    prompt = system_prompt(cfg, "Agent note.", instructions="House rule.")

    assert prompt.index("Agent note.") < prompt.index("House rule.")


# -- told only of the tools it holds --------------------------------------------

#: How each section about a built-in tool opens: kingfisher's two, deepagents' host paths
#: and langchain's checklist.
SECTIONS = ("## The file tools", "## The shell", MAPPINGS, TODOS)

READER = """name: reader
description: Reads.
builtin_tools: [read_file]
system_prompt: |
  You read.
"""


def _told(cfg, *script: AIMessage, **agent: str) -> list[str]:
    """The system message each model call of one turn was sent, in order: driven through
    a real turn, since two of the sections arrive only on the way to the model.
    """
    an_agent(cfg, **agent)
    Scripted.script.extend(script or (AIMessage("done"),))
    Kingfisher(cfg, sessions=LocalSessions()).run(
        Request("go", agent="only", session_id=start(cfg, "s"))
    )
    return [m.text for heard in Scripted.heard for m in heard if isinstance(m, SystemMessage)]


# Never the host paths: kingfisher's own shell section names the session root, which an
# agent told it is not handed the list as well.
@pytest.mark.parametrize(
    ("granted", "told"),
    [
        ({"builtin_tools": '["*"]'}, set(SECTIONS) - {MAPPINGS}),
        ({}, set()),
        ({"builtin_tools": "[]"}, set()),
        ({"builtin_tools": "[read_file]"}, {"## The file tools"}),
        ({"builtin_tools": "[execute]"}, {"## The shell"}),
        ({"builtin_tools": "[write_todos]"}, {TODOS}),
    ],
    ids=["every", "left out", "none", "read_file", "execute", "write_todos"],
)
def test_an_agent_is_told_only_of_the_built_in_tools_it_holds(scripted, granted, told):
    """An agent granted `builtin_tools: []` was still told of the file tools, the shell,
    its host paths and `write_todos`, and a model told of a tool calls it and is refused.
    """
    (system,) = _told(scripted, **granted)

    assert {section for section in SECTIONS if section in system} == told
    assert "You do the task." in system


@pytest.mark.parametrize(
    ("subagent", "agent"),
    [
        ("reader", {"subagents": "[reader]", "builtin_tools": '["*"]'}),
        ("general-purpose", {"builtin_tools": "[read_file, task]"}),
    ],
)
def test_a_subagent_is_not_told_of_a_shell_it_does_not_hold(scripted, subagent, agent):
    """deepagents hands a subagent its host paths ahead of the allowlist that takes
    `execute` away, so one granted `read_file` alone was told how to reach its files
    from a shell it could not call.
    """
    subagents_dir(scripted).mkdir(parents=True, exist_ok=True)
    (subagents_dir(scripted) / "reader.yaml").write_text(READER, encoding="utf-8")
    asks = AIMessage(
        content="",
        tool_calls=[
            {"name": "task", "args": {"description": "read", "subagent_type": subagent}, "id": "t"}
        ],
    )

    systems = _told(scripted, asks, AIMessage("read"), AIMessage("done"), **agent)

    assert len(systems) == 3
    assert "## What your tools returned" not in systems[1]
    assert MAPPINGS not in systems[1]


class _HouseRules(Instructions):
    """A deployment's instructions, handed in as `Kingfisher(instructions=)` takes them."""

    def text(self, cfg) -> str:
        return "House rule: never delete anything."


@pytest.mark.parametrize(
    ("subagent", "agent"),
    [
        ("reader", {"subagents": "[reader]", "builtin_tools": '["*"]'}),
        ("general-purpose", {"builtin_tools": "[read_file, task]"}),
    ],
)
def test_every_subagent_is_given_the_deployments_instructions(scripted, subagent, agent):
    """`general-purpose` was handed deepagents' prompt bare, so a house rule held for every
    subagent but the one the agent delegates to when it names none.
    """
    subagents_dir(scripted).mkdir(parents=True, exist_ok=True)
    (subagents_dir(scripted) / "reader.yaml").write_text(READER, encoding="utf-8")
    an_agent(scripted, **agent)
    asks = AIMessage(
        content="",
        tool_calls=[
            {"name": "task", "args": {"description": "read", "subagent_type": subagent}, "id": "t"}
        ],
    )
    Scripted.script.extend([asks, AIMessage("read"), AIMessage("done")])
    Kingfisher(scripted, sessions=LocalSessions(), instructions=_HouseRules()).run(
        Request("go", agent="only", session_id=start(scripted, "s"))
    )
    systems = [m.text for heard in Scripted.heard for m in heard if isinstance(m, SystemMessage)]

    assert len(systems) == 3
    assert "House rule: never delete anything." in systems[1]


#: A subagent that consults `reader` itself, so a helper, built through its parent's own
#: `SubAgentMiddleware`, is driven as well as the two the agent's build makes.
CONSULTING = """name: consulting
description: Asks.
builtin_tools: [read_file, task]
subagents: [reader]
system_prompt: |
  You ask.
"""


def _asking(description: str, subagent: str, at: str) -> AIMessage:
    task = {"description": description, "subagent_type": subagent}
    return AIMessage(content="", tool_calls=[{"name": "task", "args": task, "id": at}])


@pytest.mark.parametrize(
    ("asked", "agent", "script"),
    [
        (
            "read",
            {"subagents": "[reader]", "builtin_tools": '["*"]'},
            (_asking("read", "reader", "t"), AIMessage("read"), AIMessage("done")),
        ),
        (
            "read",
            {"builtin_tools": "[read_file, task]"},
            (_asking("read", "general-purpose", "t"), AIMessage("read"), AIMessage("done")),
        ),
        (
            "read deeper",
            {"subagents": "[consulting, reader]", "builtin_tools": '["*"]'},
            (
                _asking("ask", "consulting", "t"),
                _asking("read deeper", "reader", "u"),
                AIMessage("read"),
                AIMessage("asked"),
                AIMessage("done"),
            ),
        ),
    ],
    ids=["declared", "general-purpose", "helper"],
)
def test_every_subagent_is_told_the_sessions_folders_in_its_layouts_names(
    scripted, asked, agent, script
):
    """Left to guess, three subagents in four saved their deliverables to an `/outputs`
    the renamed layout did not have, and the turn reported none of them."""
    renamed = replace(scripted, layout=Layout(outputs="results"))
    subagents_dir(renamed).mkdir(parents=True, exist_ok=True)
    (subagents_dir(renamed) / "reader.yaml").write_text(READER, encoding="utf-8")
    (subagents_dir(renamed) / "consulting.yaml").write_text(CONSULTING, encoding="utf-8")

    systems = _told(renamed, *script, **agent)

    (system,) = [
        system
        for system, heard in zip(systems, Scripted.heard, strict=True)
        if any(isinstance(m, HumanMessage) and m.text == asked for m in heard)
    ]
    assert session_section(renamed.layout) in system
    assert "`/results`" in system
    assert "/outputs" not in system


# -- told to read back and save only with the tools it holds ------------------------


@pytest.mark.parametrize(
    ("granted", "saver", "reads"),
    [
        ("[read_file, write_file, edit_file]", "edit_file", True),
        ("[write_file]", "write_file", False),
        ("[read_file, ls, glob, grep, execute]", None, True),
        ("[]", None, False),
    ],
    ids=["edit_file", "write_file", "as operator", "none"],
)
def test_memory_is_saved_only_with_a_tool_the_agent_holds(scripted, granted, saver, reads):
    """With memory on, an agent holding no tool that writes, as the shipped `operator`
    holds none, was told to record with `edit_file` and spent a call on each refusal."""
    (system,) = _told(replace(scripted, memory_enabled=True), builtin_tools=granted)

    assert "<agent_memory>" in system, "no memory was wired, so the rest is vacuous"
    for tool in ("edit_file", "write_file"):
        assert (f"Save to it with `{tool}`" in system) is (tool == saver), tool
    assert ("never save credentials" in system) is (saver is not None)
    assert ("can be read here but not added to" in system) is (saver is None)
    assert ("evidence from `read_file`" in system) is reads


@pytest.mark.parametrize(
    ("subagent", "agent"),
    [
        ("reader", {"subagents": "[reader]", "builtin_tools": '["*"]'}),
        ("general-purpose", {"builtin_tools": "[read_file, task]"}),
    ],
)
def test_a_subagent_is_handed_no_memory_to_be_told_how_to_save(scripted, subagent, agent):
    """A subagent handed the memory file would hear deepagents' own guidelines, naming
    `edit_file` whatever it holds, since only the agent's follow its grant."""
    subagents_dir(scripted).mkdir(parents=True, exist_ok=True)
    (subagents_dir(scripted) / "reader.yaml").write_text(READER, encoding="utf-8")
    asks = AIMessage(
        content="",
        tool_calls=[
            {"name": "task", "args": {"description": "read", "subagent_type": subagent}, "id": "t"}
        ],
    )
    memory_on = replace(scripted, memory_enabled=True)

    systems = _told(memory_on, asks, AIMessage("read"), AIMessage("done"), **agent)

    assert "<agent_memory>" in systems[0], "the control: the agent was handed no memory"
    assert "<agent_memory>" not in systems[1]


def test_the_built_in_names_are_deepagents_and_langchains_own():
    """The file tools were listed three times and held to nothing: one deepagents added
    would be recorded, never cleared to a handle, and go unnamed in the prompt."""
    from deepagents.middleware.filesystem import FilesystemMiddleware
    from langchain.agents.middleware import TodoListMiddleware

    from kingfisher.core.agent.prompting import EXECUTE, FILE_TOOLS, WRITE_TODOS

    assert {tool.name for tool in FilesystemMiddleware().tools} == {*FILE_TOOLS, EXECUTE}
    assert [tool.name for tool in TodoListMiddleware().tools] == [WRITE_TODOS]
