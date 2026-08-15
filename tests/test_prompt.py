from __future__ import annotations

from dataclasses import replace

from kingfisher.adapters.agent import USER_PROMPT_FILE, render_system_prompt, system_prompt


def test_base_prompt_names_no_dataset_and_no_domain():
    """A general agent's base prompt must read the same whatever the project is.

    The smoke fixture's filename leaked in here once; this is the guard.
    """
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
    """The workspace layout is what every turn needs to know, whatever else is
    switched on. It is the one thing that genuinely belongs in every prompt."""
    for skills in (False, True):
        for memory in (False, True):
            text = render_system_prompt(skills_enabled=skills, memory_enabled=memory)
            assert "/data" in text
            assert "/derived" in text
            assert "/reports" in text


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

    from kingfisher.adapters.agent import CAPABILITY_MARKER

    for skills in (False, True):
        for memory in (False, True):
            text = render_system_prompt(skills_enabled=skills, memory_enabled=memory)
            assert CAPABILITY_MARKER not in text
            assert "<!--" not in text
            assert not re.search(r"\n{3,}", text)


def test_shell_section_does_not_contradict_the_injected_host_mappings():
    """FilesystemMiddleware injects host path mappings whenever a CompositeBackend
    is used, and instructs the agent to substitute them in shell commands. A
    blanket ban on absolute paths here would contradict that, and the agent
    would reasonably follow the more specific instruction.
    """
    # Collapse wrapping: the prompt is hard-wrapped, so phrases span newlines.
    text = " ".join(render_system_prompt().lower().split())
    assert "do not use host absolute paths" not in text
    # And it must not repeat the middleware's misleading implication that
    # unmapped mounts are unreachable from the shell.
    assert "nothing in the workspace is out of the shell's reach" in text


def test_prompt_warns_about_host_paths_in_file_tools():
    """virtual_mode makes `write_file('/tmp/x')` succeed by creating
    `<workspace>/tmp/x`. Observed in a real run: a subagent recreated an entire
    host path inside the workspace and believed it had written to /tmp.
    """
    text = " ".join(render_system_prompt().lower().split())
    assert "a host path is not a file-tool path" in text
    assert "recreated *inside* the workspace" in text


def test_the_system_prompt_demands_no_artifacts():
    """A general agent is sometimes just answering, and this prompt is the
    cached prefix for both kinds of turn -- the one place that cannot tell them
    apart. Asking for files belongs on the request, not here.

    Leaving it here produced both failure modes in one afternoon: a greeting
    that deliberated over files nobody wanted, and, once softened to a
    suggestion, a real analysis that recorded nothing.
    """
    for skills in (False, True):
        for memory in (False, True):
            text = render_system_prompt(skills_enabled=skills, memory_enabled=memory)
            assert "report.md" not in text
            assert "result.json" not in text

    assert "Answer the question. That is the deliverable" in render_system_prompt()
