"""`kingfisher doctor`: everything between an install and a run."""

from __future__ import annotations

import json
import platform
import re
from dataclasses import replace
from pathlib import Path

import pytest

from kingfisher import LocalSessions, seed
from kingfisher.application.inventory import inventory
from kingfisher.application.service import Kingfisher
from kingfisher.plugins.sessions.sandbox.confinement import LINUX_FENCES
from kingfisher.presentation.cli import health
from kingfisher.presentation.cli.__main__ import main
from kingfisher.presentation.cli.health import examine, worst
from tests.conftest import an_agent, middlewares_dir, subagents_dir, tools_dir, verbs

BROKEN_TOOL = '''
from langchain_core.tools import tool


@tool
def probe(text: str) -> str:
    """A tool defined twice in one file, which is the case with no second file
    to tell them apart."""
    return text


TOOLS = [probe, probe]
'''

SHADOWING_TOOL = '''
from langchain_core.tools import tool


@tool
def read_file(text: str) -> str:
    """A workspace tool wearing a built-in's name, which is refused when a graph
    is assembled and never before."""
    return text


TOOLS = [read_file]
'''


def test_a_healthy_workspace_passes_everything(cfg):
    checks = examine(cfg)

    assert {check.name for check in checks} >= {
        "catalogue", "definitions to seed", "tools", "subagents", "skills", "shell"
    }
    assert worst(checks) != "fail"


def test_a_catalogue_that_will_not_load_is_a_failure(cfg):
    """And it names the file, because the reader has to go and fix one."""
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "broken.yaml").write_text("name: broken\n", encoding="utf-8")

    checks = {check.name: check for check in examine(cfg)}

    assert checks["subagents"].verdict == "fail"
    assert "broken.yaml" in checks["subagents"].detail
    # And the other catalogues still answered, which is the half a raised
    # exception used to take away.
    assert checks["tools"].verdict == "ok"


def test_a_broken_subagent_does_not_take_the_agents_half_down(cfg):
    """Resolving what each agent delegates to reads the subagent catalogue, so a
    broken subagent makes the agents half unanswerable -- and it is carried there
    rather than raised, like every other kind's error in this record.

    Left to escape it would reach `doctor` and `list` as a traceback over a
    workspace whose agents are fine, which is the incident three of the comments in
    `inventory` record. Nothing held it: the catch could be narrowed to `AgentError`
    alone and the whole suite stayed green.
    """
    from kingfisher.application.inventory import inventory

    an_agent(cfg, "only")
    subagents = cfg.definitions / "subagents"
    subagents.mkdir(parents=True, exist_ok=True)
    (subagents / "broken.yaml").write_text("name: [unclosed\n", encoding="utf-8")

    found = inventory(cfg)

    assert found.agents_error is not None, "a broken subagent was raised rather than carried"
    assert "broken.yaml" in found.agents_error
    # And the listing still answers for everything the subagent did not break.
    assert found.subagents_error is not None


def test_a_broken_agent_catalogue_is_a_failure_too(cfg):
    """The kind `doctor` did not look at.

    `Inventory` computed `agents_error` on every walk and `kingfisher list` read it;
    this command, whose whole job is saying what would stop a run, did not. A broken
    agent file passed `doctor` and then stopped `Kingfisher(...)` dead.
    """
    agents = cfg.definitions / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "broken.yaml").write_text("name: [unclosed\n", encoding="utf-8")

    checks = {check.name: check for check in examine(cfg)}

    assert checks["agents"].verdict == "fail"
    assert "broken.yaml" in checks["agents"].detail
    assert checks["tools"].verdict == "ok", "one catalogue must not take the others down"


def test_a_broken_tool_catalogue_is_a_failure_too(cfg):
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "twice.py").write_text(BROKEN_TOOL, encoding="utf-8")

    checks = {check.name: check for check in examine(cfg)}

    assert checks["tools"].verdict == "fail"
    assert checks["subagents"].verdict == "ok"


def test_a_tool_shadowing_a_builtin_is_a_failure_and_not_a_traceback(cfg):
    """`doctor` is where somebody goes to be told, so it must survive being right.

    This one starts a deployment perfectly well and dies on the first request that
    touches tools, so `doctor` passing it was the gap; `doctor` *crashing* on it was
    the same gap wearing a traceback.
    """
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "shadow.py").write_text(SHADOWING_TOOL, encoding="utf-8")

    checks = {check.name: check for check in examine(cfg)}

    assert checks["tools"].verdict == "fail"
    assert "read_file" in checks["tools"].detail
    assert checks["subagents"].verdict == "ok"
def test_a_broken_middleware_module_is_a_failure(cfg):
    """The fifth kind, which neither command mentioned at all.

    `doctor` reported three directories, `list` reported four, and middleware was in
    neither -- so the one kind a deployment could break with nothing said about it.
    """
    middlewares_dir(cfg).mkdir(parents=True, exist_ok=True)
    (middlewares_dir(cfg) / "wrong.py").write_text(
        "class NotMiddleware:\n    name = 'nope'\n\n\nMIDDLEWARES = [NotMiddleware]\n",
        encoding="utf-8",
    )

    checks = {check.name: check for check in examine(cfg)}

    assert checks["middlewares"].verdict == "fail"
    assert "wrong.py" in checks["middlewares"].detail
    assert checks["tools"].verdict == "ok", "one catalogue must not take the others down"


AGENT_NAMING_A_MOVED_TOOL = """
name: surveyor
description: An agent that says where its tool lives, about one that has moved.
system_prompt: |
  Survey things.
tools: [moved/elsewhere.py::probe_one]
"""

A_TOOL = """
from langchain_core.tools import tool


@tool
def probe_one(text: str) -> str:
    '''A tool that exists so a reference can be wrong about where it is.'''
    return text


TOOLS = [probe_one]
"""


def test_doctor_names_every_definition_whose_tool_moved(cfg, shipped):
    """One rename, both kinds, because the shipped presets name it from each.

    `everything=True` because the agent that names it, `analyst`, also names source
    ids, which a plain seed leaves behind.
    """
    seed(cfg, shipped, everything=True)
    (tools_dir(cfg) / "csv_profile").rename(tools_dir(cfg) / "analysis")

    checks = [c for c in examine(cfg) if c.name == "tool references"]

    assert checks and all(c.verdict == "fail" for c in checks)
    said = " | ".join(c.detail for c in checks)
    assert "agent 'analyst'" in said
    assert "subagent 'profiler'" in said


def test_doctor_sees_an_agents_moved_tool_that_nothing_else_would(cfg):
    """The half with no other symptom at all.

    A subagent naming a moved tool stops the deployment, so this only says early
    what startup would say late. An agent naming one is refused nowhere: it starts,
    runs, and quietly does not have the tool its author granted. Both halves are
    asserted here, because the second is the reason this check is a failure and the
    first is the reason it must not also stop startup.
    """
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "probe.py").write_text(A_TOOL, encoding="utf-8")
    agents = cfg.catalogue_roots["agents"]
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "surveyor.yaml").write_text(AGENT_NAMING_A_MOVED_TOOL, encoding="utf-8")

    checks = {c.name: c for c in examine(cfg)}
    assert checks["tool references"].verdict == "fail"
    assert "probe_one" in checks["tool references"].detail

    # And startup is deliberately unchanged: the agent runs, without the tool.
    Kingfisher(cfg, sessions=LocalSessions())


def test_a_clean_catalogue_says_its_tool_references_are_fine(cfg, shipped):
    """The control: the shipped presets use the long form, so this would fail if the
    check were wrong about the layout it ships with.
    """
    seed(cfg, shipped)

    checks = {c.name: c for c in examine(cfg)}

    assert checks["tool references"].verdict == "ok"


def test_doctor_names_every_definition_naming_a_subagent_there_is_not(cfg):
    """`kingfisher list` failed on these and `doctor` called the workspace fine, so
    the command whose job is to warn before a run said nothing a run would refuse.
    """
    from tests.unit.test_list import UNRESOLVED, _define

    files, _, _, _ = UNRESOLVED["a helper two files define"]
    _define(cfg, files, "[reviewer, ghost]")

    checks = [c for c in examine(cfg) if c.name == "subagent references"]

    assert checks and all(c.verdict == "fail" for c in checks)
    said = " | ".join(c.detail for c in checks)
    assert "agent 'lead' names ghost" in said
    assert "subagent 'reviewer' names second" in said


def test_a_clean_catalogue_says_its_subagent_references_are_fine(cfg, shipped):
    """The control, against the shipped presets because they name subagents from
    both kinds -- an empty workspace would pass a check that failed every name.
    """
    seed(cfg, shipped)

    checks = {c.name: c for c in examine(cfg)}

    assert checks["subagent references"].verdict == "ok"


def test_an_unconfined_shell_warns_and_does_not_fail(cfg, capsys, monkeypatch):
    """The distinction the exit code turns on."""
    from dataclasses import replace

    unconfined = replace(cfg, shell_sandbox="off")

    checks = {check.name: check for check in examine(unconfined)}

    assert checks["shell"].verdict == "warn"
    assert worst(tuple(checks.values())) == "warn"


def test_an_unconfined_shell_is_reported_once_rather_than_warned_about_too(cfg):
    """The graph `doctor` assembles to read the built-in tool set had an unconfined
    shell of its own, so the exposure its `shell` check reports arrived a second
    time above it, as a bare `UserWarning`, from a shell nothing ran.
    """
    import warnings
    from dataclasses import replace

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        checks = {check.name: check for check in examine(replace(cfg, shell_sandbox="off"))}

    assert checks["shell"].verdict == "warn", "the control: the report itself stays"
    assert not [w for w in caught if "unconfined" in str(w.message)]


def test_every_way_a_source_can_be_wrong_warns_and_says_something_different(
    cfg, tmp_path, shipped
):
    """Four states, four remedies, and never a failure."""
    from dataclasses import replace

    empty = tmp_path / "nothing-of-the-kind"
    empty.mkdir()
    cases = {
        "unset": (replace(cfg, assets=None), "KINGFISHER_ASSETS is not set"),
        "missing": (replace(cfg, assets=tmp_path / "gone"), "does not exist"),
        "empty": (replace(cfg, assets=empty), "holds none of"),
    }
    for label, (record, expected) in cases.items():
        check = {c.name: c for c in examine(record)}["definitions to seed"]
        assert check.verdict == "warn", label
        assert expected in check.detail, (label, check.detail)
        assert check.remedy, label

    # And the one that is fine, so those warnings are not simply unreachable.
    fine = {c.name: c for c in examine(replace(cfg, assets=shipped))}["definitions to seed"]
    assert fine.verdict == "ok"
    assert str(shipped) in fine.detail, "an ok detail that does not say where it looked"


def test_the_empty_source_detail_names_every_kind_it_looked_for(cfg, tmp_path):
    """Separately asserted, because "holds none of them" without the list is the puzzle
    this message exists to stop being.
    """
    from dataclasses import replace

    from kingfisher.core.definitions.catalogue import DEFINITION_KINDS

    empty = tmp_path / "empty"
    empty.mkdir()

    check = {c.name: c for c in examine(replace(cfg, assets=empty))}["definitions to seed"]

    for kind in DEFINITION_KINDS:
        assert kind in check.detail, kind


def test_the_exit_code_separates_will_not_run_from_worth_knowing(cfg, monkeypatch, shipped):
    """One place decides it, and this is what it decides."""
    from dataclasses import replace

    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setattr("tests.integration.driver.config_from_env", lambda: cfg, raising=False)
    cfg = replace(cfg, assets=shipped)

    # Under a Linux fence, so nothing is left to warn about on any host: on macOS the
    # sandbox does not keep sessions apart, and `sessions apart` says so.
    with monkeypatch.context() as fenced:
        _confined_as(fenced, mechanism="Landlock")
        ok = examine(cfg)
    warned = examine(replace(cfg, shell_sandbox="off"))
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "broken.yaml").write_text("name: broken\n", encoding="utf-8")
    failed = examine(cfg)

    assert worst(ok) == "ok"
    assert worst(warned) == "warn"
    assert worst(failed) == "fail"


def test_doctor_builds_the_model_it_needs_to_answer(cfg, monkeypatch):
    """It builds the default model, and that is deliberate: a `models.yaml` row that
    cannot start a turn is reported on its own row, and nothing but a built model knows.

    This replaces `test_nothing_here_calls_a_model`, which patched
    `models.build_model` while `agent.py` holds a direct `from ... import build_model`
    -- so it never bit, and the cost it was protecting is already paid: `deepagents`
    imports both provider packages itself.
    """
    from kingfisher.infrastructure.harness import agent as agent_module

    built: list[str] = []
    real = agent_module.build_model

    def spy(profile, endpoint):
        built.append(profile.model)
        return real(profile, endpoint)

    monkeypatch.setattr(agent_module, "build_model", spy)

    assert examine(cfg)
    assert built, "doctor answered without building a model, so it cannot see a dead row"


def test_the_command_prints_a_remedy_for_what_it_can_fix(cfg, at_the_command_line, capsys):
    """A diagnosis without an instruction sends the reader back to the docs."""
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "broken.yaml").write_text("name: broken\n", encoding="utf-8")

    assert main(["doctor"]) == 1

    printed = capsys.readouterr().out
    assert "FAIL" in printed
    assert "->" in printed


def test_the_json_form_carries_the_same_checks(cfg, at_the_command_line, capsys):
    assert main(["doctor", "--json"]) == 0

    document = json.loads(capsys.readouterr().out)
    checks = document["checks"]
    # The command line hands `examine` what `.env` added -- nothing, in a test -- and a
    # check reads it, so the comparison hands over the same.
    expected = {check.name for check in examine(cfg, from_dotenv=frozenset())}
    assert {entry["name"] for entry in checks} == expected
    for entry in checks:
        assert set(entry) == {"name", "verdict", "detail", "remedy"}


def test_both_forms_of_doctor_say_where_it_read_from(cfg, at_the_command_line, capsys):
    """An object where this was a bare list of checks, because the two forms of one
    command must not show different things.
    """

    assert main(["doctor", "--json"]) == 0
    document = json.loads(capsys.readouterr().out)

    assert set(document) == {"origins", "checks"}
    assert document["origins"]["skills"]["path"] == str(cfg.skills_dir)

    assert main(["doctor"]) == 0
    printed = capsys.readouterr().out
    # The catalogue `doctor` could count and never name. It reported "12 in the
    # workspace" and had no way to say which workspace.
    #
    # Matched without its padding, which is not laziness. The column is as wide
    # as the longest key, so this read `tools     :` until `middlewares` arrived
    # and widened every row by one -- a test that failed because the alignment
    # improved, asserting a fact about spacing while claiming one about naming.
    assert re.search(r"^tools +:", printed, re.M)
    assert str(cfg.workspace) in printed


def test_a_mistyped_definitions_folder_is_refused_not_reported_empty(
    tmp_path, monkeypatch, capsys
):
    """An empty folder at a mistyped path reads exactly like one nobody has seeded, and
    the remedies are opposite -- so a path that is not there is refused, and not made.
    """
    mistyped = tmp_path / "defintions"
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("KINGFISHER_DEFINITIONS_DIR", str(mistyped))

    assert main(["doctor"]) == 2

    assert "which is not a directory" in capsys.readouterr().err
    assert not mistyped.exists()


def test_an_empty_catalogue_where_it_belongs_says_nothing(cfg):
    """A fresh definitions folder is the ordinary state, and warning about it would be
    noise on every first run.
    """
    assert "subagents directory" not in {c.name for c in examine(cfg)}


def test_a_configuration_that_is_being_ignored_is_said_out_loud(cfg, tmp_path):
    """Otherwise somebody edits the setting and watches nothing change."""
    from kingfisher import inventory
    from kingfisher.core.definitions.catalogue import Definitions

    staged = {kind: tmp_path / kind for kind in ("agents", "skills", "subagents", "tools")}
    for path in staged.values():
        path.mkdir()

    found = inventory(cfg, catalogue=Definitions.from_roots(staged))
    checks = {c.name: c for c in examine(cfg, found)}

    assert checks["skills directory"].verdict == "warn"
    assert str(staged["skills"]) in checks["skills directory"].detail
    assert str(cfg.skills_dir) in checks["skills directory"].detail
    assert "does nothing" in checks["skills directory"].detail


def test_a_missing_catalogue_is_reported_rather_than_raised(tmp_path, monkeypatch, capsys):
    """`doctor` is what somebody runs when nothing works, so the case where the
    configuration itself is absent has to be a message, not a traceback.
    """
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.delenv("KINGFISHER_MODELS_FILE", raising=False)

    assert main(["doctor"]) == 2

    assert "configuration error" in capsys.readouterr().err


def test_a_retired_setting_is_named_even_when_the_config_will_not_load(
    tmp_path, monkeypatch, capsys
):
    """The run that most needs to hear it is the one that cannot reach `examine`.

    A deployment from when `KINGFISHER_MODEL` and `KINGFISHER_API_STYLE` chose the
    model has no `models.yaml` -- the catalogue is what replaced them -- so it is
    told the file is missing and nothing connects that to the variables it is
    still setting. Reported before the error is re-raised, so the message and the
    exit code are the ones the case above asserts.
    """
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.delenv("KINGFISHER_MODELS_FILE", raising=False)
    monkeypatch.setenv("KINGFISHER_API_STYLE", "anthropic")

    assert main(["doctor"]) == 2

    err = capsys.readouterr().err
    assert "KINGFISHER_API_STYLE" in err
    assert "configuration error" in err


def test_the_failing_run_says_nothing_when_nothing_is_retired(tmp_path, monkeypatch, capsys):
    """The control, and the reason this is not simply printed unconditionally: a
    deployment whose configuration is merely incomplete has no stale line, and a
    heading about retired settings above its error would send it looking for one.
    """
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.delenv("KINGFISHER_MODELS_FILE", raising=False)
    monkeypatch.delenv("KINGFISHER_API_STYLE", raising=False)

    assert main(["doctor"]) == 2

    assert "retired settings" not in capsys.readouterr().err


def test_the_json_form_stays_a_document_when_the_config_will_not_load(
    tmp_path, monkeypatch, capsys
):
    """`--json` is read by a machine, so a warning on stdout would be a document
    with a line of prose in front of it. Nothing is printed there at all, which is
    what this command already does on that path.
    """
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.delenv("KINGFISHER_MODELS_FILE", raising=False)
    monkeypatch.setenv("KINGFISHER_API_STYLE", "anthropic")

    assert main(["doctor", "--json"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "KINGFISHER_API_STYLE" in captured.err


# -- a credential that is absent, and what it takes down -------------------

TWO_ENDPOINTS = """
endpoints:
  gateway:
    api: anthropic
    base_url: https://example.invalid/anthropic
    key_env: GATEWAY_KEY
  elsewhere:
    api: openai_responses
    base_url: https://example.invalid/v1
    key_env: ELSEWHERE_KEY

default: main-model

models:
  main-model:
    endpoint: gateway
  far-model:
    endpoint: elsewhere
"""


def _half_keyed(cfg, tmp_path):
    """A catalogue naming two endpoints with a key for one, as a fleet does."""
    from kingfisher.infrastructure.model_catalogue import load

    path = tmp_path / "half.yaml"
    path.write_text(TWO_ENDPOINTS, encoding="utf-8")
    from dataclasses import replace

    return replace(cfg, models=load(path, {"GATEWAY_KEY": "sk-gateway"}))


def test_an_endpoint_with_no_key_is_reported_rather_than_ticked(cfg, tmp_path):
    """It used to be invisible."""
    checks = {check.name: check for check in examine(_half_keyed(cfg, tmp_path))}

    assert checks["credentials"].verdict == "warn"
    assert "ELSEWHERE_KEY" in checks["credentials"].detail
    assert "far-model" in checks["credentials"].detail


def test_a_missing_credential_is_a_warning_not_a_failure(cfg, tmp_path):
    """A shared catalogue naming endpoints this machine cannot reach is the normal case
    by the loader's own account -- one reviewed file across a fleet holding different
    subsets of keys.
    """
    checks = {check.name: check for check in examine(_half_keyed(cfg, tmp_path))}

    assert checks["credentials"].verdict == "warn"


def test_an_endpoint_no_model_uses_is_not_called_keyed(cfg, tmp_path):
    """With every model's endpoint keyed, the check said every endpoint had a key while
    the loader warned that one had none -- wrong on exactly the case the loader's
    warning exists for, a typo in a `key_env` nothing uses yet.
    """
    import pytest

    from kingfisher.config import MissingCredentialsWarning
    from kingfisher.infrastructure.model_catalogue import load

    path = tmp_path / "idle.yaml"
    path.write_text(TWO_ENDPOINTS.replace("  far-model:\n    endpoint: elsewhere\n", ""))
    with pytest.warns(MissingCredentialsWarning):
        models = load(path, {"GATEWAY_KEY": "sk-gateway"})
    assert not models.unreachable, "the premise: no model sits on the dropped endpoint"

    from dataclasses import replace

    checks = {check.name: check for check in examine(replace(cfg, models=models))}

    assert checks["credentials"].verdict == "warn"
    assert "ELSEWHERE_KEY" in checks["credentials"].detail


def test_doctor_says_a_missing_key_once_and_inside_its_report(cfg, monkeypatch, capsys):
    """`doctor` printed the loader's `UserWarning` about a missing key above its report,
    and then its `credentials` check said the same thing again inside it.
    """
    import warnings

    (cfg.definitions / "models.yaml").write_text(TWO_ENDPOINTS, encoding="utf-8")
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_DEFINITIONS_DIR", str(cfg.definitions))
    monkeypatch.setenv("GATEWAY_KEY", "sk-gateway")
    monkeypatch.delenv("ELSEWHERE_KEY", raising=False)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        main(["doctor"])

    assert not [w for w in caught if "ELSEWHERE_KEY" in str(w.message)]
    assert "ELSEWHERE_KEY" in capsys.readouterr().out, "the control: the check still says it"


def test_a_definition_that_cannot_run_is_named(cfg, tmp_path):
    """The check nothing else does."""
    half = _half_keyed(cfg, tmp_path)
    subagents_dir(half).mkdir(parents=True, exist_ok=True)
    (subagents_dir(half) / "far.yaml").write_text(
        "name: far\ndescription: Runs somewhere this machine cannot reach.\n"
        "model: far-model\nsystem_prompt: |\n  Answer.\n",
        encoding="utf-8",
    )

    checks = {check.name: check for check in examine(half)}

    assert checks["definition 'far'"].verdict == "fail"
    assert "ELSEWHERE_KEY" in checks["definition 'far'"].detail
    assert checks["definition 'far'"].remedy


def test_an_agent_that_cannot_run_is_named_too(cfg, tmp_path):
    """The kind the check did not ask about.

    It called `unrunnable_subagents` alone, so the row said "every definition resolves
    to a model" and `doctor` exited 0 while `run --agent` refused with the very
    configuration error the row had promised was absent -- and catching that is the one
    thing this verb's `--help` claims for it.
    """
    half = _half_keyed(cfg, tmp_path)
    agents = half.catalogue_roots["agents"]
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "faraway.yaml").write_text(
        "name: faraway\ndescription: Runs somewhere this machine cannot reach.\n"
        "model: far-model\nsystem_prompt: |\n  Answer.\n",
        encoding="utf-8",
    )

    checks = {check.name: check for check in examine(half)}

    assert checks["definition 'faraway'"].verdict == "fail"
    assert "ELSEWHERE_KEY" in checks["definition 'faraway'"].detail
    assert "definitions run" not in checks, (
        "the row claimed everything runs, beside the one that does not"
    )


def test_a_broken_agent_catalogue_does_not_take_the_definitions_check_with_it(cfg):
    """The agent half of the guard above it: both catalogues are read here now, so an
    agent file that will not load raises out of this check unless it is asked first.
    """
    agents = cfg.catalogue_roots["agents"]
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "broken.yaml").write_text("name: [unclosed\n", encoding="utf-8")

    checks = {check.name: check for check in examine(cfg)}

    assert checks["definitions run"].verdict == "warn"
    assert "agent catalogue did not load" in checks["definitions run"].detail


def test_the_builtin_count_says_unknown_when_it_could_not_be_read(cfg):
    """A middleware that will not import takes the probe the built-in set is read off
    down with it, and `tools_error` is deliberately left alone -- the tool catalogue is
    fine and the middlewares row carries the fault with the file to open.

    So this row printed `0 built in` with an `ok` beside it over a deployment that has
    ten, and `--json` handed a script `[]` with `tools_error: null`.
    """
    folder = cfg.catalogue_roots["middlewares"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "broken.py").write_text("import nonexistent_module_xyz\n", encoding="utf-8")

    found = inventory(cfg)
    checks = {check.name: check for check in examine(cfg, found)}

    assert not found.builtin_tools_checked
    assert "0 built in" not in checks["tools"].detail
    assert "built-in set unknown" in checks["tools"].detail
    # The control: the middlewares row is where the fault itself is reported, and the
    # tools row must not claim the tool catalogue failed.
    assert checks["middlewares"].verdict == "fail"
    assert found.tools_error is None


def test_definitions_that_all_run_say_so_once(cfg, tmp_path):
    """The negative control, and it is one line rather than one per definition: a clean
    deployment should not scroll.
    """
    half = _half_keyed(cfg, tmp_path)
    subagents_dir(half).mkdir(parents=True, exist_ok=True)
    (subagents_dir(half) / "near.yaml").write_text(
        "name: near\ndescription: Runs on what this machine has.\n"
        "system_prompt: |\n  Answer.\n",
        encoding="utf-8",
    )

    checks = {check.name: check for check in examine(half)}

    assert checks["definitions run"].verdict == "ok"
    assert not [name for name in checks if name.startswith("definition ")]


def test_a_broken_catalogue_does_not_take_the_definitions_check_with_it(cfg):
    """`unrunnable_subagents` reads the same files, so a definition that will not load
    raises out of it.
    """
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "broken.yaml").write_text("name: broken\n", encoding="utf-8")

    checks = {check.name: check for check in examine(cfg)}

    assert checks["subagents"].verdict == "fail"
    assert checks["definitions run"].verdict == "warn"
    assert checks["shell"].verdict in {"ok", "warn"}  # and the rest still ran


def test_the_catalogue_line_says_what_it_did_not_check(cfg):
    """A green tick that does not say so claims more than it knows: the check is that a
    credential is *present*, never that it works.
    """
    checks = {check.name: check for check in examine(cfg)}

    assert "not tested" in checks["catalogue"].detail


def test_the_description_names_its_own_limit():
    """A command that never calls a model has to say so where it is read."""
    from kingfisher.presentation.cli.__main__ import build_parser

    description = verbs(build_parser())["doctor"].description or ""

    assert "may still be" in description  # present is not working
    assert "kingfisher.run" in description  # and here is what would prove it


# -- settings nothing reads any more -----------------------------------------


def test_a_retired_setting_is_reported(cfg, monkeypatch):
    """The whole of what replaced reading both names. A variable that stops being
    read is silent by construction -- nothing warns about an unknown `KINGFISHER_`
    one -- so without this a deployment carrying `KINGFISHER_SKILLS=true` runs with
    skills off and has nowhere to find out why.
    """
    monkeypatch.setenv("KINGFISHER_SKILLS", "true")

    check = {c.name: c for c in examine(cfg)}["retired settings"]

    assert check.verdict == "warn"
    assert "KINGFISHER_SKILLS" in check.detail
    assert "KINGFISHER_SKILLS_ENABLED" in check.remedy


def test_the_service_settings_are_reported_by_their_prefixes(cfg, monkeypatch):
    """Matched rather than enumerated, so a suffix nobody listed is still caught --
    under both names the HTTP service's settings had, because a deployment still
    carrying either is setting something that no longer does anything.
    """
    monkeypatch.setenv("KINGFISHER_SERVER_INVENTED_LATER", "1")
    monkeypatch.setenv("KINGFISHER_SERVICE_PORT", "9001")

    check = {c.name: c for c in examine(cfg)}["retired settings"]

    assert "KINGFISHER_SERVER_INVENTED_LATER" in check.detail
    assert "KINGFISHER_SERVICE_PORT" in check.detail
    assert "removed" in check.remedy


def test_a_retired_name_left_empty_is_not_reported(cfg, monkeypatch):
    """`FOO=` in an env file is a deployment that has already stopped using it, and
    a warning there is one people learn to scroll past.
    """
    monkeypatch.setenv("KINGFISHER_SCRATCH_DIR", "   ")

    assert "retired settings" not in {c.name for c in examine(cfg)}


def test_a_deployment_on_current_names_hears_nothing(cfg, monkeypatch):
    """The path every deployment ends on, and the one that would be intolerable to
    make noisy: a current name must never look retired.
    """
    monkeypatch.delenv("KINGFISHER_SKILLS", raising=False)
    monkeypatch.setenv("KINGFISHER_SKILLS_ENABLED", "true")
    # Shares its stem with both retired prefixes, and is read.
    monkeypatch.setenv("KINGFISHER_SESSION_TTL_S", "3600")

    assert "retired settings" not in {c.name for c in examine(cfg)}


def test_a_retired_setting_does_not_stop_the_deployment(cfg, monkeypatch):
    """A warning and not a failure: it runs, on the defaults, which may well be what
    the deployment wanted. `doctor` exiting non-zero over a line somebody forgot to
    delete would make an upgrade look broken when it is merely untidy.
    """
    monkeypatch.setenv("KINGFISHER_MODEL", "something-retired")

    assert worst(examine(cfg)) != "fail"


# -- a workspace that must keep nothing --------------------------------------


def _backing(monkeypatch, sessions=None, **fields):
    """Answer as a given machine would, without needing to be one.

    `**fields` describe the workspace's device and, unless `sessions` says
    otherwise, the sessions tree's as well -- which is every deployment that has
    not mounted one. Answering per path rather than once is what lets a test
    express a mount at all: a helper that returned one reading for both would
    pass just as happily against a `_at_rest` that had stopped looking at the
    sessions tree, which is the thing these tests are here to hold.
    """
    from kingfisher.domain.session import sessions_root
    from kingfisher.infrastructure.workspace import MemoryBacking

    on_the_workspace = MemoryBacking(**fields)
    on_the_sessions = MemoryBacking(**sessions) if sessions is not None else on_the_workspace
    # Taken from `sessions_root` rather than spelled, so a layout that renames
    # the directory renames it here too.
    marker = sessions_root(Path("/anywhere")).name

    monkeypatch.setattr(
        "kingfisher.presentation.cli.health.memory_backing",
        lambda path: on_the_sessions if Path(path).name == marker else on_the_workspace,
    )


def test_a_tmpfs_mounted_under_sessions_is_what_gets_measured(cfg, monkeypatch):
    """The arrangement `doctor` could not see: sessions on a memory filesystem the
    workspace is not on. `_mounted_filesystem` answers for the longest mountpoint
    containing the path it is handed, so a reading of the workspace reports the
    workspace's disk and never the mount sessions are written to -- and every check
    below it stayed silent on the one deployment they exist for.
    """
    _backing(
        monkeypatch,
        sessions={
            "filesystem": "tmpfs", "size_bytes": 300 * 1024**2,
            "limit_bytes": 400 * 1024**2, "swap_enabled": False,
        },
        filesystem="ext4", size_bytes=10**12,
    )

    checks = {c.name: c for c in examine(cfg)}

    assert checks["nothing at rest"].verdict == "ok"
    assert checks["sessions survive"].verdict == "fail"
    assert checks["session quota"].verdict == "fail"


def test_sessions_on_a_disk_under_a_memory_workspace_is_a_failure(cfg, monkeypatch):
    """The inverse mount, and the reason gating on the sessions tree is not enough on
    its own: every check here follows the sessions reading, so a workspace that
    asserted it keeps nothing while its sessions land on a disk would produce no
    output whatsoever.
    """
    _backing(
        monkeypatch,
        sessions={"filesystem": "ext4", "size_bytes": 10**12},
        filesystem="tmpfs", size_bytes=300 * 1024**2,
        limit_bytes=400 * 1024**2, swap_enabled=False,
    )

    check = {c.name: c for c in examine(cfg)}["nothing at rest"]

    assert check.verdict == "fail"
    assert "keeping every session" in check.detail
    assert check.remedy


def test_every_message_names_both_devices(cfg, monkeypatch):
    """A reader deciding whether these numbers describe the disk they mounted should
    not have to infer it from an absence -- so no message here reports one filesystem
    while silently measuring another.
    """
    _backing(
        monkeypatch,
        sessions={
            "filesystem": "tmpfs", "size_bytes": 300 * 1024**2,
            "limit_bytes": 400 * 1024**2, "swap_enabled": False,
        },
        filesystem="ext4", size_bytes=10**12,
    )

    at_rest = [c for c in examine(cfg) if c.name in
               {"nothing at rest", "sessions survive", "session quota"}]

    assert at_rest
    for check in at_rest:
        assert "tmpfs" in check.detail and "ext4" in check.detail, check


def test_a_workspace_nobody_has_seeded_still_reports_a_size(tmp_path):
    """`_size_of` stats the path it is given, so measuring a sessions directory that
    does not exist yet would answer `None` and turn every sized check into the
    unknown-limit branch -- on the one run that matters most, `doctor` before
    `kingfisher seed`, which is when a tmpfs is being sized.
    """
    from kingfisher.infrastructure.workspace import memory_backing

    bare = tmp_path / "unseeded"
    bare.mkdir()

    assert health._sessions_probe(bare) == bare
    assert memory_backing(health._sessions_probe(bare)).size_bytes is not None


def test_a_laid_out_workspace_is_measured_at_its_sessions(workspace):
    """The control for the fallback above: where the directory exists, that is what
    gets stat'd, or the mount would never be the thing measured.
    """
    from kingfisher.domain.session import sessions_root

    assert health._sessions_probe(workspace) == sessions_root(workspace)


def test_an_ordinary_disk_says_nothing_at_all(cfg, monkeypatch):
    """A deployment allowed to hold data on its own disk is not misconfigured for doing
    so, and a check that fired anyway would be one people learn to scroll past.
    """
    _backing(monkeypatch, filesystem="ext4", size_bytes=10**12)

    assert "nothing at rest" not in {check.name for check in examine(cfg)}


def test_swapping_fails_because_it_is_the_silent_one(cfg, monkeypatch):
    """The measured behaviour, and the reason this check exists at all: a memory
    filesystem larger than the limit does not refuse when it fills, it swaps -- the
    write succeeds, no error appears, and the bytes are on a disk.
    """
    _backing(
        monkeypatch, filesystem="tmpfs", size_bytes=512 * 1024**2,
        limit_bytes=1024 * 1024**2, swap_enabled=True,
    )

    check = {c.name: c for c in examine(cfg)}["nothing at rest"]

    assert check.verdict == "fail"
    assert "swap" in check.detail
    assert check.remedy


def test_a_filesystem_larger_than_the_limit_fails(cfg, monkeypatch):
    """The other half of the same trap."""
    _backing(
        monkeypatch, filesystem="tmpfs", size_bytes=2048 * 1024**2,
        limit_bytes=400 * 1024**2, swap_enabled=False,
    )

    check = {c.name: c for c in examine(cfg)}["nothing at rest"]

    assert check.verdict == "fail"
    assert "2048MB" in check.detail and "400MB" in check.detail


def test_no_memory_limit_at_all_warns(cfg, monkeypatch):
    """Not a failure: a memory filesystem on a host with no cgroup limit is what a
    developer's machine looks like, and it runs.
    """
    _backing(monkeypatch, filesystem="tmpfs", size_bytes=512 * 1024**2, swap_enabled=False)

    check = {c.name: c for c in examine(cfg)}["nothing at rest"]

    assert check.verdict == "warn"


def test_the_arrangement_that_works_says_so(cfg, monkeypatch):
    """Smaller than the limit, swap off."""
    _backing(
        monkeypatch, filesystem="tmpfs", size_bytes=300 * 1024**2,
        limit_bytes=400 * 1024**2, swap_enabled=False,
    )

    check = {c.name: c for c in examine(cfg)}["nothing at rest"]

    assert check.verdict == "ok"
    assert "no swap" in check.detail


def test_a_memory_workspace_with_nowhere_to_keep_sessions_fails(cfg, monkeypatch):
    """The combination that loses everything and says nothing."""
    _backing(
        monkeypatch, filesystem="tmpfs", size_bytes=300 * 1024**2,
        limit_bytes=400 * 1024**2, swap_enabled=False,
    )

    check = {c.name: c for c in examine(cfg)}["sessions survive"]

    assert check.verdict == "fail"
    assert "KINGFISHER_SESSIONS" in check.remedy


def test_no_quota_fails_only_once_memory_is_shared(cfg, monkeypatch, tmp_path):
    """Unset means unbounded, which is survivable on a disk and is not survivable in
    memory every session in the container shares.
    """
    from dataclasses import replace

    _backing(
        monkeypatch, filesystem="tmpfs", size_bytes=300 * 1024**2,
        limit_bytes=400 * 1024**2, swap_enabled=False,
    )
    wired = replace(cfg, sessions_factory="mystores:build", session_max_bytes=None)

    check = {c.name: c for c in examine(wired)}["session quota"]

    assert check.verdict == "fail"


def test_a_quota_larger_than_the_filesystem_warns_that_it_cannot_bind(cfg, monkeypatch, tmp_path):
    """A number that can never be reached is not a limit."""
    from dataclasses import replace

    _backing(
        monkeypatch, filesystem="tmpfs", size_bytes=100 * 1024**2,
        limit_bytes=400 * 1024**2, swap_enabled=False,
    )
    wired = replace(
        cfg, sessions_factory="mystores:build", session_max_bytes=1024 * 1024**2
    )

    check = {c.name: c for c in examine(wired)}["session quota"]

    assert check.verdict == "warn"


def test_none_of_this_fires_on_an_ordinary_disk(cfg, monkeypatch):
    """Neither check has an opinion about a deployment allowed to hold data."""
    _backing(monkeypatch, filesystem="ext4", size_bytes=10**12)

    names = {check.name for check in examine(cfg)}

    assert "sessions survive" not in names
    assert "session quota" not in names


def test_a_runtime_confined_shell_reads_as_ok_rather_than_a_warning(cfg, monkeypatch):
    """`KINGFISHER_SHELL_SANDBOX=external` is a deployment saying a container already
    mounts only the workspace.
    """
    from dataclasses import replace

    check = {c.name: c for c in examine(replace(cfg, shell_sandbox="external"))}["shell"]

    assert check.verdict == "ok"
    assert "runtime" in check.detail


# -- what could fence the shell, and on what kernel -------------------------


def test_the_probe_answers_nothing_where_there_is_no_landlock():
    """Landlock is a Linux thing, and asking anywhere else must not raise -- this runs
    inside `doctor`, whose whole job is to survive a host that is wrong in some way
    and report it.
    """
    from kingfisher.plugins.sessions.sandbox.confinement import landlock_abi

    assert landlock_abi() is None or platform.system() == "Linux"


def test_an_unconfined_shell_is_told_what_this_kernel_could_do(cfg, monkeypatch):
    """The point of step 1: an operator learns where they stand rather than only that
    they are somewhere bad.
    """
    from dataclasses import replace

    monkeypatch.setattr(health.platform, "system", lambda: "Linux")
    monkeypatch.setattr(health.platform, "release", lambda: "6.12.0")
    monkeypatch.setattr(health.confinement, "landlock_abi", lambda: 6)

    check = {c.name: c for c in examine(replace(cfg, shell_sandbox="off"))}["shell"]

    assert check.verdict == "warn"
    assert "6.12.0" in check.remedy and "ABI 6" in check.remedy


def test_a_kernel_below_the_full_ruleset_is_told_it_is_below(cfg, monkeypatch):
    """S6: a fence that quietly becomes weaker on a different node is worse than one
    that says so.
    """
    from dataclasses import replace

    monkeypatch.setattr(health.platform, "system", lambda: "Linux")
    monkeypatch.setattr(health.platform, "release", lambda: "6.1.0")
    monkeypatch.setattr(health.confinement, "landlock_abi", lambda: 4)

    remedy = {c.name: c for c in examine(replace(cfg, shell_sandbox="off"))}["shell"].remedy

    assert "ABI 4" in remedy
    assert "below" in remedy and "weaker" in remedy


def test_a_kernel_with_no_landlock_is_not_offered_one(cfg, monkeypatch):
    """The answer that changes what an operator should do: no fence is coming on this
    host, so the container is the only boundary available.
    """
    from dataclasses import replace

    monkeypatch.setattr(health.platform, "system", lambda: "Linux")
    monkeypatch.setattr(health.platform, "release", lambda: "5.10.0")
    monkeypatch.setattr(health.confinement, "landlock_abi", lambda: None)

    remedy = {c.name: c for c in examine(replace(cfg, shell_sandbox="off"))}["shell"].remedy

    assert "no Landlock" in remedy
    assert "external" in remedy


def test_a_kernel_that_can_fence_is_told_what_is_missing_rather_than_to_wait(cfg, monkeypatch):
    """The remedy told a kernel that could already be fenced to wait "until that is
    wired", beside a warning from the fence naming the one package that was missing.
    """
    from dataclasses import replace

    monkeypatch.setattr(health.platform, "system", lambda: "Linux")
    monkeypatch.setattr(health.platform, "release", lambda: "6.12.0")
    monkeypatch.setattr(health.confinement, "landlock_abi", lambda: 6)
    monkeypatch.setattr(health.confinement, "landlock_ready", lambda: False)
    # Named rather than inherited from the machine. "Not ready" has two causes now,
    # and on a Linux runner with the extra installed this would otherwise assert
    # against the other one -- green here, red in CI.
    monkeypatch.setattr(health.confinement, "sandlock_minimum", lambda: None)

    remedy = {c.name: c for c in examine(replace(cfg, shell_sandbox="off"))}["shell"].remedy

    assert "kingfisher[fence]" in remedy
    assert "wired" not in remedy
    assert "below" not in remedy


def test_a_kernel_ready_to_fence_is_told_the_one_setting_that_turns_it_on(cfg, monkeypatch):
    """`off` on a host with Landlock and `sandlock` both in place is one setting away."""
    from dataclasses import replace

    monkeypatch.setattr(health.platform, "system", lambda: "Linux")
    monkeypatch.setattr(health.platform, "release", lambda: "6.12.0")
    monkeypatch.setattr(health.confinement, "landlock_abi", lambda: 6)
    monkeypatch.setattr(health.confinement, "landlock_ready", lambda: True)

    remedy = {c.name: c for c in examine(replace(cfg, shell_sandbox="off"))}["shell"].remedy

    assert "KINGFISHER_SHELL_SANDBOX=auto" in remedy
    assert "kingfisher[fence]" not in remedy


def test_the_warning_and_the_remedy_name_the_same_missing_piece(cfg, monkeypatch):
    """On `auto` the warning is the fence's account of why it is not running and the
    remedy is doctor's. Faked once, at the fence, so a reading of the kernel of
    doctor's own would show here as the two disagreeing.
    """
    from dataclasses import replace

    from kingfisher.plugins.sessions.sandbox import bubblewrap

    monkeypatch.setattr(health.platform, "system", lambda: "Linux")
    monkeypatch.setattr(health.platform, "release", lambda: "6.12.0")
    monkeypatch.setattr(health.confinement, "landlock_abi", lambda: 6)
    monkeypatch.setattr(health.confinement, "landlock_ready", lambda: False)
    monkeypatch.setattr(health.confinement, "sandlock_minimum", lambda: None)
    monkeypatch.setattr(bubblewrap, "bubblewrap_available", lambda: False)

    check = {c.name: c for c in examine(replace(cfg, shell_sandbox="auto"))}["shell"]

    assert check.verdict == "warn"
    assert "sandlock" in check.detail
    assert "sandlock" in check.remedy


def test_a_confined_shell_names_what_is_confining_it(monkeypatch):
    """"confined" was true and unhelpful.

    Asserted against `_mechanism` rather than a real `examine`, because the confined
    branch is only reachable on a host with `sandbox-exec`, and a test that quietly
    asserts nothing on the CI runner is worse than no test.
    """
    from kingfisher.plugins.sessions.sandbox.confinement import Confinement, _unwrapped

    assert health._mechanism(Confinement(wrap=lambda c: c, mechanism="sandbox-exec")) == (
        "sandbox-exec"
    )

    # And bubblewrap says more than its name, because `auto` reaching it means
    # the shell lost its network as well as its reach. An operator whose skill
    # suddenly cannot download anything should find the reason here.
    said = health._mechanism(Confinement(wrap=_unwrapped, mechanism="bubblewrap"))
    assert "bubblewrap" in said
    assert "no network" in said


def test_the_doctor_says_which_answer_it_is_giving(cfg):
    """It builds a `Config` from the environment and never sees a `Kingfisher`, so a
    deployment supplying its own runner or session root is invisible to it.
    """
    check = {c.name: c for c in examine(cfg)}["shell"]

    assert "from configuration" in check.detail
    assert "not visible here" in check.detail


def test_a_supplied_local_runner_is_named_beside_the_mechanism():
    """It still receives the confined command, so the mechanism holds and has only
    gained company.
    """
    from kingfisher.plugins.sessions.sandbox.confinement import Confinement

    said = health._mechanism(
        Confinement(wrap=lambda c: c, mechanism="sandbox-exec", supplied=True)
    )

    assert "sandbox-exec" in said
    assert "supplied runner" in said


# -- a check that could not run is not a check that passed -------------------


def _broken(cfg, where: str) -> None:
    """A working bundled subagent, with one file broken in the place `where` names."""
    from tests.unit.test_bundles import BROKEN, OWN_TOOL_ONLY, workspace_with_bundle

    workspace_with_bundle(cfg, definition=OWN_TOOL_ONLY)
    subagents = cfg.definitions / "subagents"
    target, text = {
        "the subagent's own tool": (subagents / "surveyor" / "tools" / "probe.py", BROKEN),
        "a catalogue tool": (cfg.definitions / "tools" / "shared.py", BROKEN),
        "a subagent file": (subagents / "bad.yaml", "name: [\n"),
    }[where]
    target.write_text(text, encoding="utf-8")


#: Which of the checks each breakage stops, and which it leaves able to run.
#: Measured rather than reasoned: a broken catalogue tool sets the bundles' error too,
#: and the folder check still runs -- so `doctor` cannot infer "not checked" from the
#: other rows' errors, and each of these says what it actually did.
STOPPED_BY = {
    "the subagent's own tool": {
        "bundled entries": "warn",
        "tool references": "ok",
        "subagent references": "ok",
    },
    "a catalogue tool": {
        "bundled entries": "ok",
        "tool references": "warn",
        "subagent references": "ok",
    },
    "a subagent file": {
        "bundled entries": "warn",
        "tool references": "warn",
        "subagent references": "warn",
    },
}


@pytest.mark.parametrize("where", sorted(STOPPED_BY))
def test_a_check_that_could_not_run_says_so_rather_than_passing(cfg, where):
    """Both read a catalogue, and both answered "ok" when it would not load: an empty
    result reads the same as a clean one. With a subagent's tool broken, `doctor` said
    `bundled entries: ok -- every subagent lists exactly its own folder` for a folder
    it never read.

    The `ok` cells are the control -- a check that said "not checked" whenever
    anything at all was broken would pass every `warn` here.
    """
    _broken(cfg, where)

    verdicts = {check.name: check for check in examine(cfg)}

    for name, expected in STOPPED_BY[where].items():
        assert verdicts[name].verdict == expected, (name, verdicts[name].detail)
        if expected == "warn":
            assert verdicts[name].detail.startswith("not checked"), name


# -- where each key came from, and who else can read it ----------------------


def _keyed_three_ways(cfg):
    """One endpoint keyed from the keys file, one from a variable, one built in code."""
    from dataclasses import replace

    fake, elsewhere = cfg.models.endpoints["fake"], cfg.models.endpoints["elsewhere"]
    return replace(
        cfg,
        models=replace(
            cfg.models,
            endpoints={
                "fake": replace(fake, key_env="", key_entry="minimax"),
                "elsewhere": replace(elsewhere, key_env="OPENAI_API_KEY"),
                "coded": replace(elsewhere, key_env=""),
            },
        ),
    )


def test_credentials_say_where_each_key_came_from(cfg):
    """With two places a key can come from, "every endpoint has a key" no longer says
    which one it is using.
    """
    (check,) = [c for c in examine(_keyed_three_ways(cfg)) if c.name == "credentials"]

    assert check.verdict == "ok"
    assert 'fake: keys file, entry "minimax"' in check.detail
    assert "elsewhere: OPENAI_API_KEY" in check.detail
    assert "coded: set in code" in check.detail


def test_a_missing_key_still_says_where_the_others_came_from(cfg, tmp_path):
    from kingfisher.config import MissingCredentialsWarning

    with pytest.warns(MissingCredentialsWarning):
        half = _half_keyed(cfg, tmp_path)
    (check,) = [c for c in examine(half) if c.name == "credentials"]

    assert check.verdict == "warn"
    assert "elsewhere (ELSEWHERE_KEY is not set)" in check.detail
    assert "gateway: GATEWAY_KEY" in check.detail
    assert "keys file" in check.remedy, "the remedy only knew about variables"


def _with_keys_file(cfg, path, **change):
    from dataclasses import replace

    return replace(cfg, models=replace(cfg.models, keys_file=path), **change)


def _fenced_by(monkeypatch, mechanism):
    """The mechanism doctor sees, whatever this host would choose."""
    from kingfisher.plugins.sessions.sandbox.confinement import Confinement

    seen = Confinement(wrap=lambda command: command, mechanism=mechanism)
    monkeypatch.setattr(health, "shell_confinement", lambda cfg, **kw: seen)


def _row(checks, name):
    found = [c for c in checks if c.name == name]
    return found[0] if found else None


def test_a_keys_file_the_shell_can_read_is_warned_about(cfg, tmp_path, monkeypatch):
    """Inside the workspace on a Mac is the mistake a renamed example makes; outside the
    home folder is the one a check about the workspace alone would call safe.
    """
    from kingfisher.plugins.sessions.sandbox.confinement import SANDBOX_EXEC

    monkeypatch.setenv("HOME", str(tmp_path))
    _fenced_by(monkeypatch, SANDBOX_EXEC)
    inside, outside = cfg.workspace / "keys.yaml", tmp_path.parent / "elsewhere" / "keys.yaml"

    for path, says in ((inside, str(cfg.workspace)), (outside, "outside the home folder")):
        check = _row(examine(_with_keys_file(cfg, path)), "keys file")
        assert check is not None and check.verdict == "warn", path
        assert says in check.detail
        assert "home folder" in check.remedy


@pytest.mark.parametrize("mechanism", LINUX_FENCES, ids=str)
def test_a_keys_file_a_linux_fence_grants_is_warned_about(cfg, tmp_path, monkeypatch, mechanism):
    """The whole Linux branch of `shell_readable` was undriven: replacing its body with
    `return ()` left the suite green, and this row then said the sandbox kept a keys
    file that the fence in fact grants.

    Linux is the deployment platform, so what stood between an operator and a false
    "the sandbox keeps it from the agent's shell" was untested code. Both fences,
    because each is built from its own list of system paths -- one list serving both is
    how a mechanism gains a path it meant to deny.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    _fenced_by(monkeypatch, mechanism)
    granted = cfg.skills_dir / "keys.yaml"

    check = _row(examine(_with_keys_file(cfg, granted)), "keys file")

    assert check is not None and check.verdict == "warn"
    assert str(cfg.skills_dir) in check.detail
    assert mechanism in check.detail, "the row did not say which fence grants it"
    assert "what the fence grants" in check.remedy


@pytest.mark.parametrize("mechanism", LINUX_FENCES, ids=str)
def test_a_keys_file_a_linux_fence_does_not_grant_is_said_to_be_hidden(
    cfg, tmp_path, monkeypatch, mechanism
):
    """The control beside the rule above: a file neither fence grants is reported as
    kept away, so the warning there is this check discriminating rather than warning
    about everything it is shown.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    _fenced_by(monkeypatch, mechanism)
    elsewhere = tmp_path / ".config" / "kingfisher" / "keys.yaml"

    check = _row(examine(_with_keys_file(cfg, elsewhere)), "keys file")

    assert check is not None and check.verdict == "ok", check
    assert "keeps from the agent's shell" in check.detail


def test_a_keys_file_the_sandbox_hides_is_said_to_be_hidden(cfg, tmp_path, monkeypatch):
    from kingfisher.plugins.sessions.sandbox.confinement import SANDBOX_EXEC

    monkeypatch.setenv("HOME", str(tmp_path))
    _fenced_by(monkeypatch, SANDBOX_EXEC)
    hidden = tmp_path / ".config" / "kingfisher" / "keys.yaml"

    check = _row(examine(_with_keys_file(cfg, hidden)), "keys file")

    assert check is not None and check.verdict == "ok"
    assert _row(examine(cfg), "keys file") is None, "no keys file named, nothing to say"


def test_with_no_fence_a_keys_file_is_readable_wherever_it_is(cfg, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _fenced_by(monkeypatch, "")
    hidden = tmp_path / ".config" / "kingfisher" / "keys.yaml"

    check = _row(examine(_with_keys_file(cfg, hidden, shell_sandbox="off")), "keys file")

    assert check is not None and check.verdict == "warn"


def test_session_backends_doctor_cannot_see_into_are_said_so(cfg, tmp_path, monkeypatch):
    """The same answer `session files` gives them: their shell may run on another machine,
    and nothing here can tell.
    """
    from kingfisher.plugins.sessions.sandbox.confinement import SANDBOX_EXEC

    monkeypatch.setenv("HOME", str(tmp_path))
    _fenced_by(monkeypatch, SANDBOX_EXEC)
    hidden = tmp_path / ".config" / "kingfisher" / "keys.yaml"
    named = _with_keys_file(cfg, hidden, sessions_factory="deployment:Remote")

    check = _row(examine(named), "keys file")

    assert check is not None and check.verdict == "warn"
    assert "KINGFISHER_SESSIONS" in check.detail


def test_a_session_reached_through_a_link_of_its_own_is_named(cfg, tmp_path, monkeypatch):
    """A static profile cannot bind it -- it is written per workspace, before the
    session exists -- so the session is unusable and every command in it fails at `cd`
    with a message about the shell. This row is the only thing that says why.
    """
    from kingfisher.plugins.sessions.sandbox.confinement import SANDBOX_EXEC

    _fenced_by(monkeypatch, SANDBOX_EXEC)
    far = tmp_path / "far"
    far.mkdir()
    (cfg.workspace / "sessions" / "strayed").symlink_to(far)

    check = _row(examine(cfg), "session paths")

    assert check is not None and check.verdict == "warn"
    assert "strayed" in check.detail
    assert "sessions" in check.remedy


def test_doctor_answers_on_a_workspace_nothing_has_laid_out_yet(cfg, tmp_path, monkeypatch):
    """`doctor` is the command you run *because* a deployment is not working, so it has
    to answer on a workspace that does not exist yet. The session scan guards against
    that, and nothing reached the guard: removing it left all 3,041 tests green while
    `examine` raised `FileNotFoundError` on the sessions directory -- and `examine`
    catches `ConfigError` only, so the traceback is what the operator gets.

    That is the shape of the entry this check was added beside: one bad row took
    `doctor`, `list`, `sessions` and `reap` down together, a slice earlier.
    """
    from kingfisher.plugins.sessions.sandbox.confinement import SANDBOX_EXEC

    _fenced_by(monkeypatch, SANDBOX_EXEC)
    nowhere = replace(cfg, workspace=tmp_path / "never-made")

    checks = examine(nowhere)

    assert checks, "no rows at all, so this says nothing about the scan"
    assert _row(checks, "session paths") is None


def test_a_link_to_something_that_is_not_a_session_is_not_named(cfg, tmp_path, monkeypatch):
    """The row names *sessions* reached through a link, and the only thing in
    `sessions/` that can be one is a directory. A link to a file outside the workspace
    would otherwise be reported as a strayed session, sending an operator to move
    something that is not one -- and no fixture put a non-directory there, so the
    filter was indistinguishable from `True`.
    """
    from kingfisher.plugins.sessions.sandbox.confinement import SANDBOX_EXEC

    _fenced_by(monkeypatch, SANDBOX_EXEC)
    beside = tmp_path / "notes.txt"
    beside.write_text("not a session", encoding="utf-8")
    (cfg.workspace / "sessions" / "notes.txt").symlink_to(beside)

    assert _row(examine(cfg), "session paths") is None


def test_a_session_inside_the_sessions_directory_is_not_named(cfg, tmp_path, monkeypatch):
    """The control: a row on every workspace tells an operator to go and look at an
    arrangement that is the ordinary one.
    """
    from kingfisher.plugins.sessions.sandbox.confinement import SANDBOX_EXEC

    _fenced_by(monkeypatch, SANDBOX_EXEC)
    (cfg.workspace / "sessions" / "ordinary").mkdir(parents=True)

    assert _row(examine(cfg), "session paths") is None


def test_sessions_moved_all_at_once_are_not_named(cfg, tmp_path, monkeypatch):
    """The other control, and the one that matters: moving every session with one link
    on `sessions` is the arrangement the profile now follows, so warning about it would
    send an operator to undo a thing that works.
    """
    from kingfisher.plugins.sessions.sandbox.confinement import SANDBOX_EXEC

    _fenced_by(monkeypatch, SANDBOX_EXEC)
    far = tmp_path / "far"
    far.mkdir()
    (far / "ordinary").mkdir()
    (cfg.workspace / "sessions").rmdir()
    (cfg.workspace / "sessions").symlink_to(far)

    assert _row(examine(cfg), "session paths") is None


def test_a_linked_session_is_not_named_where_the_fence_grants_it_by_path(
    cfg, tmp_path, monkeypatch
):
    """Quiet under a Linux fence, and that is not tidiness: `policy_for` is built from
    the session directory itself, so the link is followed when it is opened and the
    rules land on the real directory. A row there would send an operator to undo an
    arrangement that works.
    """
    from kingfisher.plugins.sessions.sandbox.confinement import LANDLOCK

    _fenced_by(monkeypatch, LANDLOCK)
    far = tmp_path / "far"
    far.mkdir()
    (cfg.workspace / "sessions" / "strayed").symlink_to(far)

    assert _row(examine(cfg), "session paths") is None


def test_a_linked_session_is_not_named_for_backends_doctor_cannot_see_into(
    cfg, tmp_path, monkeypatch
):
    """The sessions directory on this host is not theirs, so what is in it says nothing
    about where their sessions are -- the answer `session files` already gives them.
    """
    from dataclasses import replace

    from kingfisher.plugins.sessions.sandbox.confinement import SANDBOX_EXEC

    _fenced_by(monkeypatch, SANDBOX_EXEC)
    far = tmp_path / "far"
    far.mkdir()
    (cfg.workspace / "sessions" / "strayed").symlink_to(far)
    theirs = replace(cfg, sessions_factory="deployment:Remote")

    assert _row(examine(theirs), "session paths") is None


def test_a_keys_file_other_users_can_read_is_warned_about(cfg, tmp_path):
    """Other people on the machine, not the agent -- the expectation ssh holds its keys to."""
    path = tmp_path / "keys.yaml"
    path.write_text("minimax: sk-x\n", encoding="utf-8")

    # Group-readable as well as world-readable: a group is other users too.
    for mode in (0o644, 0o640):
        path.chmod(mode)
        check = _row(examine(_with_keys_file(cfg, path)), "keys file mode")
        assert check is not None and check.verdict == "warn", f"{mode:o} passed"
        assert f"chmod 600 {path}" in check.remedy
    path.chmod(0o600)
    assert _row(examine(_with_keys_file(cfg, path)), "keys file mode") is None


def test_a_key_in_the_starting_environment_is_warned_about_where_it_is_read(cfg, monkeypatch):
    """On a Mac, or with no fence, the shell reads a process's environment as it started
    -- so an exported key is readable, and one `.env` added afterwards is not.
    """
    from kingfisher.plugins.sessions.sandbox.confinement import BUBBLEWRAP, SANDBOX_EXEC

    keyed = _keyed_three_ways(cfg)

    _fenced_by(monkeypatch, SANDBOX_EXEC)
    exported = _row(examine(keyed, from_dotenv=frozenset()), "starting environment")
    loaded = frozenset({"OPENAI_API_KEY"})
    from_file = _row(examine(keyed, from_dotenv=loaded), "starting environment")
    unknown = _row(examine(keyed), "starting environment")
    _fenced_by(monkeypatch, BUBBLEWRAP)
    hidden = _row(examine(keyed, from_dotenv=frozenset()), "starting environment")

    assert exported is not None and exported.verdict == "warn"
    assert "OPENAI_API_KEY" in exported.detail
    assert "keys file" in exported.remedy
    assert from_file is None, "a key .env added is not in the starting environment"
    assert unknown is None, "a caller that cannot say what .env added is told nothing"
    assert hidden is None, "bubblewrap shows the shell no other process"


def test_the_command_line_says_which_keys_env_added(cfg, tmp_path, monkeypatch, capsys):
    """Driven through `main`, with a real `.env`: the half of the rule no examine-level
    test reaches is the command line recording what it loaded.
    """
    (cfg.definitions / "models.yaml").write_text(TWO_ENDPOINTS, encoding="utf-8")
    (tmp_path / ".env").write_text("GATEWAY_KEY=sk-from-dotenv\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GATEWAY_KEY", raising=False)
    monkeypatch.setenv("ELSEWHERE_KEY", "sk-exported")
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_DEFINITIONS_DIR", str(cfg.definitions))
    monkeypatch.setenv("KINGFISHER_SHELL_SANDBOX", "off")

    main(["doctor"])

    said = [line for line in capsys.readouterr().out.splitlines() if "starting environment" in line]
    assert said, "nothing was said about the starting environment"
    assert "ELSEWHERE_KEY" in said[0]
    assert "GATEWAY_KEY" not in said[0], "a key .env loaded was called exported"


# -- one session reaching another -------------------------------------------------


def _confined_as(monkeypatch, **seen):
    """The confinement doctor sees, whatever this host would choose."""
    from kingfisher.plugins.sessions.sandbox.confinement import Confinement

    confined = Confinement(wrap=lambda command: command, **seen)
    monkeypatch.setattr(health, "shell_confinement", lambda cfg, **kw: confined)


@pytest.mark.parametrize(
    ("seen", "says", "remedy"),
    [
        ({"warning": "unconfined"}, "nothing kingfisher runs confines", "sandbox on"),
        ({"mechanism": "sandbox-exec"}, "macOS sandbox", "workspace of their own"),
        ({"elsewhere": True}, "'external'", "workspace of their own"),
    ],
    ids=["no-fence", "macos", "external"],
)
def test_sessions_that_can_reach_each_other_are_said_to(cfg, monkeypatch, seen, says, remedy):
    """`shell` says what the host is exposed to. What it never said is the consequence for
    people sharing one workspace: one session's agent can read and change another's.
    """
    _confined_as(monkeypatch, **seen)

    check = _row(examine(cfg), "sessions apart")

    assert check is not None and check.verdict == "warn"
    assert "read and change another session's files" in check.detail
    assert says in check.detail
    assert remedy in check.remedy


@pytest.mark.parametrize("mechanism", ["Landlock", "bubblewrap"])
def test_a_linux_fence_keeps_sessions_apart_and_is_not_warned_about(cfg, monkeypatch, mechanism):
    """Each turn is handed its own session and nothing else, so there is nothing to say."""
    _confined_as(monkeypatch, mechanism=mechanism)

    assert _row(examine(cfg), "sessions apart") is None


def test_session_backends_doctor_cannot_see_into_are_left_to_their_contract(cfg, monkeypatch):
    """Whether they keep sessions apart is theirs to show, and `tests/checks/sessions.py`
    asks them; a third "cannot see" line on every check of such a deployment is noise.
    """
    from dataclasses import replace

    _confined_as(monkeypatch, warning="unconfined")
    named = replace(cfg, sessions_factory="deployment:Remote")

    assert _row(examine(named), "sessions apart") is None
