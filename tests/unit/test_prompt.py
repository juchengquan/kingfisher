from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from kingfisher.application.turn import ledger_delta, ledger_index
from kingfisher.infrastructure.harness.kinds.subagents import as_subagent
from kingfisher.infrastructure.prompting import (
    USER_PROMPT_FILE,
    render_system_prompt,
    system_prompt,
)
from kingfisher.infrastructure.session_store import entry_id
from kingfisher.layout import INPUTS, OUTPUTS, SCRATCH, TOOL_RESULTS_ROUTE, Layout
from tests.conftest import a_subagent


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
    text = render_system_prompt()

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
    text = render_system_prompt(skills_enabled=True, memory_enabled=True, layout=moved)

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
    from kingfisher.infrastructure.harness.backend import backend_at

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


def test_user_prompt_is_appended_when_the_workspace_has_one(cfg):
    (cfg.workspace / USER_PROMPT_FILE).write_text(
        "Prefer polars over pandas in this project.\n", encoding="utf-8"
    )
    text = system_prompt(cfg)
    assert "Prefer polars over pandas" in text
    # Appended, not replacing the structural contract.
    assert "/outputs" in text


def test_no_user_prompt_leaves_the_base_prompt_untouched(cfg):
    assert system_prompt(cfg) == render_system_prompt()


def test_enabling_capabilities_changes_the_rendered_prompt(cfg):
    enabled = replace(cfg, skills_enabled=True, memory_enabled=True)
    assert system_prompt(enabled) != system_prompt(cfg)
    assert "/skills" in system_prompt(enabled)


def test_assembly_leaves_no_blank_line_runs_or_markers():
    """The marker is removed cleanly; the prefix ships on every step."""
    import re

    from kingfisher.infrastructure.prompting import CAPABILITY_MARKER

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
    text = " ".join(render_system_prompt().lower().split())
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
    from kingfisher.infrastructure.harness.backend import backend_at

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
    from kingfisher.infrastructure.harness.backend import backend_at

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
# `PROMPT.md` is where a workspace writes what it wants said about its own
# work. It reached the main agent and nothing the main agent delegated to, so a
# house rule applied right up until the moment the work was handed on. Nothing
# stated that as a decision; the asymmetry was just there.


SUBAGENT = """name: reviewer
description: Checks an analysis.
system_prompt: |
  You review analyses for arithmetic errors.
"""


def _subagent_prompt(cfg):
    spec = a_subagent(SUBAGENT, "reviewer.yaml")
    return as_subagent(spec, cfg)["system_prompt"]


def test_a_subagent_is_given_the_workspaces_own_instructions(cfg):
    (cfg.workspace / USER_PROMPT_FILE).write_text("Always cite the file.", encoding="utf-8")

    assert "Always cite the file." in _subagent_prompt(cfg)


def test_a_workspace_that_wrote_none_changes_nothing(cfg):
    """The definition's own text, byte for byte -- no separator, no blank tail."""
    assert _subagent_prompt(cfg) == "You review analyses for arithmetic errors."


def test_a_subagent_is_not_given_the_harnesss_own_prompt(cfg):
    """The part that must *not* be inherited."""
    (cfg.workspace / USER_PROMPT_FILE).write_text("Always cite the file.", encoding="utf-8")
    prompt = _subagent_prompt(cfg)

    assert len(prompt) < 200
    assert "/inputs" not in prompt


def test_both_levels_separate_the_addition_the_same_way(cfg):
    """One joining rule."""
    (cfg.workspace / USER_PROMPT_FILE).write_text("Always cite the file.", encoding="utf-8")

    main = system_prompt(replace(cfg, skills_enabled=True))
    subagent = _subagent_prompt(cfg)

    assert "\n\n---\n\nAlways cite the file." in main
    assert "\n\n---\n\nAlways cite the file." in subagent


def test_the_prompt_does_not_promise_other_sessions_are_out_of_reach():
    """It told the agent another session's files were not reachable from any path it
    could write. The macOS shell can read and change them, and so can any unfenced one.
    """
    for skills in (False, True):
        for memory in (False, True):
            text = render_system_prompt(skills_enabled=skills, memory_enabled=memory)
            assert "another session's files" not in text


# -- a deployment's own house-rules prompt -----------------------------------


def test_the_deployment_prompt_is_appended_after_the_harness(cfg):
    """A library integrator's standing instruction reaches the main agent, behind the
    harness prompt rather than in place of it.
    """
    with_it = system_prompt(replace(cfg, deployment_prompt="House rule: cite every figure."))

    assert "House rule: cite every figure." in with_it
    assert with_it.index("You are kingfisher") < with_it.index("House rule:")
    # The structural contract is still there -- the addition did not replace it.
    assert f"/{INPUTS}" in with_it


def test_the_deployment_prompt_reaches_a_subagent_too(cfg):
    """A subagent does not get the harness prompt, but it does get the house rules."""
    prompt = _subagent_prompt(replace(cfg, deployment_prompt="House rule: cite every figure."))

    assert "House rule: cite every figure." in prompt
    assert "You are kingfisher" not in prompt, "still not the harness prompt"


def test_no_deployment_prompt_changes_nothing(cfg):
    """The default is empty, and an empty one is byte-for-byte the prompt without it."""
    assert system_prompt(cfg) == system_prompt(replace(cfg, deployment_prompt=""))
    assert _subagent_prompt(cfg) == "You review analyses for arithmetic errors."


def test_the_deployment_prompt_leads_the_workspace_and_agent_additions(cfg):
    """Order: harness, then the deployment's house rules, then the workspace, then the
    agent -- most general to most specific.
    """
    (cfg.workspace / USER_PROMPT_FILE).write_text("Workspace note.", encoding="utf-8")
    prompt = system_prompt(replace(cfg, deployment_prompt="House rule."), "Agent note.")

    assert prompt.index("House rule.") < prompt.index("Workspace note.")
    assert prompt.index("Workspace note.") < prompt.index("Agent note.")
