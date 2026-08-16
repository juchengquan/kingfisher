"""What keeps `execute` off the rest of the host.

The behavioural tests here run a real shell through a real sandbox rather than
inspecting the profile text, because a profile that parses is not the same claim
as a file that cannot be read. They are macOS-only for the same reason the
feature is: `sandbox-exec` is what this platform offers, and a test that asserted
the wrapping without exercising it would pass on a machine where the boundary
does not exist.
"""

from __future__ import annotations

import asyncio
import platform
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from kingfisher.infrastructure import confinement
from kingfisher.infrastructure.backend import build_backend

macos = pytest.mark.skipif(
    platform.system() != "Darwin", reason="sandbox-exec is the macOS mechanism"
)


# -- choosing one ---------------------------------------------------------


def test_off_is_warned_about_on_every_start(cfg, tmp_path):
    """An exposure nobody is reminded of is one nobody fixes."""
    chosen = confinement.resolve(
        confinement.OFF, workspace=cfg.workspace, state_dir=tmp_path, scratch_dir=tmp_path
    )

    assert not chosen.confined
    assert "unconfined" in chosen.warning
    assert chosen.wrap("ls") == "ls", "off must not wrap"


def test_external_is_silent_because_the_runtime_already_did_it(cfg, tmp_path):
    """A container that mounts only the workspace has provided the boundary.
    Warning there would train operators to ignore the warning."""
    chosen = confinement.resolve(
        confinement.EXTERNAL, workspace=cfg.workspace, state_dir=tmp_path, scratch_dir=tmp_path
    )

    assert chosen.warning == ""
    assert chosen.wrap("ls") == "ls"


def test_an_unknown_mode_is_refused_rather_than_treated_as_off(cfg, tmp_path):
    """A typo in a deployment's env must not silently unconfine it."""
    with pytest.raises(ValueError, match="unknown shell sandbox mode"):
        confinement.resolve(
            "sandbox", workspace=cfg.workspace, state_dir=tmp_path, scratch_dir=tmp_path
        )


@macos
def test_auto_confines_and_says_nothing(cfg, tmp_path):
    chosen = confinement.resolve(
        confinement.AUTO, workspace=cfg.workspace, state_dir=tmp_path, scratch_dir=tmp_path
    )

    assert chosen.confined
    assert chosen.warning == ""
    assert (tmp_path / "shell.sb").is_file(), "profile not written"


def test_the_profile_lives_in_harness_state_not_the_workspace(cfg, tmp_path):
    """A boundary the agent can edit is not a boundary. `/runs` and `/derived`
    are writable through the file tools; the state directory is not addressable
    at all."""
    confinement.resolve(
        confinement.AUTO, workspace=cfg.workspace, state_dir=tmp_path, scratch_dir=tmp_path
    )

    assert not list(Path(cfg.workspace).rglob("shell.sb"))


# -- what the profile says ------------------------------------------------


def test_the_interpreter_stays_readable_or_python_stops_working(cfg, tmp_path):
    """A venv's `python3` is usually a symlink onto an interpreter installed
    under the home -- uv puts it in `~/.local/share/uv/`. Denying the home
    without re-allowing `sys.base_prefix` leaves the agent unable to run Python
    at all, which is how this was found.
    """
    import sys

    roots = confinement.readable_roots(cfg.workspace)

    assert Path(sys.base_prefix).resolve() in roots
    assert Path(cfg.workspace).resolve() in roots


def test_paths_a_deployment_put_on_path_stay_readable(cfg, tmp_path):
    """`shell_path_extra` is a deployment saying "the agent may run these"."""
    roots = confinement.readable_roots(cfg.workspace, ("/opt/homebrew/bin",))

    assert Path("/opt/homebrew/bin") in roots


# -- the part that actually has to hold -----------------------------------


@macos
def test_the_shell_cannot_read_a_secret_outside_the_workspace(cfg, session_dir, tmp_path):
    """The measured hole: unconfined, this shell could read the deployment's own
    API keys, `~/.aws`, and the GitHub CLI's token. `http_fetch` is a registered
    tool, so reading and sending are one turn apart.

    Written against a file in the operator's home rather than a real credential,
    so the test proves the boundary without depending on what happens to be on
    the machine.
    """
    secret = Path.home() / ".kingfisher-confinement-probe"
    secret.write_text("token", encoding="utf-8")
    try:
        backend = build_backend(cfg, session_dir)
        result = backend.execute(f"cat {secret}")

        assert "token" not in str(result.output), "the shell read a file in the home"
        assert "not permitted" in str(result.output).lower()
    finally:
        secret.unlink(missing_ok=True)


@macos
def test_the_async_path_is_confined_too(cfg, session_dir):
    """`aexecute` is what the interpreter's code-side dispatch runs on, so the
    boundary must not depend on which entry point a caller happened to use."""
    secret = Path.home() / ".kingfisher-confinement-probe-async"
    secret.write_text("token", encoding="utf-8")
    try:
        backend = build_backend(cfg, session_dir)
        result = asyncio.run(backend.aexecute(f"cat {secret}"))

        assert "token" not in str(result.output), "aexecute read a file in the home"
    finally:
        secret.unlink(missing_ok=True)


def test_the_async_path_still_routes_through_execute(cfg, session_dir):
    """`ConfinedShell` overrides only `execute`, because upstream's `aexecute`
    is `asyncio.to_thread(self.execute, ...)`. Overriding both wrapped every
    async command twice, nesting one sandbox inside another -- which still
    confined, so nothing failed and only the command string showed it.

    This pins the delegation that makes one override sufficient. A deepagents
    release that gives `aexecute` its own body fails here, rather than leaving
    the async path unconfined and silent.
    """
    seen: list[str] = []
    backend = build_backend(cfg, session_dir)
    shell = backend.default
    shell.confinement = replace(shell.confinement, wrap=lambda c: (seen.append(c), c)[1])

    asyncio.run(backend.aexecute("echo hi"))

    assert seen == ["echo hi"], (
        "aexecute no longer routes through execute exactly once -- "
        f"the wrapper saw {seen}"
    )


@macos
def test_the_workspace_itself_stays_fully_usable(cfg, session_dir):
    """Confinement that broke the agent's own working directory would be
    swapped straight back out."""
    backend = build_backend(cfg, session_dir)
    backend.write("/derived/note.txt", "hello")

    result = backend.execute("cat derived/note.txt")

    assert result.exit_code == 0
    assert "hello" in str(result.output)


@macos
def test_python_still_runs_with_its_dependencies(cfg, session_dir):
    """The whole point of the re-allowed roots."""
    backend = build_backend(cfg, session_dir)

    result = backend.execute('python3 -c "import yaml; print(\'deps ok\')"')

    assert result.exit_code == 0, f"python broke under confinement: {result.output}"
    assert "deps ok" in str(result.output)


@macos
def test_a_command_with_shell_metacharacters_still_runs_confined(cfg, session_dir):
    """The agent's command is quoted into the outer `sandbox-exec` invocation.
    Getting that wrong either breaks ordinary pipelines or lets the command
    break out of the wrapper -- so both halves are checked.
    """
    backend = build_backend(cfg, session_dir)

    piped = backend.execute("echo 'a b' | tr ' ' '-'")
    assert piped.exit_code == 0
    assert "a-b" in str(piped.output)

    escape = backend.execute(f"cat {Path.home()}/.zshrc 2>&1; echo done")
    assert "done" in str(escape.output)
    assert "not permitted" in str(escape.output).lower() or "No such file" in str(escape.output)


@macos
def test_off_really_does_leave_the_shell_open(cfg, session_dir):
    """The escape hatch has to actually be an escape hatch -- otherwise a
    deployment that hits a false positive has no way past it."""
    secret = Path.home() / ".kingfisher-confinement-probe-off"
    secret.write_text("token", encoding="utf-8")
    try:
        backend = build_backend(replace(cfg, shell_sandbox=confinement.OFF), session_dir)

        assert "token" in str(backend.execute(f"cat {secret}").output)
    finally:
        secret.unlink(missing_ok=True)


# -- writes: the rules system.md only asked for ---------------------------


@macos
def test_the_shell_cannot_write_outside_the_workspace(cfg, session_dir):
    """`system.md` says to stop and report rather than reach outside the
    workspace. In an observed run the agent did it anyway, so the rule is the
    kernel's now."""
    target = Path.home() / ".kingfisher-write-probe"
    backend = build_backend(cfg, session_dir)
    try:
        result = backend.execute(f"echo pwned > {target}")

        assert not target.exists(), "the shell wrote into the operator's home"
        assert "not permitted" in str(result.output).lower()
    finally:
        target.unlink(missing_ok=True)


@macos
def test_a_literal_tmp_write_is_refused(cfg, session_dir):
    """The prompt says to write scratch under `$TMPDIR`, never a literal
    `/tmp`. The observed run wrote `/tmp/preview.pdf` regardless. `/tmp` is
    world-writable, which is the reason the rule exists."""
    target = Path("/tmp/kingfisher-write-probe")
    backend = build_backend(cfg, session_dir)
    try:
        backend.execute(f"echo pwned > {target}")

        assert not target.exists(), "the shell wrote to a literal /tmp"
    finally:
        target.unlink(missing_ok=True)


@macos
def test_installing_into_the_environment_is_refused(cfg, session_dir):
    """Two `pip install` attempts in one observed run. They failed only because
    this venv has no `pip` -- which is luck, not a boundary. The venv stays
    readable so Python runs, and unwritable so nothing can be added to it."""
    import sys

    backend = build_backend(cfg, session_dir)
    probe = Path(sys.prefix) / "kingfisher-write-probe"
    # Cleaned in `finally` because the interesting runs are the ones where the
    # write *succeeds*: without this, a failing assertion leaves a file in the
    # venv and every later run of this test fails on the leftover rather than
    # on the boundary. Observed while mutation-testing it.
    try:
        result = backend.execute(f"touch {probe}")

        assert not probe.exists(), "the shell wrote into the environment"
        assert "not permitted" in str(result.output).lower()
    finally:
        probe.unlink(missing_ok=True)


@macos
def test_tmpdir_stays_writable_wherever_it_is_pointed(cfg, session_dir, tmp_path):
    """`$TMPDIR` is where the prompt sends scratch, and
    `KINGFISHER_SCRATCH_DIR` can move it out of the workspace. Denying it would
    close the one place the agent is told to use."""
    outside = tmp_path / "scratch-elsewhere"
    relocated = replace(cfg, scratch_root=outside)
    backend = build_backend(relocated, session_dir)

    result = backend.execute('echo hi > "$TMPDIR/note.txt" && cat "$TMPDIR/note.txt"')

    assert result.exit_code == 0, f"$TMPDIR is not writable: {result.output}"
    assert "hi" in str(result.output)


@macos
def test_redirecting_to_dev_null_still_works(cfg, session_dir):
    """It appears in about half the commands an agent writes."""
    backend = build_backend(cfg, session_dir)

    result = backend.execute("echo noise 2>/dev/null && echo ok")

    assert result.exit_code == 0
    assert "ok" in str(result.output)


@macos
def test_the_agent_can_still_write_everything_it_is_meant_to(cfg, session_dir):
    """Confinement that broke the deliverable would be reverted, so this is the
    other half of the bargain: `/derived` survives the turn, the run directory
    holds scratch, and both are the agent's to write."""
    backend = build_backend(cfg, session_dir)

    for command in (
        "echo kept > derived/report.md",
        "mkdir -p runs/t001 && echo scratch > runs/t001/notes.txt",
    ):
        result = backend.execute(command)
        assert result.exit_code == 0, f"{command!r} was refused: {result.output}"

    assert (session_dir / "derived" / "report.md").read_text().strip() == "kept"


@macos
def test_a_catalogue_deployed_outside_the_workspace_stays_readable(cfg, session_dir, tmp_path):
    """`KINGFISHER_SKILLS_DIR` exists so several deployments can share one
    reviewed catalogue, which means it commonly sits outside the workspace --
    and a shared directory lives in somebody's home as often as not.

    Denying the home without re-allowing it gave the agent a split view rather
    than a refusal: file tools are routed and reached the catalogue, the shell
    was denied, so reading a `SKILL.md` worked while running the script beside
    it did not.
    """
    catalogue = Path.home() / "kingfisher-catalogue-probe" / "skills"
    (catalogue / "demo").mkdir(parents=True, exist_ok=True)
    (catalogue / "demo" / "run.sh").write_text("echo from-the-catalogue\n")
    try:
        relocated = replace(cfg, skills_root=catalogue, skills_enabled=True)
        backend = build_backend(relocated, session_dir)

        result = backend.execute('sh "$KINGFISHER_SKILLS/demo/run.sh"')

        assert result.exit_code == 0, f"the shell cannot reach the catalogue: {result.output}"
        assert "from-the-catalogue" in str(result.output)
    finally:
        shutil.rmtree(Path.home() / "kingfisher-catalogue-probe", ignore_errors=True)
