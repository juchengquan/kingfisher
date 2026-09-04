"""The driver's rendering, which had no tests at all.

Nothing imported tests/integration/driver.py, so the one file whose entire job is what the user
sees was the one file nobody checked. Every way this fails is silent and
textual -- a line jammed onto the end of a sentence, an unbounded argument, a
tool result leaking through as prose -- which is exactly what "run it and
look" misses, because it only shows on the input you did not happen to try.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from kingfisher.config import ConfigError
from kingfisher.domain.capabilities import CapabilityError
from kingfisher.domain.result import RunEvent, RunResult
from kingfisher.infrastructure.catalogue.subagents import LocalSubagentRepository
from kingfisher.infrastructure.harness import agent as main_agent_module
from kingfisher.infrastructure.workspace import fs as workspace_fs
from kingfisher.presentation.cli.progress import show
from kingfisher.skills import spec as skill
from kingfisher.skills.catalogue import LocalSkillRepository
from tests.conftest import subagents_dir, tools_dir
from tests.integration import driver as main


def _render(events: list[RunEvent]) -> tuple[str, RunResult | None]:
    """One stream, which is what the driver passes and what these tests are about.

    The renderer moved into the wheel when `kingfisher run` needed one --
    shipping a second copy is how the two would have come to disagree about a
    new event kind. These stay here because they are the driver's rendering
    contract; the two-stream split `run` uses is tested beside the verb.
    """
    out = io.StringIO()
    result = show(iter(events), out)
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


def test_a_delegates_prose_is_marked_where_it_starts_and_ends():
    """Two answers on one stream, with nothing between them, read as one.

    The marker cannot go on the fragment: chunks split mid-word, so there is no
    line to tag. It goes at the seam, which is the only place a boundary
    actually exists.
    """
    text, _ = _render(
        [
            RunEvent(kind="token", text="Checking that. "),
            RunEvent(kind="token", text="I recomputed it.", agent="reviewer"),
            RunEvent(kind="token", text="Agreed."),
        ]
    )

    assert text == "Checking that. \n[reviewer]\nI recomputed it.\n[main]\nAgreed.\n"


def test_a_run_with_no_delegates_is_rendered_exactly_as_before():
    """The regression that would be easy to miss: every run gaining a `[main]`
    line it never had. The marker is printed on a *change* of speaker, and a
    run with one speaker never changes."""
    text, _ = _render(
        [RunEvent(kind="token", text="7 to"), RunEvent(kind="token", text=" seven.txt")]
    )

    assert text == "7 to seven.txt\n"


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


def test_a_broken_tool_is_reported_rather_than_raised(cfg, capsys):
    """`--list` is where someone goes *because* something is wrong.

    A malformed subagent has always been caught and printed; a tool that would
    not load went out as a traceback over the rest of the inventory. Folders
    make that more likely rather than less, so the two loaders report the same
    way.

    The example used to be two folders each defining a `find_company`, which is
    no longer broken -- see the test below. One file exporting a name twice
    still is, because there is no second file to tell those apart.
    """
    directory = tools_dir(cfg) / "research"
    directory.mkdir(parents=True)
    (directory / "t.py").write_text(
        "from langchain_core.tools import tool\n"
        "@tool\ndef find_company(x: str) -> str:\n"
        '    """Look up."""\n'
        "    return x\n"
        "TOOLS = [find_company, find_company]\n",
        encoding="utf-8",
    )

    assert main.show_inventory(cfg, cfg.workspace) == 1

    printed = capsys.readouterr().out
    assert "cannot load" in printed
    # The path a reader can open -- `t.py` alone names nothing.
    assert "research/t.py" in printed


def test_two_folders_may_each_define_one_subagent_name(cfg, capsys):
    """The subagent half, and it failed harder than the tool one did: a
    duplicate name took the whole inventory down rather than one section.

    Listed under the reference a grant would write, with no trailing `(file)` --
    the reference already says where it lives, and printing it twice in a
    listing whose job is to be scannable is noise.
    """
    spec = (
        "name: surveyor\ndescription: Surveys, the {who} way.\n"
        "system_prompt: |\n  Do the thing.\n"
    )
    for who in ("vendor", "team"):
        directory = subagents_dir(cfg) / who
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "surveyor.yaml").write_text(spec.format(who=who), encoding="utf-8")

    assert main.show_inventory(cfg, cfg.workspace) == 0

    printed = capsys.readouterr().out
    assert "vendor/surveyor.yaml::surveyor — Surveys, the vendor way." in printed
    assert "team/surveyor.yaml::surveyor — Surveys, the team way." in printed


def test_two_folders_may_each_define_one_name(cfg, capsys):
    """The case that used to stop `--list` dead, and stop a deployment with it.

    Vendors do not coordinate names. Both are listed, under the reference a
    grant would write, because a bare `find_company` no longer says which.
    """
    for folder in ("research", "sales"):
        directory = tools_dir(cfg) / folder
        directory.mkdir(parents=True)
        (directory / "t.py").write_text(
            "from langchain_core.tools import tool\n"
            "@tool\ndef find_company(x: str) -> str:\n"
            '    """Look up."""\n'
            "    return x\n"
            "TOOLS = [find_company]\n",
            encoding="utf-8",
        )

    assert main.show_inventory(cfg, cfg.workspace) == 0

    printed = capsys.readouterr().out
    assert "research/t.py::find_company" in printed
    assert "sales/t.py::find_company" in printed


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
    is where the backend's `/skills` route still points.

    A session does hold `skills/uploaded`, and always did -- it used to appear
    on the first backend build and now arrives with the rest of the layout. So
    the claim is that the *catalogue* is not copied in, not that the directory
    is absent: an empty slot for a caller's own skills is not a second copy of
    the shared ones.
    """
    from dataclasses import replace

    main.prepare_smoke(replace(cfg, skills_enabled=True), cfg.workspace, "smoke3")
    session = cfg.workspace / "sessions" / "smoke3"

    assert (cfg.workspace / "skills" / "tabular-qa" / "SKILL.md").is_file()
    assert not (session / "skills" / "tabular-qa").exists()
    assert list((session / "skills" / "uploaded").iterdir()) == []


def _unused(cfg, tmp_path):
    """A `Config` whose workspace does not exist yet.

    The `cfg` fixture runs `ensure_layout`, which writes the marker -- so its
    workspace is not new and nothing below would fire. What A7 is about is the
    directory that has never been used.
    """
    from dataclasses import replace

    return replace(cfg, workspace=tmp_path / "brand-new")


def _driver_on(monkeypatch, target, source=None):
    """Point the driver at one record for all three seams.

    `config_from_env` serves the run, `paths_from_env` decides where seeding goes, and
    the same record now says where seeding copies *from*. A `Config` satisfies
    `Destination` and `Source` by shape, so one record answers all three --
    which is the point of the protocols and not a shortcut for the test.

    `assets` is filled in because it has to be: nothing ships definitions, so a
    record carrying `None` makes the driver refuse rather than seed. Set here
    rather than in each caller so that a test about seeding is about seeding.
    """
    from dataclasses import replace

    from tests.conftest import repository_root
    from tests.integration import driver

    configured = replace(target, assets=source or repository_root() / "assets_examples")
    monkeypatch.setattr(driver, "config_from_env", lambda: configured)
    monkeypatch.setattr(driver, "paths_from_env", lambda: configured)
    return driver


def test_a_new_workspace_seeds_itself(cfg, tmp_path, monkeypatch):
    """A7, and the reason the flag is not the only way in. Nothing is copied
    unless a pack was installed, which is somebody's explicit choice; a new
    workspace is empty by definition, so nothing can be lost."""
    fresh = _unused(cfg, tmp_path)
    driver = _driver_on(monkeypatch, fresh)

    assert driver.main(["driver.py", "--list"]) == 0

    assert LocalSkillRepository(fresh.skills_dir).names


def test_a_new_workspace_says_what_it_wrote(cfg, tmp_path, capsys, monkeypatch):
    """`is_new_workspace` also fires on a *misconfigured* workspace -- an
    unstable `~`, a changed variable -- and a wrong path holding ten files
    reads more like success than an empty one does. The list is how someone
    notices they seeded somewhere they did not mean."""
    fresh = _unused(cfg, tmp_path)
    driver = _driver_on(monkeypatch, fresh)

    driver.main(["driver.py", "--list"])

    printed = capsys.readouterr().out
    assert "created a new workspace" in printed

    # The half this test is named for -- what it *wrote* -- is a loop, and an
    # empty one passes. Seeding writing no skills at all is exactly the failure
    # this would otherwise report as success.
    seeded = LocalSkillRepository(fresh.skills_dir).names
    assert seeded, "seeding wrote no skills, so the loop below checks nothing"

    for name in seeded:
        assert f"seeded skills/{name}" in printed


def test_a_workspace_that_already_exists_does_not_reseed(cfg, tmp_path, capsys, monkeypatch):
    """The half that makes the other half safe.

    Seeding overwrites by design -- that is what makes re-seeding after an
    upgrade possible -- so firing it on every run would quietly replace the
    edits the whole arrangement exists to invite. `--seed-assets` stays the way
    to ask for that, and asking is the point.
    """
    fresh = _unused(cfg, tmp_path)
    driver = _driver_on(monkeypatch, fresh)
    driver.main(["driver.py", "--list"])  # the first run, which seeds
    edited = fresh.skills_dir / "code-review" / skill.FILENAME
    edited.write_text("---\nname: code-review\ndescription: mine\n---\nmine\n", encoding="utf-8")
    capsys.readouterr()

    driver.main(["driver.py", "--list"])

    assert "seeded" not in capsys.readouterr().out
    assert edited.read_text(encoding="utf-8").endswith("mine\n")


def test_a_new_workspace_seeds_before_the_catalogue_is_read(tmp_path, capsys, monkeypatch):
    """The ordering, which is the whole reason any of this can work.

    `models.yaml` lives *inside* the workspace, so a first run cannot load one:
    `config_from_env` used to raise before the directory it needed had been created,
    and the error it printed said to run `--seed-assets`, which failed the same
    way. A first run could not reach seeding at all -- precisely the run seeding
    is for. Measured on a workspace with no catalogue: it still seeds, and still
    exits 2 for the missing file.

    Through the environment rather than the `_driver_on` seam, because the
    ordering under test is what `paths_from_env` makes possible -- patching it
    away would leave nothing to assert.
    """
    from tests.conftest import repository_root
    from tests.integration import driver

    workspace = tmp_path / "brand-new"
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(workspace))
    monkeypatch.setenv("KINGFISHER_ASSETS", str(repository_root() / "assets_examples"))
    monkeypatch.delenv("KINGFISHER_MODELS_FILE", raising=False)

    assert driver.main(["driver.py", "--list"]) == 2  # no catalogue, as expected

    assert LocalSkillRepository(workspace / "skills").names
    assert (workspace / workspace_fs.EXAMPLE).is_file()


def test_the_catalogue_error_stops_naming_a_command_that_already_ran(tmp_path):
    """The dead end, closed.

    The message said "`--seed-assets` writes an annotated models.yaml.example
    next to it" whether or not one was there -- and running `--seed-assets` hit
    this same error. Now a first run seeds first, so the file is usually already
    beside the reader, and being told to run a command that has just run is how
    a message stops being read.
    """
    from kingfisher.infrastructure import model_catalogue

    absent = tmp_path / "models.yaml"

    with pytest.raises(ConfigError) as without:
        model_catalogue.load(absent, {})
    (tmp_path / workspace_fs.EXAMPLE).write_text("# annotated\n", encoding="utf-8")
    with pytest.raises(ConfigError) as with_example:
        model_catalogue.load(absent, {})

    assert "`kingfisher seed` writes" in str(without.value)
    assert "is next to it" in str(with_example.value)
    assert "`kingfisher seed` writes" not in str(with_example.value)
    # The command, not the flag. `--seed-assets` is on `the driver`, which is not
    # in the wheel, so it names nothing a pip-installed reader has.
    assert "--seed" not in str(without.value)


def test_a_first_run_with_nothing_to_seed_is_quiet(cfg, tmp_path, capsys, monkeypatch):
    """A damaged install, where the shipped definitions are missing. Not an
    error: the catalogue example is the one thing a first run always needs, and
    it arrives with the layout rather than with the copy.

    That is the change this asserts. The example used to be reported as seeded,
    so a run with no definitions still printed one line and looked productive.
    It is placed by `ensure_layout` now, before seeding is reached at all --
    which is what lets seeding refuse without taking the example with it.

    Reached by pointing the seeder at an empty directory, which is the only way
    to produce this state now that the definitions ride inside the wheel."""
    fresh = _unused(cfg, tmp_path)
    driver = _driver_on(monkeypatch, fresh)
    empty = tmp_path / "no-definitions"
    empty.mkdir()
    real_seed = driver.seeding.seed
    # `**kwargs` rather than the one keyword, so this stub does not have to be
    # edited again the next time `seed` grows an argument it is not about.
    monkeypatch.setattr(
        driver.seeding,
        "seed",
        lambda cfg, source=None, **kwargs: real_seed(cfg, empty, **kwargs),
    )

    driver.main(["driver.py", "--list"])

    printed = capsys.readouterr().out
    assert f"seeded {workspace_fs.EXAMPLE}" not in printed
    assert "seeded skills/" not in printed
    assert (fresh.workspace / workspace_fs.EXAMPLE).is_file()


# -- --without-tools and friends ------------------------------------------


def _args(**kwargs):
    import argparse

    base = dict.fromkeys(
        (*main.GRANTS, *(f"without_{kind}" for kind in main.GRANTS))
    )
    return argparse.Namespace(**{**base, **kwargs})


def test_no_flags_leaves_every_kind_unrestricted(cfg):
    """Absent, not enumerated and not `None`.

    An enumeration would go stale the moment the workspace gained a tool. But
    `None` is worse than stale: `Capabilities` starts `builtin_tools`, `tools`
    and `skills` at `ALL` and reads `None` on those fields as *none*, so
    handing it a `None` per kind is a request for an agent with no tools and
    no skills at all.

    Which is what `tests/integration/driver.py "task"` was quietly sending. Measured against a real
    run: every workspace tool and every skill came back as withheld on a
    command line carrying no capability flags, while this file's own docstring
    promised that omitting a flag means everything the workspace offers.
    """
    # Empty, so `Capabilities(**grants)` is `Capabilities()` and every field
    # keeps its own default. That is the whole mechanism: this function says
    # what was *asked for*, and says nothing about what was not.
    assert main._grants(cfg, _args()) == {}


def test_a_subtraction_becomes_the_enumerated_rest(cfg, shipped):
    """And each subtraction is taken from its *own* axis.

    `execute` and `delete` are built-ins, so they are subtracted from the
    built-in set. Taken from the union -- which is what `_offered` used to
    return -- the rest included every built-in and was then assigned to the
    workspace grant, so `--without-tools execute,delete`, the example this
    driver's own docstring gives, came back as "those are builtin tools".
    """
    from kingfisher.infrastructure.workspace import seeding

    seeding.seed(cfg, shipped)

    granted = main._grants(cfg, _args(without_builtin_tools="execute,delete"))

    builtin = granted["builtin_tools"]
    assert builtin is not None
    assert "execute" not in builtin
    assert "delete" not in builtin
    assert "read_file" in builtin
    # The other axis is untouched: subtracting a built-in says nothing about
    # what the workspace defines, so it is absent rather than `None`.
    assert "tools" not in granted
    assert "http_fetch" not in builtin, "a workspace tool leaked onto the builtin axis"


def test_the_two_tool_axes_subtract_independently(cfg, shipped):
    """A workspace tool is subtracted from the workspace set, and only that."""
    from kingfisher.infrastructure.workspace import seeding

    seeding.seed(cfg, shipped)

    tools = main._grants(cfg, _args(without_tools="http_fetch"))["tools"]

    assert tools is not None
    assert "http_fetch" not in tools
    assert "csv_profile" in tools  # a workspace tool, so it had to be built to be seen
    assert "read_file" not in tools, "a builtin leaked onto the workspace axis"


def test_subtracting_skills_and_subagents_too(cfg, shipped):
    """The rest is asked of the catalogue, not named here.

    Naming it made this a test about how many presets ship: it asserted
    `("reviewer",)` and went red on main when a third subagent preset was
    added, for a reason having nothing to do with subtraction. What is being
    tested is that the named one is gone and the others are enumerated, which
    is true at any catalogue size.
    """
    from kingfisher.infrastructure.workspace import seeding

    seeding.seed(cfg, shipped)
    # `_offered`, not `LocalSkillRepository.names`, and the difference is the
    # point: the repository lists the directories directly under `skills/`,
    # while a run is offered whatever the *registry* resolves -- which includes
    # a skill in a source folder, `incident::postmortem`. Asking the wrong one
    # made this a test that passed until somebody shipped a sourced skill.
    seeded_skills = set(main._offered(cfg)["skills"])
    seeded_subagents = set(LocalSubagentRepository(subagents_dir(cfg)).specs)
    # Not vacuous: subtracting a name the catalogue does not offer would leave
    # "the rest" equal to the whole of it, and this would still pass.
    assert {"tabular-qa"} < seeded_skills
    assert {"extractor"} < seeded_subagents

    grants = main._grants(cfg, _args(without_skills="tabular-qa", without_subagents="extractor"))

    assert grants["skills"] == tuple(sorted(seeded_skills - {"tabular-qa"}))
    assert grants["subagents"] == tuple(sorted(seeded_subagents - {"extractor"}))
    assert "tools" not in grants
    assert "builtin_tools" not in grants


def test_subtracting_on_the_wrong_tool_axis_names_the_right_flag(cfg, shipped):
    """The mistake someone arrives with, because it used to be the advice.

    `--without-tools execute` is what this driver's docstring advertised before
    the axes were split. Left to `all_but` it comes back as "cannot exclude
    unknown name(s): execute" beside a list not containing it -- true, and no
    help at all if you do not know a second flag exists.
    """
    from kingfisher.infrastructure.workspace import seeding

    seeding.seed(cfg, shipped)

    with pytest.raises(CapabilityError, match=r"subtract it with --without-builtin-tools"):
        main._grants(cfg, _args(without_tools="execute"))

    with pytest.raises(CapabilityError, match=r"subtract it with --without-tools"):
        main._grants(cfg, _args(without_builtin_tools="http_fetch"))


def test_naming_both_forms_of_one_kind_is_refused(cfg):
    """Two ways to say the same thing; whichever precedence we picked, the
    other reading is the one somebody meant."""
    with pytest.raises(ValueError, match="not both: --tools and --without-tools"):
        main._grants(cfg, _args(tools="ls", without_tools="execute"))


def test_a_typo_in_a_subtraction_is_refused(cfg):
    with pytest.raises(CapabilityError, match="cannot exclude unknown name"):
        main._grants(cfg, _args(without_builtin_tools="exec"))


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

    main._grants(cfg, _args(without_builtin_tools="execute"))
    assert len(builds) == 1


# -- --agent, which every run that reaches a model needs -------------------


def _intercepted(monkeypatch) -> list:
    """Catch the `Request` the driver builds, without running a turn.

    Patched on `kingfisher` itself, not on `kingfisher.application.run`. The
    lazy export table caches: `__getattr__` resolves a name once and writes it
    into the package globals, so patching the defining module works for the
    first test in a session and silently hands every later one whatever the
    first cached -- which here meant a second test appending to the first
    test's list and finding its own empty.
    """
    from tests.integration import driver

    seen: list = []

    def _stream(request, **kwargs):
        seen.append(request)
        return iter(())

    monkeypatch.setattr("kingfisher.stream", _stream, raising=False)
    # The driver imports `show` by name, so the patch goes on the driver's own
    # binding -- patching the module it came from would leave this one bound.
    monkeypatch.setattr(driver, "show", lambda events, out: None)
    return seen


def test_the_agent_named_on_the_command_line_reaches_the_request(cfg, monkeypatch):
    """The whole flag. `Request.agent` is refused downstream when it is absent,
    and the driver had no way to supply it -- so every task exited 2, including
    the smoke."""
    driver = _driver_on(monkeypatch, cfg)
    seen = _intercepted(monkeypatch)

    driver.main(["driver.py", "say ok", "--agent", "assistant", "--no-checks"])

    assert seen and seen[0].agent == "assistant"


def test_a_run_without_an_agent_is_refused_rather_than_defaulted(cfg, monkeypatch, capsys):
    """No default, deliberately: an agent decides which endpoint a session's
    prompts reach and whose credentials pay, so a driver picking one would put
    that choice somewhere the command line never mentions.

    The refusal comes from the service and names what the workspace offers; the
    driver's job is only to let a caller answer it.
    """
    driver = _driver_on(monkeypatch, cfg)
    seen = _intercepted(monkeypatch)

    driver.main(["driver.py", "say ok", "--no-checks"])

    assert seen and seen[0].agent is None  # carried as absent, refused downstream


def test_the_flag_is_offered_in_help(capsys):
    """It is the one argument a task cannot omit, so it has to be findable
    without reading the source."""
    from tests.integration import driver

    parser = driver.build_parser()
    flags = {action.dest for action in parser._actions}

    assert "agent" in flags
