from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from kingfisher import backend_at
from kingfisher.application.turn import ledger_delta, ledger_index
from kingfisher.core.access.capabilities import Capabilities
from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.agent.kinds.subagents import as_subagent
from kingfisher.core.agent.prompting import (
    kingfishers_shell,
    render_system_prompt,
    system_prompt,
)
from kingfisher.infrastructure.session_store import entry_id
from kingfisher.layout import INPUTS, OUTPUTS, SCRATCH, TOOL_RESULTS_ROUTE, Layout
from tests.conftest import FakeToolCallingModel, a_subagent


def test_base_prompt_names_no_dataset_and_no_domain():
    """A general agent's base prompt must read the same whatever the project is."""
    text = render_system_prompt()
    for leaked in ("sales", ".csv", "revenue", "region"):
        assert leaked not in text.lower(), f"domain-specific token {leaked!r} in base prompt"


def test_capability_sections_appear_only_when_enabled():
    """The agent must never be told about a capability that is not wired."""
    off = render_system_prompt()
    assert "/skills" not in off
    assert "/memory" not in off

    on = render_system_prompt(skills_enabled=True, memory_enabled=True)
    assert "/skills" in on
    assert "/memory" in on


def test_capability_flags_are_independent():
    skills_only = render_system_prompt(skills_enabled=True)
    assert "/skills" in skills_only
    assert "/memory" not in skills_only


def test_structural_contract_survives_every_combination():
    """The workspace layout is what every turn needs to know, whatever else is switched
    on.
    """
    for skills in (False, True):
        for memory in (False, True):
            text = render_system_prompt(skills_enabled=skills, memory_enabled=memory)
            assert "/inputs" in text
            assert "/outputs" in text


def test_the_prompt_s_path_table_spells_the_directories_the_session_has():
    """A renamed directory whose old name the prompt still teaches: every write the
    agent makes there is refused, or lands where nothing looks.

    The names come from `layout` rather than being written out here, since a literal
    copy would be renamed along with the prompt, or not at all, and pass either way.
    """
    text = render_system_prompt(shell=kingfishers_shell())

    for name in (INPUTS, OUTPUTS, SCRATCH):
        assert f"| `/{name}/<name>` | `{name}/<name>` |" in text, name


def test_a_renamed_layout_reaches_every_path_in_the_prompt():
    """The prompt is the one thing the model reads about where its files are. A renamed
    layout that does not reach it leaves the agent told to read and write the default
    names the backend no longer routes -- so every templated path must move together.
    """
    moved = Layout(
        inputs="in", outputs="out", memory="mem", scratchpad="scratch", tool_results="results"
    )
    text = render_system_prompt(
        skills_enabled=True, memory_enabled=True, layout=moved, shell=kingfishers_shell()
    )

    # The renamed leaves, spelled where the default names used to be.
    assert "| `/in/<name>` | `in/<name>` |" in text
    assert "| `/out/<name>` | `out/<name>` |" in text
    assert "| `/scratch/<name>` | `scratch/<name>` |" in text
    assert "/scratch/results/" in text
    # The fixed sub-leaves stay fixed, under the renamed scratchpad.
    assert "/scratch/large_tool_results/" in text
    assert "/scratch/conversation_history/" in text
    # The memory capability section names the renamed memory leaf.
    assert "/mem" in text

    # And none of the default spellings the rename replaced.
    for gone in ("/inputs", "/outputs", "/scratchpad", "/memory", "/tool_results"):
        assert gone not in text, gone


def test_the_prompt_names_the_folders_deepagents_writes_on_its_own(cfg, session_dir):
    """A prompt naming folders the backend no longer has deepagents write sends the
    agent's search somewhere empty, as deepagents' own `grep` description already does.

    The root is read off what `backend_at` builds rather than spelled here, so moving
    the setting or renaming the scratchpad fails this until the prompt follows.
    """
    from kingfisher.plugins.sessions.backend import backend_at

    root = backend_at(cfg, session_dir).artifacts_root.rstrip("/")
    text = render_system_prompt()

    for folder in ("large_tool_results", "conversation_history"):
        assert f"`{root}/{folder}/`" in text, folder


def test_the_prompt_names_a_recorded_result_as_the_ledger_does():
    """The folder, the id and the tag are each the recorder's or the delta's spelling;
    renamed on one side alone, the agent is told to look for something never written.

    Read off the code that writes each, since a literal here would be renamed along
    with the prompt, or not at all, and pass either way.
    """
    text = render_system_prompt()

    assert f"`{TOOL_RESULTS_ROUTE}`" in text
    assert f"`[{entry_id(42)}]`" in text
    for block in (ledger_delta(()), ledger_index(())):
        assert f"`{block.splitlines()[0]}`" in text


def test_the_prompt_lets_the_model_cite_a_result_and_does_not_tell_it_to():
    """Worded as an instruction, the model hangs an id on every claim whether or not the
    reader wants one; the owner asked for the sentence to give permission instead.
    """
    # As read, not as wrapped in the file.
    text = " ".join(render_system_prompt().split())

    assert "You can cite it by that id when you rely on that result." in text
    assert "Cite it" not in text


def test_nothing_the_agent_is_told_sends_it_to_a_run_directory():
    """The run directory went when a turn stopped being a place, and the prompt and the
    smoke task went on sending files there for days: a place the agent cannot find is
    one it guesses at, and the smoke's driver never read where it guessed.
    """
    from evals.task import SMOKE_TASK

    for text in (render_system_prompt(skills_enabled=True, memory_enabled=True), SMOKE_TASK):
        assert "run directory" not in text


def test_instructions_are_appended_to_the_harness_prompt(cfg):
    text = system_prompt(cfg, instructions="Prefer polars over pandas in this project.")
    assert "Prefer polars over pandas" in text
    # Appended, not replacing the structural contract.
    assert "/outputs" in text


def test_no_instructions_leave_the_base_prompt_untouched(cfg):
    assert system_prompt(cfg) == render_system_prompt()


def test_enabling_capabilities_changes_the_rendered_prompt(cfg):
    enabled = replace(cfg, skills_enabled=True, memory_enabled=True)
    assert system_prompt(enabled) != system_prompt(cfg)
    assert "/skills" in system_prompt(enabled)


def test_assembly_leaves_no_blank_line_runs_or_markers():
    """The marker is removed cleanly; the prefix ships on every step."""
    import re

    from kingfisher.core.agent.prompting import CAPABILITY_MARKER

    for skills in (False, True):
        for memory in (False, True):
            text = render_system_prompt(skills_enabled=skills, memory_enabled=memory)
            assert CAPABILITY_MARKER not in text
            assert "<!--" not in text
            assert not re.search(r"\n{3,}", text)


def test_shell_section_does_not_contradict_the_injected_host_mappings():
    """FilesystemMiddleware injects host path mappings whenever a CompositeBackend is
    used, and instructs the agent to substitute them in shell commands.
    """
    # Collapse wrapping: the prompt is hard-wrapped, so phrases span newlines.
    text = " ".join(render_system_prompt(shell=kingfishers_shell()).lower().split())
    assert "do not use host absolute paths" not in text
    # And it must not repeat the middleware's misleading implication that
    # unmapped mounts are unreachable from the shell.
    assert "nothing in the workspace is out of the shell's reach" in text


def test_prompt_warns_about_host_paths_in_file_tools():
    """virtual_mode makes `write_file('/tmp/x')` succeed by creating
    `<workspace>/tmp/x`.
    """
    text = " ".join(render_system_prompt().lower().split())
    assert "a host path is not a file-tool path" in text
    assert "recreated *inside* the workspace" in text


def test_the_system_prompt_demands_no_artifacts():
    """A general agent is sometimes just answering, and this prompt is the cached prefix
    for both kinds of turn -- the one place that cannot tell them apart.
    """
    for skills in (False, True):
        for memory in (False, True):
            text = render_system_prompt(skills_enabled=skills, memory_enabled=memory)
            assert "report.md" not in text
            assert "result.json" not in text

    assert "Answer the question. That is the deliverable" in render_system_prompt()


def test_the_shell_mapping_the_prompt_promises_is_the_one_the_backend_implements(
    cfg, session_dir
):
    """The prompt tells the agent a virtual path becomes a shell path by dropping the
    leading slash.
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
    """The skills section warns that `/skills` is the one path where dropping the slash
    silently reads the wrong directory.
    """
    from kingfisher.plugins.sessions.backend import backend_at

    backend = backend_at(cfg, session_dir)
    cwd = Path(backend.default.cwd).resolve()
    backend.write("/skills/demo/SKILL.md", "hello")

    assert not (cwd / "skills" / "demo").exists(), (
        "the catalogue now resolves by dropping the slash -- drop the warning"
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
    return as_subagent(spec, cfg, instructions=instructions)["system_prompt"]


def test_a_subagent_is_given_the_deployments_instructions(cfg):
    """A subagent does not get the harness prompt, but it does get the house rules."""
    prompt = _subagent_prompt(cfg, "Always cite the file.")

    assert prompt.index("Always cite the file.") < prompt.index("You review analyses")


def test_a_deployment_with_no_instructions_changes_nothing(cfg):
    """The definition's own text, byte for byte -- no separator, no blank tail."""
    assert _subagent_prompt(cfg) == "You review analyses for arithmetic errors."


def test_a_subagent_is_not_given_the_harnesss_own_prompt(cfg):
    """The part that must *not* be inherited."""
    prompt = _subagent_prompt(cfg, "Always cite the file.")

    assert len(prompt) < 200
    assert "/inputs" not in prompt


def test_both_levels_separate_the_addition_the_same_way(cfg):
    """One joining rule."""
    main = system_prompt(replace(cfg, skills_enabled=True), instructions="Always cite the file.")
    subagent = _subagent_prompt(cfg, "Always cite the file.")

    assert "\n\n---\n\nAlways cite the file." in main
    assert "Always cite the file.\n\n---\n\n" in subagent


def test_the_prompt_does_not_promise_other_sessions_are_out_of_reach():
    """It told the agent another session's files were not reachable from any path it
    could write. The macOS shell can read and change them, and so can any unfenced one.
    """
    for skills in (False, True):
        for memory in (False, True):
            text = render_system_prompt(skills_enabled=skills, memory_enabled=memory)
            assert "another session's files" not in text


def test_the_prompt_claims_about_inputs_only_what_holds_with_no_fence():
    """It said writes under the inputs folder are denied "at the tool level **and by
    the filesystem itself**". The second half is a fence, and `off` and `external` are
    supported settings: measured, with the sandbox off, `chmod u+w` and the same write
    replaced the caller's file and `rm -rf` took the folder, both exit 0.

    `core/agent/assembly.py` records that exact measurement in the present tense a few lines
    from the code that drops the bits -- so the prompt was contradicted by its own
    repository. This is the fifth sighting of that sentence: #743 corrected it in the
    permissions helper, a test docstring and the contract kit, and left it in the text
    the model reads every turn.

    The file-tool half holds on every run, and that is what the prompt says now. What
    the fence does is the operator's to know, and `doctor` is where they hear it.
    """
    text = render_system_prompt()

    assert "every file tool\n  refuses a write under it" in text
    assert "by the filesystem itself" not in text


@pytest.mark.parametrize("sandbox", ["auto", "off", "external"])
def test_the_half_the_prompt_still_claims_holds_with_no_fence_at_all(
    sandbox, cfg, session_dir
):
    """The other half of the rule above: the sentence was corrected rather than
    deleted, so what it now claims has to be driven -- and driven in the settings the
    old claim was false in, which is where a wording fix would otherwise just move the
    untruth.

    A real build, a real `write_file`, one confinement mode each. The file tools carry
    the deny rule whatever runs the shell, because it is a rule on the backend and not
    on the host.
    """
    made = replace(cfg, shell_sandbox=sandbox)
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
        made,
        backend=backend_at(made, session_dir),
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


def test_the_prompt_does_not_count_the_places_a_turn_may_write():
    """"One of two places, and never anywhere else" is false the moment memory is on:
    the same composed prompt then tells the agent to record things, and
    `write_file("/memory/AGENTS.md", ...)` succeeds and comes back as an artifact.

    An absolute claim in the base document that an optional section contradicts is the
    composition hazard #760 fixed for the shell's four claims. Named rather than
    counted now -- what is *not* the agent's to write does not change with a
    capability, and the sections that grant a place say so where they grant it.
    """
    for memory in (False, True):
        text = render_system_prompt(memory_enabled=memory)

        assert "never anywhere else" not in text
        assert "Nothing else in the session is yours to write" in text
        assert ("Record something when it would change" in text) is memory


# -- a deployment's own instructions ---------------------------------------------


def test_the_instructions_come_after_the_harness(cfg):
    """A deployment's standing instruction reaches the main agent, behind the harness
    prompt rather than in place of it.
    """
    with_it = system_prompt(cfg, instructions="House rule: cite every figure.")

    assert with_it.index("You are kingfisher") < with_it.index("House rule:")
    # The structural contract is still there -- the addition did not replace it.
    assert f"/{INPUTS}" in with_it


def test_no_instructions_change_nothing(cfg):
    """Empty is byte-for-byte the prompt without them."""
    assert system_prompt(cfg) == system_prompt(cfg, instructions="")


def test_the_instructions_lead_the_agents_own_line(cfg):
    """Order: harness, then the deployment's instructions, then the agent -- most
    general to most specific.
    """
    prompt = system_prompt(cfg, "Agent note.", instructions="House rule.")

    assert prompt.index("House rule.") < prompt.index("Agent note.")
