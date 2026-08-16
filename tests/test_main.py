"""The driver's rendering, which had no tests at all.

Nothing imported main.py, so the one file whose entire job is what the user
sees was the one file nobody checked. Every way this fails is silent and
textual -- a line jammed onto the end of a sentence, an unbounded argument, a
tool result leaking through as prose -- which is exactly what "run it and
look" misses, because it only shows on the input you did not happen to try.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

import main
from kingfisher.domain.capabilities import CapabilityError
from kingfisher.domain.result import RunEvent, RunResult
from kingfisher.infrastructure import agent as main_agent_module


def _render(events: list[RunEvent]) -> tuple[str, RunResult | None]:
    out = io.StringIO()
    result = main.render(iter(events), out)
    return out.getvalue(), result


def _a_result() -> RunResult:
    return RunResult(
        session_id="s",
        turn_id="t001",
        answer="42",
        run_dir=Path("/tmp/run"),
        log_path=Path("/tmp/log"),
    )


def test_structural_events_render_one_per_line():
    text, _ = _render(
        [
            RunEvent(kind="run_start", text="/runs/s/t001"),
            RunEvent(kind="model_call", tools=("execute",), args=({"command": "ls"},)),
        ]
    )

    assert text.splitlines() == [
        "[start] /runs/s/t001",
        "[model] → execute(command=ls)  (in=0 cached=0)",
    ]


def test_the_finished_event_is_returned_not_printed():
    """It carries the result; it has nothing to say that was not already said."""
    expected = _a_result()
    text, result = _render([RunEvent(kind="finished", text="42", result=expected)])

    assert result is expected
    assert text == ""


def test_prose_and_tagged_lines_do_not_jam_together():
    """Without the owed newline: "…the file.[model] → write_file(…)"."""
    text, _ = _render(
        [
            RunEvent(kind="token", text="I'll write the file."),
            RunEvent(kind="model_call", tools=("write_file",), args=({"file_path": "/data/x"},)),
        ]
    )

    assert "I'll write the file.\n[model]" in text


def test_consecutive_tokens_are_not_broken_apart():
    """One newline per run of prose, at the end -- never between fragments."""
    text, _ = _render(
        [RunEvent(kind="token", text="7 to"), RunEvent(kind="token", text=" seven.txt")]
    )

    assert text == "7 to seven.txt\n"


def test_prose_is_closed_off_when_the_run_ends_on_it():
    """The common case: the last thing a run does is finish its answer."""
    text, result = _render(
        [RunEvent(kind="token", text="done"), RunEvent(kind="finished", result=_a_result())]
    )

    assert text == "done\n"
    assert result is not None


def test_the_models_own_formatting_survives():
    """Tokens carry markdown mid-stream; `_line` would flatten it."""
    text, _ = _render([RunEvent(kind="token", text="1. first\n\n2. second")])

    assert text == "1. first\n\n2. second\n"


def test_no_result_when_the_stream_never_finishes():
    """A stream cut short must not look like a successful run."""
    text, result = _render([RunEvent(kind="run_start", text="/runs/s/t001")])

    assert result is None
    assert "[start]" in text


def test_the_inventory_lists_without_a_session(cfg, capsys):
    """`--list` describes the workspace, and a workspace has no session.

    Rooting each session at its own directory made `session_dir` mandatory for
    `build_agent`, and this caller had none to give -- so `--list` raised
    instead of listing.
    """
    assert main.show_inventory(cfg, cfg.workspace) == 0

    printed = capsys.readouterr().out
    assert "read_file" in printed
    assert "could not introspect" not in printed


def test_listing_the_inventory_leaves_no_session_behind(cfg):
    """Introspection is a question, not a turn. It must not litter."""
    main.show_inventory(cfg, cfg.workspace)

    sessions = cfg.workspace / "sessions"
    assert list(sessions.iterdir()) == []


def test_the_smoke_seeds_its_dataset_where_the_agent_looks(cfg):
    """`/data` is a *session's* directory, not the workspace's.

    Seeding at workspace level wrote the fixture where no route reaches: the
    smoke printed "seeded sample dataset into /data" and the agent found /data
    empty, so every check failed for a reason nothing reported.
    """
    seeded = main.prepare_smoke(cfg, cfg.workspace, "smoke1")

    assert (cfg.workspace / "sessions" / "smoke1" / "data" / "orders.csv").is_file()
    assert any("dataset" in line for line in seeded)


def test_the_smoke_never_creates_a_workspace_level_data_directory(cfg):
    """The stray directory is the tell: nothing reads it, so nothing may make it."""
    main.prepare_smoke(cfg, cfg.workspace, "smoke2")

    assert not (cfg.workspace / "data").exists()


def test_skills_stay_shared_across_sessions(cfg):
    """Only `data` moved under the session. Skills are workspace-level, which
    is where the backend's `/skills` route still points."""
    from dataclasses import replace

    main.prepare_smoke(replace(cfg, skills_enabled=True), cfg.workspace, "smoke3")

    assert (cfg.workspace / "skills" / "tabular-qa" / "SKILL.md").is_file()
    assert not (cfg.workspace / "sessions" / "smoke3" / "skills").exists()


def test_seeding_lands_in_the_catalogue_not_the_workspace(cfg, tmp_path, capsys, monkeypatch):
    """They are the same directory until a deployment moves them, and this
    wrote to the workspace unconditionally. With a relocated catalogue it
    seeded four skills where nothing reads, and the `--list` on the next line
    reported `(none)` -- the third time a path has gone stale this way, after
    `writable_data` and `promote_report`.
    """
    from dataclasses import replace

    import main as driver
    from kingfisher.infrastructure import skill_store

    catalogue = tmp_path / "catalogue"
    relocated = replace(
        cfg, skills_root=catalogue / "skills", subagents_root=catalogue / "subagents"
    )
    monkeypatch.setattr(driver, "from_env", lambda: relocated)

    assert driver.main(["main.py", "--seed-presets", "--list"]) == 0

    assert skill_store.names(relocated.skills_dir)  # the catalogue was filled
    assert not skill_store.names(relocated.workspace / "skills")  # and not the workspace

    # And the listing that follows reflects it, which is what went wrong before.
    listed = capsys.readouterr().out
    for name in skill_store.names(relocated.skills_dir):
        assert name in listed


def test_seeding_still_works_when_the_catalogue_is_the_workspace(cfg, capsys, monkeypatch):
    """The default, and the case the old code got right -- worth keeping, or
    the fix above could quietly break the ordinary setup."""
    import main as driver
    from kingfisher.infrastructure import skill_store

    monkeypatch.setattr(driver, "from_env", lambda: cfg)

    # `--list` so it returns after seeding; without it the driver falls
    # through to running the task, which wants a model.
    assert driver.main(["main.py", "--seed-presets", "--list"]) == 0
    assert skill_store.names(cfg.skills_dir)


def test_seeding_puts_tools_in_the_tool_catalogue(cfg, tmp_path, monkeypatch):
    """The third catalogue, and the third chance to seed where nothing reads.

    `KINGFISHER_TOOLS_DIR` relocates it the way the other two relocate, so a
    preset tool written to `workspace/tools` would be invisible to the agent
    for exactly the reason #40 fixed for skills.
    """
    from dataclasses import replace

    import main as driver
    from kingfisher.infrastructure.tool_store import names

    catalogue = tmp_path / "catalogue"
    relocated = replace(cfg, tools_root=catalogue / "tools")
    monkeypatch.setattr(driver, "from_env", lambda: relocated)

    assert driver.main(["main.py", "--seed-presets", "--list"]) == 0

    assert "http_fetch" in names(relocated.tools_dir)
    # `ensure_layout` still makes the workspace directory, so the place to put
    # one is obvious. What must not happen is a preset landing in it.
    assert names(relocated.workspace / "tools") == ()


# -- --without-tools and friends ------------------------------------------


def _args(**kwargs):
    import argparse

    base = dict.fromkeys(
        ("tools", "skills", "subagents", "without_tools", "without_skills", "without_subagents")
    )
    return argparse.Namespace(**{**base, **kwargs})


def test_no_flags_leaves_every_kind_unrestricted(cfg):
    """`None` per kind, not an enumerated list of everything: an enumeration
    would go stale the moment the workspace gained a tool."""
    assert main._grants(cfg, _args()) == {"tools": None, "skills": None, "subagents": None}


def test_a_subtraction_becomes_the_enumerated_rest(cfg):
    from kingfisher.infrastructure import presets

    presets.seed(cfg)

    tools = main._grants(cfg, _args(without_tools="execute,delete"))["tools"]

    assert tools is not None
    assert "execute" not in tools
    assert "delete" not in tools
    assert "http_fetch" in tools  # a workspace tool, so it had to be built to be seen
    assert main._grants(cfg, _args(without_tools="execute"))["skills"] is None


def test_subtracting_skills_and_subagents_too(cfg):
    """The rest is asked of the catalogue, not named here.

    Naming it made this a test about how many presets ship: it asserted
    `("reviewer",)` and went red on main when a third subagent preset was
    added, for a reason having nothing to do with subtraction. What is being
    tested is that the named one is gone and the others are enumerated, which
    is true at any catalogue size.
    """
    from kingfisher.infrastructure import presets, skill_store, subagent_store

    presets.seed(cfg)
    seeded_skills = set(skill_store.names(cfg.skills_dir))
    seeded_subagents = set(subagent_store.load_all(cfg.subagents_dir))
    # Not vacuous: subtracting a name the catalogue does not offer would leave
    # "the rest" equal to the whole of it, and this would still pass.
    assert {"tabular-qa"} < seeded_skills
    assert {"extractor"} < seeded_subagents

    grants = main._grants(cfg, _args(without_skills="tabular-qa", without_subagents="extractor"))

    assert grants["skills"] == tuple(sorted(seeded_skills - {"tabular-qa"}))
    assert grants["subagents"] == tuple(sorted(seeded_subagents - {"extractor"}))
    assert grants["tools"] is None


def test_naming_both_forms_of_one_kind_is_refused(cfg):
    """Two ways to say the same thing; whichever precedence we picked, the
    other reading is the one somebody meant."""
    with pytest.raises(ValueError, match="not both: --tools and --without-tools"):
        main._grants(cfg, _args(tools="ls", without_tools="execute"))


def test_a_typo_in_a_subtraction_is_refused(cfg):
    with pytest.raises(CapabilityError, match="cannot exclude unknown name"):
        main._grants(cfg, _args(without_tools="exec"))


def test_the_agent_is_only_built_when_a_subtraction_asks(cfg, monkeypatch):
    """Resolving needs to know what is offered, which needs an assembled agent.
    A run that does not subtract should not pay for one."""
    builds = []
    real = main_agent_module.build_agent

    def counted(*args, **kwargs):
        builds.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(main_agent_module, "build_agent", counted)

    main._grants(cfg, _args(tools="ls"))
    assert builds == []

    main._grants(cfg, _args(without_tools="execute"))
    assert len(builds) == 1
