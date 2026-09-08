"""What keeps `execute` off the rest of the host.

The behavioural tests here run a real shell through a real sandbox rather than
inspecting the profile text, because a profile that parses is not the same claim as a
file that cannot be read.
"""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from kingfisher.domain.ports import CommandResult
from kingfisher.infrastructure.harness.backend import build_backend
from kingfisher.infrastructure.sandbox import confinement
from kingfisher.infrastructure.sandbox.bubblewrap import BubblewrapRunner
from kingfisher.infrastructure.sandbox.fence import LandlockRunner
from kingfisher.infrastructure.workspace.layout import ensure_layout
from kingfisher.infrastructure.workspace.sessions import ensure_session_layout

macos = pytest.mark.skipif(
    platform.system() != "Darwin", reason="sandbox-exec is the macOS mechanism"
)

#: Two tests need the agent's shell to start *this* interpreter, with this project's
#: dependencies importable, from inside the sandbox. That works on a developer's machine
#: and does not on a GitHub macOS runner: `python3` there resolves to Xcode's shim
#: rather than the venv, which then cannot write its `xcrun` cache and cannot import
#: `yaml`.
needs_a_real_toolchain = pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason="the runner's python3 is Xcode's shim, not the project venv",
)


# -- choosing one ---------------------------------------------------------


def test_off_is_warned_about_on_every_start(cfg, tmp_path):
    """An exposure nobody is reminded of is one nobody fixes."""
    chosen = confinement.resolve(
        confinement.OFF, workspace=cfg.workspace
    )

    assert not chosen.confined
    assert "unconfined" in chosen.warning
    assert chosen.wrap("ls") == "ls", "off must not wrap"


def test_external_is_silent_because_the_runtime_already_did_it(cfg, tmp_path):
    """A container that mounts only the workspace has provided the boundary."""
    chosen = confinement.resolve(
        confinement.EXTERNAL, workspace=cfg.workspace
    )

    assert chosen.warning == ""
    assert chosen.wrap("ls") == "ls"


def test_an_unknown_mode_is_refused_rather_than_treated_as_off(cfg, tmp_path):
    """A typo in a deployment's env must not silently unconfine it."""
    with pytest.raises(ValueError, match="unknown shell sandbox mode"):
        confinement.resolve(
            "sandbox", workspace=cfg.workspace
        )


@macos
def test_auto_confines_and_says_nothing(cfg, tmp_path):
    chosen = confinement.resolve(
        confinement.AUTO, workspace=cfg.workspace
    )

    assert chosen.confined
    assert chosen.warning == ""
    assert confinement.profile_path(cfg.workspace).is_file(), "profile not written"


@macos
def test_the_profile_has_one_place_it_can_be(cfg):
    """`.kingfisher/shell.sb`, and no setting moves it.

    Two names ago this was `..._lives_in_harness_state_not_the_workspace`,
    docstringed *"a boundary the agent can edit is not a boundary"*, and read as
    proof the profile was out of the agent's reach. It never checked that: it
    relocated `state_dir`, whose default is inside the workspace. What makes it a
    boundary is `test_the_shell_cannot_rewrite_the_profile_it_runs_under`; what
    this checks is that there is one answer to where it lives.
    """
    confinement.resolve(confinement.AUTO, workspace=cfg.workspace)

    assert confinement.profile_path(cfg.workspace).is_file()
    assert [p.name for p in Path(cfg.workspace).rglob("shell.sb")] == ["shell.sb"]


# -- what the profile says ------------------------------------------------


def test_the_interpreter_stays_readable_or_python_stops_working(cfg, tmp_path):
    """A venv's `python3` is usually a symlink onto an interpreter installed under the
    home -- uv puts it in `~/.local/share/uv/`.
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
    """The measured hole: unconfined, this shell could read the deployment's own API
    keys, `~/.aws`, and the GitHub CLI's token.
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
    """`aexecute` is what the interpreter's code-side dispatch runs on, so the boundary
    must not depend on which entry point a caller happened to use.
    """
    secret = Path.home() / ".kingfisher-confinement-probe-async"
    secret.write_text("token", encoding="utf-8")
    try:
        backend = build_backend(cfg, session_dir)
        result = asyncio.run(backend.aexecute(f"cat {secret}"))

        assert "token" not in str(result.output), "aexecute read a file in the home"
    finally:
        secret.unlink(missing_ok=True)


class Recorder:
    """A runner that runs nothing, for tests about what reaches one."""

    #: This one is local, so the confinement should reach it.
    local = True

    def __init__(self, output: str = "ran", exit_code: int = 0) -> None:
        self.seen: list[tuple[str, int | None]] = []
        self.result = CommandResult(output=output, exit_code=exit_code)

    def run(self, command: str, *, timeout: int | None = None) -> CommandResult:
        self.seen.append((command, timeout))
        return self.result


class Elsewhere(Recorder):
    """A runner whose machine has none of this host's paths."""

    local = False


def test_a_runner_that_is_not_here_gets_the_command_unwrapped(cfg, session_dir):
    """A confinement is a command prefix naming paths on *this* host."""
    runner = Elsewhere()
    backend = build_backend(cfg, session_dir, runner=runner)
    backend.default.confinement = replace(
        backend.default.confinement, wrap=lambda c: f"fenced({c})"
    )

    backend.execute("echo hi")

    assert runner.seen == [("echo hi", None)], "the local prefix must not travel"


def test_a_runner_that_says_nothing_keeps_the_fence(cfg, session_dir):
    """The default is the safe one on purpose."""

    class SaysNothing:
        def __init__(self):
            self.seen = []

        def run(self, command, *, timeout=None):
            self.seen.append(command)
            return CommandResult(output="", exit_code=0)

    runner = SaysNothing()
    # `ty: ignore` is the test. `local` is required of anything type-checked
    # against the protocol, so an object without it is exactly the duck-typed
    # case the safe default exists for -- and the only way to reach that case is
    # to pass something the checker refuses.
    backend = build_backend(cfg, session_dir, runner=runner)  # ty: ignore[invalid-argument-type]
    backend.default.confinement = replace(
        backend.default.confinement, wrap=lambda c: f"fenced({c})"
    )

    backend.execute("echo hi")

    assert runner.seen == ["fenced(echo hi)"]


def test_a_runner_is_given_the_command_already_confined(cfg, session_dir):
    """Applying the confinement stays on this side of the seam."""
    runner = Recorder()
    backend = build_backend(cfg, session_dir, runner=runner)
    backend.default.confinement = replace(
        backend.default.confinement, wrap=lambda c: f"fenced({c})"
    )

    backend.execute("echo hi", timeout=7)

    assert runner.seen == [("fenced(echo hi)", 7)]


def test_what_a_runner_returns_reaches_the_model(cfg, session_dir):
    """The seam is only useful if the result travels."""
    backend = build_backend(cfg, session_dir, runner=Recorder(output="elsewhere", exit_code=3))

    result = backend.execute("whoami")

    assert result.output == "elsewhere"
    assert result.exit_code == 3


def test_the_only_runner_kingfisher_builds_for_itself_is_a_fence(cfg, session_dir):
    """`None` is not "do nothing" -- it is upstream's own execution, unchanged.

    A default runner would be 110 lines of upstream's truncation, timeout and
    exit-code handling copied into this repository to be kept in step. So the only
    runner built here is a fence, and where the platform has no fence there is no
    runner at all.
    """
    backend = build_backend(cfg, session_dir)

    runner = backend.default.runner
    assert runner is None or isinstance(runner, (LandlockRunner, BubblewrapRunner)), (
        f"{type(runner).__name__} is a runner this repository built for itself, "
        "which is the 110 lines of upstream's execution handling this avoids"
    )
    assert backend.execute("echo hi").output.strip() == "hi"


def test_the_async_path_reaches_a_runner_too(cfg, session_dir):
    """The same delegation the test below pins, seen from the other side: a deployment
    running commands elsewhere must not have an async path that quietly runs them
    here instead.
    """
    runner = Recorder()
    backend = build_backend(cfg, session_dir, runner=runner)

    asyncio.run(backend.aexecute("echo hi"))

    # Once, and confined -- on a host with a confinement the command the runner
    # sees is the wrapped one, which is the point of the test above.
    assert len(runner.seen) == 1
    assert "echo hi" in runner.seen[0][0]


def test_the_async_path_still_routes_through_execute(cfg, session_dir):
    """`ConfinedLocalShellBackend` overrides only `execute`, because upstream's
    `aexecute` is `asyncio.to_thread(self.execute, ...)`.
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
    """Confinement that broke the agent's own working directory would be swapped
    straight back out.
    """
    backend = build_backend(cfg, session_dir)
    backend.write("/derived/note.txt", "hello")

    result = backend.execute("cat derived/note.txt")

    assert result.exit_code == 0
    assert "hello" in str(result.output)


@macos
@needs_a_real_toolchain
def test_python_still_runs_with_its_dependencies(cfg, session_dir):
    """The whole point of the re-allowed roots."""
    backend = build_backend(cfg, session_dir)

    result = backend.execute('python3 -c "import yaml; print(\'deps ok\')"')

    assert result.exit_code == 0, f"python broke under confinement: {result.output}"
    assert "deps ok" in str(result.output)


@macos
def test_a_command_with_shell_metacharacters_still_runs_confined(cfg, session_dir):
    """The agent's command is quoted into the outer `sandbox-exec` invocation."""
    backend = build_backend(cfg, session_dir)

    piped = backend.execute("echo 'a b' | tr ' ' '-'")
    assert piped.exit_code == 0
    assert "a-b" in str(piped.output)

    escape = backend.execute(f"cat {Path.home()}/.zshrc 2>&1; echo done")
    assert "done" in str(escape.output)
    assert "not permitted" in str(escape.output).lower() or "No such file" in str(escape.output)


@macos
def test_off_really_does_leave_the_shell_open(cfg, session_dir):
    """The escape hatch has to actually be an escape hatch -- otherwise a deployment
    that hits a false positive has no way past it.
    """
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
    """`system.md` says to stop and report rather than reach outside the workspace."""
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
    """The prompt says to write scratch under `$TMPDIR`, never a literal `/tmp`."""
    target = Path("/tmp/kingfisher-write-probe")
    backend = build_backend(cfg, session_dir)
    try:
        backend.execute(f"echo pwned > {target}")

        assert not target.exists(), "the shell wrote to a literal /tmp"
    finally:
        target.unlink(missing_ok=True)


@macos
def test_installing_into_the_environment_is_refused(cfg, session_dir):
    """Two `pip install` attempts in one observed run."""
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
def test_tmpdir_is_writable_inside_the_session(cfg, session_dir):
    """`$TMPDIR` is where the prompt sends scratch, so a shell that cannot write it
    fails on its first command.

    It used to be relocatable out of the workspace and named separately in the
    writable set for that reason. Inside the session, it is covered by the
    workspace allow the profile already emits.
    """
    backend = build_backend(cfg, session_dir)

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
    """Confinement that broke the deliverable would be reverted, so this is the other
    half of the bargain: `/derived` survives the turn, the run directory holds
    scratch, and both are the agent's to write.
    """
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
    """`KINGFISHER_SKILLS_DIR` exists so several deployments can share one reviewed
    catalogue, which means it commonly sits outside the workspace -- and a shared
    directory lives in somebody's home as often as not.
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


# -- getting *to* the workspace, not just reading it ----------------------


@pytest.fixture
def workspace_in_the_home():
    """A workspace where a workspace normally is: inside the operator's home.

    Every other test here builds one under `tmp_path`, which on macOS sits outside the
    one directory this profile denies -- which is why nothing caught the bug below.
    """
    root = Path(tempfile.mkdtemp(prefix="kingfisher-home-probe-", dir=Path.home()))
    try:
        yield ensure_layout(root / "ws")
    finally:
        shutil.rmtree(root, ignore_errors=True)


@macos
def test_the_shell_can_walk_into_a_workspace_that_lives_in_the_home(cfg, workspace_in_the_home):
    """Denying the home as a subpath denies the way *in* to the workspace too."""
    session = ensure_session_layout(workspace_in_the_home / "sessions" / "s")
    (session / "runs" / "t001").mkdir(parents=True)
    backend = build_backend(replace(cfg, workspace=workspace_in_the_home), session)

    result = backend.execute("cd runs/t001 && pwd")

    assert result.exit_code == 0, f"the shell cannot walk into the workspace: {result.output}"
    assert str(session / "runs" / "t001") in str(result.output)


@macos
def test_walking_in_does_not_open_the_home_it_walks_through(cfg, workspace_in_the_home):
    """The way in is metadata only: the directories on it can be `stat`ed and nothing
    more.
    """
    session = ensure_session_layout(workspace_in_the_home / "sessions" / "s")
    secret = Path.home() / ".kingfisher-traversal-probe"
    secret.write_text("token", encoding="utf-8")
    backend = build_backend(replace(cfg, workspace=workspace_in_the_home), session)
    try:
        read = backend.execute(f"cat {secret}")
        listing = backend.execute(f"ls {Path.home()}")

        assert "token" not in str(read.output), "the shell read a file in the home"
        assert "not permitted" in str(read.output).lower()
        assert "not permitted" in str(listing.output).lower(), "the home is listable"
    finally:
        secret.unlink(missing_ok=True)


def test_external_is_confined_elsewhere_rather_than_unconfined(cfg, tmp_path):
    """The distinction `EXTERNAL` exists for, made readable downstream."""
    chosen = confinement.resolve(
        confinement.EXTERNAL, workspace=cfg.workspace
    )

    assert chosen.elsewhere
    assert not chosen.confined, "nothing is wrapped -- that is the point of it"
    assert chosen.warning == "", "the deployment asserted the boundary; there is nothing to warn"
    assert chosen.wrap("ls") == "ls"


def test_nothing_configured_is_not_confined_elsewhere(cfg, tmp_path):
    """The other side, or the flag would say yes to everything."""
    chosen = confinement.resolve(
        confinement.OFF, workspace=cfg.workspace
    )

    assert not chosen.elsewhere


# -- what runs a command, said once ------------------------------------------


def test_a_supplied_runner_that_is_not_here_stops_the_confinement_claiming(cfg, session_dir):
    """Measured before this existed: a runner declaring `local = False` ran the command
    with no wrap applied, and the `Confinement` still reported
    `mechanism='sandbox-exec'` and `confined=True`.
    """
    backend = build_backend(cfg, session_dir, runner=Elsewhere())
    confined = backend.default.confinement

    assert not confined.confined, "nothing this process applies reaches the command"
    assert confined.mechanism == ""
    assert confined.elsewhere, "which is exactly what EXTERNAL means"
    assert confined.supplied


def test_a_supplied_runner_that_is_here_keeps_the_mechanism_and_adds_itself(cfg, session_dir):
    """The other case, and it is not the same fact."""
    backend = build_backend(cfg, session_dir, runner=Recorder())
    confined = backend.default.confinement

    assert confined.supplied
    assert confined.mechanism == confinement.shell_confinement(cfg).mechanism


def test_no_supplied_runner_says_nothing_new(cfg, session_dir):
    """The case every existing deployment is in."""
    assert not build_backend(cfg, session_dir).default.confinement.supplied


# -- the definition roots are not the agent's to edit ---------------------


@macos
@pytest.mark.parametrize("kind", ["agents", "skills", "subagents", "tools"])
def test_the_shell_cannot_write_into_a_definition_root(cfg, session_dir, kind):
    """A definition the agent can rewrite says whatever the agent likes."""
    root = cfg.catalogue_roots[kind]
    # Created first, or the write fails for want of a directory and the test
    # passes without the profile doing anything. `agents/` is not in the
    # fixture's layout, and that vacuity was real: dropping the protection
    # entirely left this parametrisation green for `agents`.
    root.mkdir(parents=True, exist_ok=True)
    backend = build_backend(cfg, session_dir)
    target = root / "written-by-the-agent"

    backend.execute(f'printf x > "{target}"')

    assert not target.exists(), (
        f"the shell wrote into {kind}/, so a definition is still the agent's to edit"
    )


@macos
@pytest.mark.parametrize("kind", ["agents", "skills", "subagents", "tools"])
def test_a_definition_root_stays_readable(cfg, session_dir, kind):
    """Denied writes, not denied access."""
    root = cfg.catalogue_roots[kind]
    root.mkdir(parents=True, exist_ok=True)
    (root / "readable.txt").write_text("from the catalogue\n", encoding="utf-8")
    backend = build_backend(cfg, session_dir)

    result = backend.execute(f'cat "{root / "readable.txt"}"')

    assert result.exit_code == 0, f"the shell cannot read {kind}/: {result.output}"
    assert "from the catalogue" in str(result.output)


@macos
def test_the_rest_of_the_workspace_is_still_writable(cfg, session_dir):
    """The bound on the rule."""
    backend = build_backend(cfg, session_dir)
    target = cfg.workspace / "ordinary-work.txt"

    backend.execute(f'printf x > "{target}"')

    assert target.exists(), "the workspace itself stopped being writable"


def test_every_definition_root_is_protected(cfg):
    """The property, not the four names, so a fifth kind arrives covered."""
    protected = confinement.protected_roots(
        cfg.workspace, cfg.skills_dir, tuple(cfg.catalogue_roots.values())
    )

    missing = [
        kind
        for kind, root in cfg.catalogue_roots.items()
        if root.resolve() not in protected
    ]

    assert not missing, (
        f"{missing} are definition roots the profile would leave writable; every "
        f"kind in `catalogue_roots` belongs in `protected`"
    )


def test_a_root_named_twice_is_protected_once(cfg):
    """`skills` is passed separately *and* is a catalogue root."""
    roots = tuple(cfg.catalogue_roots.values())

    protected = confinement.protected_roots(cfg.workspace, cfg.catalogue_roots["skills"], roots)

    assert len(protected) == len(set(protected))
    assert len(protected) == len(roots) + 1, "a duplicate survived, or a root was dropped"


def test_a_definition_root_that_does_not_exist_is_still_named(cfg, tmp_path):
    """A profile is written once, and `seed` runs after it at least once."""
    absent = tmp_path / "not-created"

    protected = confinement.protected_roots(tmp_path / "ws", None, (absent,))

    assert absent.resolve() in protected


# -- the profile is not the agent's to edit either ------------------------


@macos
@pytest.mark.parametrize(
    "how",
    [
        'printf x > "{profile}"',
        'printf x >> "{profile}"',
        'rm -f "{profile}"',
        'printf x > "{beside}"; mv "{beside}" "{profile}"',
    ],
    ids=["overwrite", "append", "unlink", "rename-over"],
)
def test_the_shell_cannot_rewrite_the_profile_it_runs_under(cfg, session_dir, how):
    """Two commands took the sandbox apart: write the rules, then run under them.

    `sandbox-exec -f` re-reads the profile for every command and `resolve` rewrites
    it only per backend, so a shell that could write it was unconfined for the rest
    of the turn. It could: the profile sits in the workspace and
    `writable_roots` returns the whole workspace, so the rules sat in the region
    they declared writable. Measured before the fix -- the home became readable and
    `skills/` became writable, both of which the profile above had just refused.

    Four ways rather than one, because refusing an overwrite while allowing
    `mv` over the same name is not a boundary.
    """
    profile = confinement.profile_path(cfg.workspace)
    backend = build_backend(cfg, session_dir)
    before = profile.read_text(encoding="utf-8")

    backend.execute(how.format(profile=profile, beside=cfg.workspace / "beside.sb"))

    assert profile.read_text(encoding="utf-8") == before, (
        "the shell rewrote the profile that confines it, so the next command in "
        "this turn would run under rules the agent chose"
    )


@macos
def test_the_home_stays_denied_after_a_shell_tries_to_open_it(cfg, session_dir):
    """The consequence, not the mechanism: what the escape was *for*."""
    secret = Path.home() / ".kingfisher-profile-probe"
    secret.write_text("PRIVATE", encoding="utf-8")
    backend = build_backend(cfg, session_dir)
    try:
        profile = confinement.profile_path(cfg.workspace)
        backend.execute(f'printf "(version 1)\\n(allow default)\\n" > "{profile}"')

        result = backend.execute(f'cat "{secret}"')
    finally:
        secret.unlink(missing_ok=True)

    assert "PRIVATE" not in str(result.output), "the shell rewrote its way into the home"


def test_the_profile_refuses_itself_last(tmp_path):
    """`sandbox-exec` takes the last matching rule, and this one has to beat the
    allow that covers the directory it sits in."""
    written = tmp_path / "ws" / ".kingfisher" / "shell.sb"

    lines = confinement.profile(
        home=tmp_path / "home",
        workspace=tmp_path / "ws",
        readable=(tmp_path / "ws",),
        writable=(tmp_path / "ws",),
        itself=written,
        protected=(tmp_path / "ws" / "skills",),
    ).splitlines()

    # Last but one: the harness denial that follows it is the same kind of rule
    # and covers a different path, so both have to sit after the allows.
    assert f'(deny file-write* (path "{written}"))' in lines[-2:]
    assert all(not line.startswith("(allow file-write*") for line in lines[-2:])


@macos
def test_a_profile_is_replaced_rather_than_truncated(tmp_path):
    """A turn reading the profile while another rewrites it must not see half of one.

    Every input to `profile` comes from one `Config`, so concurrent writers write the
    same bytes; what is being prevented is the window in which the file is empty.
    """
    workspace = tmp_path / "ws"
    for _ in range(2):
        confinement.resolve(confinement.AUTO, workspace=workspace)

    beside = confinement.profile_path(workspace).parent
    assert sorted(p.name for p in beside.iterdir()) == ["shell.sb"], (
        "a temporary profile was left beside the real one"
    )
