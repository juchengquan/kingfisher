"""`kingfisher doctor`: everything between an install and a run.

The checks existed and were scattered -- a `ConfigError` here, a warning inside
`model_catalogue.load` there, `warn_if_unconfined` in a driver the wheel does not
ship. Diagnosing a deployment meant provoking each in turn.

The distinction these hold hardest is failure against warning. An unconfined
shell is a deployment's choice and must not make the command non-zero, or it
goes unrun in exactly the deployments most worth checking.
"""

from __future__ import annotations

import json

from kingfisher.presentation.cli.__main__ import main
from kingfisher.presentation.cli.health import examine, worst
from tests.conftest import subagents_dir, tools_dir

BROKEN_TOOL = '''
from langchain_core.tools import tool


@tool
def probe(text: str) -> str:
    """A tool defined twice in one file, which is the case with no second file
    to tell them apart."""
    return text


TOOLS = [probe, probe]
'''


def test_a_healthy_workspace_passes_everything(cfg):
    checks = examine(cfg)

    assert {check.name for check in checks} >= {
        "catalogue", "aliases", "definitions to seed", "tools", "subagents", "skills", "shell"
    }
    assert worst(checks) != "fail"


def test_an_unbound_alias_is_a_failure(cfg):
    """It refuses at build time, one request in -- so a deployment can start,
    look healthy, and fail the first time somebody activates the delegate that
    names it. That is exactly the class of problem this command is for."""
    from dataclasses import replace

    broken = replace(cfg, models=replace(cfg.models, aliases={"cheap": "a-model-nobody-defined"}))

    checks = {check.name: check for check in examine(broken)}

    assert checks["aliases"].verdict == "fail"
    assert "a-model-nobody-defined" in checks["aliases"].detail
    assert checks["aliases"].remedy


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


def test_a_broken_tool_catalogue_is_a_failure_too(cfg):
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "twice.py").write_text(BROKEN_TOOL, encoding="utf-8")

    checks = {check.name: check for check in examine(cfg)}

    assert checks["tools"].verdict == "fail"
    assert checks["subagents"].verdict == "ok"


def test_an_unconfined_shell_warns_and_does_not_fail(cfg, capsys, monkeypatch):
    """The distinction the exit code turns on.

    Plenty of deployments run unconfined on purpose -- in a container that is
    already the boundary. A `doctor` that failed there would be a command those
    deployments stop running, and then it is not checking anything at all.
    """
    from dataclasses import replace

    unconfined = replace(cfg, shell_sandbox="off")

    checks = {check.name: check for check in examine(unconfined)}

    assert checks["shell"].verdict == "warn"
    assert worst(tuple(checks.values())) == "warn"


def test_no_asset_pack_warns_rather_than_failing(cfg, monkeypatch):
    """The definitions ship with kingfisher, so this only happens to a damaged
    install -- which is exactly the case worth naming, since a broken install
    and an empty workspace look identical from outside."""
    # Patched where `health` bound it, not where it is defined. The module
    # imports the name at the front door, so that binding is the live one and
    # patching `seeding.shipped_kinds` would change nothing.
    monkeypatch.setattr("kingfisher.presentation.cli.health.shipped_kinds", tuple)

    checks = {check.name: check for check in examine(cfg)}

    assert checks["definitions to seed"].verdict == "warn"
    assert checks["definitions to seed"].remedy


def test_the_exit_code_separates_will_not_run_from_worth_knowing(cfg, monkeypatch):
    """One place decides it, and this is what it decides."""
    from dataclasses import replace

    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setattr("main.from_env", lambda: cfg, raising=False)

    ok = examine(cfg)
    warned = examine(replace(cfg, shell_sandbox="off"))
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "broken.yaml").write_text("name: broken\n", encoding="utf-8")
    failed = examine(cfg)

    assert worst(ok) == "ok"
    assert worst(warned) == "warn"
    assert worst(failed) == "fail"


def test_nothing_here_calls_a_model(cfg, monkeypatch):
    """The line that keeps this cheap enough to run before every deployment.

    A credential that is present can still be wrong, and finding out means
    spending money on a call nobody asked for. Asserted by making any outbound
    build explode: `examine` must not reach one.
    """
    from kingfisher.infrastructure.harness import models

    reached = "doctor built a model"

    def _refuse(*args, **kwargs):
        raise AssertionError(reached)

    monkeypatch.setattr(models, "build_model", _refuse)

    assert examine(cfg)  # it still answered


def test_the_command_prints_a_remedy_for_what_it_can_fix(cfg, monkeypatch, capsys):
    """A diagnosis without an instruction sends the reader back to the docs."""
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_MODELS_FILE", str(_catalogue(cfg)))
    monkeypatch.setenv("FAKE_KEY", "not-a-real-key")
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "broken.yaml").write_text("name: broken\n", encoding="utf-8")

    assert main(["doctor"]) == 1

    printed = capsys.readouterr().out
    assert "FAIL" in printed
    assert "->" in printed


def test_the_json_form_carries_the_same_checks(cfg, monkeypatch, capsys):
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_MODELS_FILE", str(_catalogue(cfg)))
    monkeypatch.setenv("FAKE_KEY", "not-a-real-key")

    assert main(["doctor", "--json"]) == 0

    document = json.loads(capsys.readouterr().out)
    assert {entry["name"] for entry in document} == {check.name for check in examine(cfg)}
    for entry in document:
        assert set(entry) == {"name", "verdict", "detail", "remedy"}


def test_a_missing_catalogue_is_reported_rather_than_raised(tmp_path, monkeypatch, capsys):
    """`doctor` is what somebody runs when nothing works, so the case where the
    configuration itself is absent has to be a message, not a traceback."""
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.delenv("KINGFISHER_MODELS_FILE", raising=False)

    assert main(["doctor"]) == 2

    assert "configuration error" in capsys.readouterr().err


def _catalogue(cfg):
    path = cfg.workspace / "models.yaml"
    path.write_text(
        "endpoints:\n  fake:\n    api: anthropic\n"
        "    base_url: http://127.0.0.1:9/never-called\n    key_env: FAKE_KEY\n"
        "default: fake-model\n"
        "models:\n  fake-model:\n    endpoint: fake\n",
        encoding="utf-8",
    )
    return path


# -- a credential that is absent, and what it takes down -------------------

TWO_ENDPOINTS = """
endpoints:
  gateway:
    api: anthropic
    base_url: https://example.invalid/anthropic
    key_env: GATEWAY_KEY
  elsewhere:
    api: openai
    base_url: https://example.invalid/v1
    key_env: ELSEWHERE_KEY

default: main-model

models:
  main-model:
    endpoint: gateway
  far-model:
    endpoint: elsewhere

aliases:
  alternate: far-model
"""


def _half_keyed(cfg, tmp_path):
    """A catalogue naming two endpoints with a key for one, as a fleet does."""
    from kingfisher.infrastructure.model_catalogue import load

    path = tmp_path / "half.yaml"
    path.write_text(TWO_ENDPOINTS, encoding="utf-8")
    from dataclasses import replace

    return replace(cfg, models=load(path, {"GATEWAY_KEY": "sk-gateway"}))


def test_an_endpoint_with_no_key_is_reported_rather_than_ticked(cfg, tmp_path):
    """It used to be invisible. The drop is announced by a warning at load and
    then discarded, so `doctor` counted the survivors and printed `ok` over a
    catalogue that had lost an endpoint."""
    checks = {check.name: check for check in examine(_half_keyed(cfg, tmp_path))}

    assert checks["credentials"].verdict == "warn"
    assert "ELSEWHERE_KEY" in checks["credentials"].detail
    assert "far-model" in checks["credentials"].detail


def test_a_missing_credential_is_a_warning_not_a_failure(cfg, tmp_path):
    """A shared catalogue naming endpoints this machine cannot reach is the
    normal case by the loader's own account -- one reviewed file across a fleet
    holding different subsets of keys. Failing here fails the arrangement the
    format encourages."""
    checks = {check.name: check for check in examine(_half_keyed(cfg, tmp_path))}

    assert checks["credentials"].verdict == "warn"


def test_a_bound_alias_is_no_longer_called_unbound(cfg, tmp_path):
    """The misdiagnosis this whole change began with.

    `alternate` binds `far-model`, which the file defines. Its endpoint has no
    key here, so it was reported as "named in `aliases:`, not defined under
    `models:`" with a remedy pointing at correct YAML. The loader had already
    decided this case was legal; the check re-derived it and got it backwards.
    """
    checks = {check.name: check for check in examine(_half_keyed(cfg, tmp_path))}

    assert checks["aliases"].verdict == "ok"


def test_a_definition_that_cannot_run_is_named(cfg, tmp_path):
    """The check nothing else does.

    A delegate binding an alias to an unreachable model leaves a workspace that
    loads, lists cleanly, and fails on the first request naming it. The build
    refuses it then; `doctor` exists to be the before.
    """
    half = _half_keyed(cfg, tmp_path)
    subagents_dir(half).mkdir(parents=True, exist_ok=True)
    (subagents_dir(half) / "far.yaml").write_text(
        "name: far\ndescription: Runs somewhere this machine cannot reach.\n"
        "alias: alternate\nsystem_prompt: |\n  Answer.\n",
        encoding="utf-8",
    )

    checks = {check.name: check for check in examine(half)}

    assert checks["definition 'far'"].verdict == "fail"
    assert "ELSEWHERE_KEY" in checks["definition 'far'"].detail
    assert checks["definition 'far'"].remedy


def test_definitions_that_all_run_say_so_once(cfg, tmp_path):
    """The negative control, and it is one line rather than one per definition:
    a clean deployment should not scroll."""
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
    """`unrunnable_delegates` reads the same files, so a definition that will
    not load raises out of it. A diagnosis that stops at the first problem is
    what this command exists to replace, and the check above already said so."""
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "broken.yaml").write_text("name: broken\n", encoding="utf-8")

    checks = {check.name: check for check in examine(cfg)}

    assert checks["subagents"].verdict == "fail"
    assert checks["definitions run"].verdict == "warn"
    assert checks["shell"].verdict in {"ok", "warn"}  # and the rest still ran


def test_the_catalogue_line_says_what_it_did_not_check(cfg):
    """A green tick that does not say so claims more than it knows: the check is
    that a credential is *present*, never that it works."""
    checks = {check.name: check for check in examine(cfg)}

    assert "not tested" in checks["catalogue"].detail


def test_reaching_the_cli_stays_free_of_provider_sdks():
    """`unrunnable_delegates` costs 868ms and 3,137 modules to import, so at the
    top of `health` every verb would pay it -- `kingfisher help` would spend a
    second to print text. Imported inside the check instead."""
    import subprocess
    import sys

    probe = (
        "import sys\n"
        "import kingfisher.presentation.cli.__main__\n"
        "print(','.join(m for m in ('deepagents', 'langchain_openai',"
        " 'langchain_anthropic') if m in sys.modules))"
    )
    out = subprocess.run(  # noqa: S603 -- our own interpreter, our own literal
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )

    assert out.stdout.strip() == ""
