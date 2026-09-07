"""`kingfisher doctor`: everything between an install and a run."""

from __future__ import annotations

import json
import platform

from kingfisher.presentation.cli import health
from kingfisher.presentation.cli.__main__ import main
from kingfisher.presentation.cli.health import examine, worst
from tests.conftest import subagents_dir, tools_dir, verbs

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


def test_a_broken_tool_catalogue_is_a_failure_too(cfg):
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "twice.py").write_text(BROKEN_TOOL, encoding="utf-8")

    checks = {check.name: check for check in examine(cfg)}

    assert checks["tools"].verdict == "fail"
    assert checks["subagents"].verdict == "ok"


def test_an_unconfined_shell_warns_and_does_not_fail(cfg, capsys, monkeypatch):
    """The distinction the exit code turns on."""
    from dataclasses import replace

    unconfined = replace(cfg, shell_sandbox="off")

    checks = {check.name: check for check in examine(unconfined)}

    assert checks["shell"].verdict == "warn"
    assert worst(tuple(checks.values())) == "warn"


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

    # And the one that is fine, so the three above are not simply unreachable.
    fine = {c.name: c for c in examine(replace(cfg, assets=shipped))}["definitions to seed"]
    assert fine.verdict == "ok"
    assert str(shipped) in fine.detail, "an ok detail that does not say where it looked"


def test_the_empty_source_detail_names_every_kind_it_looked_for(cfg, tmp_path):
    """Separately asserted, because "holds none of them" without the list is the puzzle
    this message exists to stop being.
    """
    from dataclasses import replace

    from kingfisher.infrastructure.catalogue import DEFINITION_KINDS

    empty = tmp_path / "empty"
    empty.mkdir()

    check = {c.name: c for c in examine(replace(cfg, assets=empty))}["definitions to seed"]

    for kind in DEFINITION_KINDS:
        assert kind in check.detail, kind


def test_the_unset_remedy_says_where_your_own_definitions_go(cfg, tmp_path, monkeypatch):
    """`doctor` is where a deployment learns it has nothing to seed from, and the remedy
    said only where to point -- never where to put.
    """
    from dataclasses import replace

    from kingfisher.infrastructure.workspace.seeding import DESTINATION

    unset = replace(cfg, assets=None)

    monkeypatch.chdir(tmp_path)
    bare = {c.name: c for c in examine(unset)}["definitions to seed"]

    (tmp_path / DESTINATION).mkdir()
    beside_one = {c.name: c for c in examine(unset)}["definitions to seed"]

    assert str(DESTINATION) not in bare.remedy, "named a directory that is not there"
    assert str(DESTINATION) in beside_one.remedy, "did not name the one that is"


def test_the_exit_code_separates_will_not_run_from_worth_knowing(cfg, monkeypatch, shipped):
    """One place decides it, and this is what it decides."""
    from dataclasses import replace

    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setattr("tests.integration.driver.config_from_env", lambda: cfg, raising=False)
    cfg = replace(cfg, assets=shipped)

    ok = examine(cfg)
    warned = examine(replace(cfg, shell_sandbox="off"))
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "broken.yaml").write_text("name: broken\n", encoding="utf-8")
    failed = examine(cfg)

    assert worst(ok) == "ok"
    assert worst(warned) == "warn"
    assert worst(failed) == "fail"


def test_nothing_here_calls_a_model(cfg, monkeypatch):
    """The line that keeps this cheap enough to run before every deployment."""
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
    checks = document["checks"]
    assert {entry["name"] for entry in checks} == {check.name for check in examine(cfg)}
    for entry in checks:
        assert set(entry) == {"name", "verdict", "detail", "remedy"}


def test_both_forms_of_doctor_say_where_it_read_from(cfg, monkeypatch, capsys):
    """An object where this was a bare list of checks, because the two forms of one
    command must not show different things.
    """
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_MODELS_FILE", str(_catalogue(cfg)))
    monkeypatch.setenv("FAKE_KEY", "not-a-real-key")

    assert main(["doctor", "--json"]) == 0
    document = json.loads(capsys.readouterr().out)

    assert set(document) == {"origins", "checks"}
    assert document["origins"]["skills"]["path"] == str(cfg.skills_dir)

    assert main(["doctor"]) == 0
    printed = capsys.readouterr().out
    # The catalogue `doctor` could count and never name. It reported "12 in the
    # workspace" and had no way to say which workspace.
    assert "tools     :" in printed
    assert str(cfg.workspace) in printed


def test_an_empty_catalogue_somebody_pointed_at_is_not_an_empty_workspace(cfg, tmp_path):
    """The two look identical and the remedies are opposite."""
    from dataclasses import replace

    elsewhere = tmp_path / "staged-subagents"
    elsewhere.mkdir()

    checks = {c.name: c for c in examine(replace(cfg, subagents_root=elsewhere))}

    assert checks["subagents directory"].verdict == "warn"
    assert str(elsewhere) in checks["subagents directory"].detail
    assert "created rather than refused" in checks["subagents directory"].detail


def test_an_empty_catalogue_where_it_belongs_says_nothing(cfg):
    """A fresh workspace is the ordinary state, and warning about it would make the
    check above noise on every first run.
    """
    assert "subagents directory" not in {c.name for c in examine(cfg)}


def test_a_relocated_catalogue_that_holds_something_says_nothing(cfg, tmp_path):
    """Sharing a catalogue across deployments is the arrangement the setting exists for."""
    from dataclasses import replace

    from tests.conftest import an_agent

    moved = replace(cfg, agents_root=tmp_path / "shared-agents")
    (tmp_path / "shared-agents").mkdir()
    an_agent(moved, "only")

    assert "agents directory" not in {c.name for c in examine(moved)}


def test_a_configuration_that_is_being_ignored_is_said_out_loud(cfg, tmp_path):
    """Otherwise somebody edits the setting and watches nothing change."""
    from kingfisher import inventory
    from kingfisher.infrastructure.catalogue import Definitions

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
    """`unrunnable_delegates` reads the same files, so a definition that will not load
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


def test_reaching_the_cli_stays_free_of_provider_sdks():
    """`unrunnable_delegates` costs 868ms and 3,137 modules to import, so at the top of
    `health` every verb would pay it -- `kingfisher help` would spend a second to
    print text.
    """
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


def test_the_description_names_its_own_limit():
    """A command that never calls a model has to say so where it is read."""
    from kingfisher.presentation.cli.__main__ import build_parser

    description = verbs(build_parser())["doctor"].description or ""

    assert "may still be" in description  # present is not working
    assert "kingfisher.run" in description  # and here is what would prove it


# -- a workspace that must keep nothing --------------------------------------


def _backing(monkeypatch, **fields):
    """Answer as a given machine would, without needing to be one."""
    from kingfisher.infrastructure.workspace.backing import MemoryBacking

    monkeypatch.setattr(
        "kingfisher.presentation.cli.health.memory_backing",
        lambda _workspace: MemoryBacking(**fields),
    )


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
    assert "KINGFISHER_SESSION_STORE" in check.remedy


def test_no_quota_fails_only_once_memory_is_shared(cfg, monkeypatch, tmp_path):
    """Unset means unbounded, which is survivable on a disk and is not survivable in
    memory every session in the container shares.
    """
    from dataclasses import replace

    _backing(
        monkeypatch, filesystem="tmpfs", size_bytes=300 * 1024**2,
        limit_bytes=400 * 1024**2, swap_enabled=False,
    )
    wired = replace(cfg, session_store=tmp_path / "kept", session_max_bytes=None)

    check = {c.name: c for c in examine(wired)}["session quota"]

    assert check.verdict == "fail"


def test_a_quota_larger_than_the_filesystem_warns_that_it_cannot_bind(cfg, monkeypatch, tmp_path):
    """A number that can never be reached is not a limit."""
    from dataclasses import replace

    _backing(
        monkeypatch, filesystem="tmpfs", size_bytes=100 * 1024**2,
        limit_bytes=400 * 1024**2, swap_enabled=False,
    )
    wired = replace(cfg, session_store=tmp_path / "kept", session_max_bytes=1024 * 1024**2)

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
    from kingfisher.infrastructure.sandbox.confinement import landlock_abi

    assert landlock_abi() is None or platform.system() == "Linux"


def test_an_unconfined_shell_is_told_what_this_kernel_could_do(cfg, monkeypatch):
    """The point of step 1: an operator learns where they stand rather than only that
    they are somewhere bad.
    """
    from dataclasses import replace

    monkeypatch.setattr(health.platform, "system", lambda: "Linux")
    monkeypatch.setattr(health.platform, "release", lambda: "6.12.0")
    monkeypatch.setattr(health, "landlock_abi", lambda: 6)

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
    monkeypatch.setattr(health, "landlock_abi", lambda: 4)

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
    monkeypatch.setattr(health, "landlock_abi", lambda: None)

    remedy = {c.name: c for c in examine(replace(cfg, shell_sandbox="off"))}["shell"].remedy

    assert "no Landlock" in remedy
    assert "external" in remedy


def test_a_confined_shell_names_what_is_confining_it(monkeypatch):
    """"confined" was true and unhelpful."""
    from kingfisher.infrastructure.sandbox.confinement import Confinement, _unwrapped

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
    from kingfisher.infrastructure.sandbox.confinement import Confinement

    said = health._mechanism(
        Confinement(wrap=lambda c: c, mechanism="sandbox-exec", supplied=True)
    )

    assert "sandbox-exec" in said
    assert "supplied runner" in said
