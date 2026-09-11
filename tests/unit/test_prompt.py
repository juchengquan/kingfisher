from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from kingfisher.infrastructure.harness.subagents import as_subagent
from kingfisher.infrastructure.prompting import (
    USER_PROMPT_FILE,
    render_system_prompt,
    system_prompt,
)
from kingfisher.kinds.subagents import reading


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
            assert "/data" in text
            assert "/derived" in text


def test_user_prompt_is_appended_when_the_workspace_has_one(cfg):
    (cfg.workspace / USER_PROMPT_FILE).write_text(
        "Prefer polars over pandas in this project.\n", encoding="utf-8"
    )
    text = system_prompt(cfg)
    assert "Prefer polars over pandas" in text
    # Appended, not replacing the structural contract.
    assert "/derived" in text


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
    from kingfisher.infrastructure.harness.backend import build_backend

    backend = build_backend(cfg, session_dir)
    cwd = Path(backend.default.cwd).resolve()

    for virtual in ("/runs/t001/input/x.txt", "/derived/x.txt", "/data/x.txt"):
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
    from kingfisher.infrastructure.harness.backend import build_backend

    backend = build_backend(cfg, session_dir)
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


# -- what a delegate's prompt carries -------------------------------------
#
# `PROMPT.md` is where a workspace writes what it wants said about its own
# work. It reached the main agent and nothing the main agent delegated to, so a
# house rule applied right up until the moment the work was handed on. Nothing
# stated that as a decision; the asymmetry was just there.


DELEGATE = """name: reviewer
description: Checks an analysis.
system_prompt: |
  You review analyses for arithmetic errors.
"""


def _delegate(cfg):
    spec = reading.read(DELEGATE, Path("reviewer.yaml"))
    return as_subagent(spec, cfg)["system_prompt"]


def test_a_delegate_is_given_the_workspaces_own_instructions(cfg):
    (cfg.workspace / USER_PROMPT_FILE).write_text("Always cite the file.", encoding="utf-8")

    assert "Always cite the file." in _delegate(cfg)


def test_a_workspace_that_wrote_none_changes_nothing(cfg):
    """The definition's own text, byte for byte -- no separator, no blank tail."""
    assert _delegate(cfg) == "You review analyses for arithmetic errors."


def test_a_delegate_is_not_given_the_harnesss_own_prompt(cfg):
    """The part that must *not* be inherited."""
    (cfg.workspace / USER_PROMPT_FILE).write_text("Always cite the file.", encoding="utf-8")
    prompt = _delegate(cfg)

    assert len(prompt) < 200
    assert "/data" not in prompt


def test_both_levels_separate_the_addition_the_same_way(cfg):
    """One joining rule."""
    (cfg.workspace / USER_PROMPT_FILE).write_text("Always cite the file.", encoding="utf-8")

    main = system_prompt(replace(cfg, skills_enabled=True))
    delegate = _delegate(cfg)

    assert "\n\n---\n\nAlways cite the file." in main
    assert "\n\n---\n\nAlways cite the file." in delegate
