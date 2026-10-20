"""What keeps `execute` off the rest of the host.

The behavioural tests here run a real shell through a real sandbox rather than
inspecting the profile text, because a profile that parses is not the same claim as a
file that cannot be read.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import os
import re
import shutil
import tempfile
import warnings
from concurrent import futures
from dataclasses import replace
from pathlib import Path
from typing import TypedDict

import pytest

from kingfisher.core.config import ConfigError
from kingfisher.core.interfaces import CommandResult
from kingfisher.core.layout import DEFAULT_LAYOUT, HARNESS, INPUTS_ROUTE, Layout
from kingfisher.core.workspace import ensure_layout
from kingfisher.plugins.sessions.backend import LocalSessions, backend_at
from kingfisher.plugins.sessions.folders import ensure_session_layout
from kingfisher.plugins.sessions.sandbox import confinement, sandbox_exec
from kingfisher.plugins.sessions.sandbox.bubblewrap import BubblewrapRunner
from kingfisher.plugins.sessions.sandbox.fence import LandlockRunner
from tests.conftest import macos, repository_root

#: Two tests need the agent's shell to start *this* interpreter, with this project's
#: dependencies importable, from inside the sandbox. That works on a developer's machine
#: and does not on a GitHub macOS runner: `python3` there resolves to Xcode's shim
#: rather than the venv, which then cannot write its `xcrun` cache and cannot import
#: `yaml`.
needs_a_real_toolchain = pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason="the runner's python3 is Xcode's shim, not the project venv",
)

DEFAULT_SHELL = confinement.ShellSettings()
UNFENCED = confinement.ShellSettings(sandbox=confinement.OFF)



class _Places(TypedDict):
    workspace: Path
    sessions_root: Path
    kingfisher_dir: Path


def _places(cfg) -> _Places:
    """The three places `resolve` is told, as `shell_confinement` tells it them."""
    return _Places(
        workspace=cfg.workspace,
        sessions_root=cfg.sessions_root,
        kingfisher_dir=cfg.kingfisher_dir,
    )


# -- choosing one ---------------------------------------------------------


def test_off_is_warned_about_on_every_start(cfg, tmp_path):
    """An exposure nobody is reminded of is one nobody fixes."""
    chosen = confinement.resolve(
        confinement.OFF, **_places(cfg), layout=DEFAULT_LAYOUT
    )

    assert not chosen.confined
    assert "unconfined" in chosen.warning
    assert chosen.wrap("ls") == "ls", "off must not wrap"


def test_external_is_silent_because_the_runtime_already_did_it(cfg, tmp_path):
    """A container that mounts only the workspace has provided the boundary."""
    chosen = confinement.resolve(
        confinement.EXTERNAL, **_places(cfg), layout=DEFAULT_LAYOUT
    )

    assert chosen.warning == ""
    assert chosen.wrap("ls") == "ls"


def test_an_unknown_mode_is_refused_rather_than_treated_as_off(cfg, tmp_path):
    """A typo in a deployment's env must not silently unconfine it."""
    with pytest.raises(ValueError, match="unknown shell sandbox mode"):
        confinement.resolve(
            "sandbox", **_places(cfg), layout=DEFAULT_LAYOUT
        )


def test_a_blank_shell_sandbox_reads_as_the_default():
    """An emptied `KINGFISHER_SHELL_SANDBOX=` line was mode `''`, a traceback on the first
    turn, where every other setting reads blank as unset -- and unset here is `auto`."""
    read = confinement.ShellSettings.from_env({"KINGFISHER_SHELL_SANDBOX": " "})

    assert read.sandbox == confinement.AUTO


@macos
def test_auto_confines_and_says_nothing(cfg, tmp_path):
    chosen = confinement.resolve(
        confinement.AUTO, **_places(cfg), layout=DEFAULT_LAYOUT
    )

    assert chosen.confined
    assert chosen.warning == ""
    assert chosen.profile is not None
    assert chosen.profile.is_file(), "profile not written"


@macos
def test_the_profile_has_one_place_it_can_be(cfg):
    """In the harness's folder, named for its rules, and no setting moves it -- so the
    same rules are one file, however many turns write them.

    Two names ago this was `..._lives_in_harness_state_not_the_workspace`,
    docstringed *"a boundary the agent can edit is not a boundary"*, and read as
    proof the profile was out of the agent's reach. It never checked that: it
    relocated `state_dir`, whose default is inside the workspace. What makes it a
    boundary is `test_the_shell_cannot_rewrite_the_profile_it_runs_under`; what
    this checks is that there is one answer to where it lives.
    """
    first = confinement.resolve(
        confinement.AUTO, **_places(cfg), layout=DEFAULT_LAYOUT
    )
    again = confinement.resolve(
        confinement.AUTO, **_places(cfg), layout=DEFAULT_LAYOUT
    )

    assert first.profile == again.profile
    assert first.profile is not None
    assert first.profile.parent == cfg.workspace / ".kingfisher"
    assert re.fullmatch(r"shell\.[0-9a-f]{16}\.sb", first.profile.name), first.profile.name
    assert list(Path(cfg.workspace).rglob("shell.*.sb")) == [first.profile]


@macos
def test_processes_configured_apart_keep_their_profiles_apart(cfg):
    """Catches two processes sharing a workspace -- several each serving one session --
    writing their rules to one file, which left every turn bound by whichever process
    wrote last, under rules its own configuration never asked for.
    """
    one = confinement.resolve(
        confinement.AUTO, **_places(cfg), extra=("/opt/one",), layout=DEFAULT_LAYOUT
    )
    other = confinement.resolve(
        confinement.AUTO, **_places(cfg), extra=("/opt/other",), layout=DEFAULT_LAYOUT
    )

    assert one.profile is not None
    assert other.profile is not None
    assert one.profile != other.profile
    assert "/opt/one" in one.profile.read_text(encoding="utf-8")
    assert "/opt/one" not in other.profile.read_text(encoding="utf-8")


# -- what the profile says ------------------------------------------------


def test_the_interpreter_stays_readable_or_python_stops_working(cfg, tmp_path):
    """A venv's `python3` is usually a symlink onto an interpreter installed under the
    home -- uv puts it in `~/.local/share/uv/`.
    """
    import sys

    roots = confinement.readable_roots(cfg.workspace, cfg.sessions_root)

    assert Path(sys.base_prefix).resolve() in roots
    assert Path(cfg.workspace).resolve() in roots


def test_the_sessions_root_is_named_twice_only_where_it_is_somewhere_else(cfg, tmp_path):
    """Inside the workspace it is a subpath of the root already returned, and naming it
    again would read as though it added something. Reached through a link it is not
    under the workspace at all, and then it is the whole point.
    """
    inside = confinement.writable_roots(cfg.workspace, cfg.sessions_root)

    far = tmp_path / "far"
    far.mkdir()
    (cfg.workspace / "sessions").rmdir()
    (cfg.workspace / "sessions").symlink_to(far)
    elsewhere = confinement.writable_roots(cfg.workspace, cfg.sessions_root)

    assert inside == (Path(cfg.workspace).resolve(),)
    assert elsewhere == (Path(cfg.workspace).resolve(), far.resolve())
    assert far.resolve() in confinement.readable_roots(cfg.workspace, cfg.sessions_root)


def test_paths_a_deployment_put_on_path_stay_readable(cfg, tmp_path):
    """`ShellSettings.path_extra` is a deployment saying "the agent may run these"."""
    roots = confinement.readable_roots(cfg.workspace, cfg.sessions_root, ("/opt/homebrew/bin",))

    assert Path("/opt/homebrew/bin") in roots


@pytest.mark.parametrize("entry", ["", ".", "bin"])
def test_a_path_entry_that_is_not_absolute_is_refused(entry):
    """`path_extra=("",)` resolved to the server's working directory, which every fence
    then granted the shell to read: `str(Path(""))` is `"."`, so no filter caught it."""
    with pytest.raises(ConfigError, match="KINGFISHER_SHELL_PATH_EXTRA"):
        confinement.ShellSettings(path_extra=(entry,))


def test_an_absolute_path_entry_is_still_taken():
    """The control: a guard refusing every entry would pass the test above."""
    assert confinement.ShellSettings(path_extra=("/opt/tools/bin",)).path_extra == (
        "/opt/tools/bin",
    )


def test_the_empty_parts_of_the_variable_are_dropped_rather_than_refused():
    """`PATH`-style joining leaves stray colons, and refusing one would stop a start the
    variable meant nothing by."""
    read = confinement.ShellSettings.from_env({"KINGFISHER_SHELL_PATH_EXTRA": ":/opt/x::"})

    assert read.path_extra == ("/opt/x",)


def test_a_relative_part_of_the_variable_refuses_the_start():
    """Through `from_env` too, so a relative entry in `.env` fails as the sessions are built."""
    with pytest.raises(ConfigError, match="'bin'"):
        confinement.ShellSettings.from_env({"KINGFISHER_SHELL_PATH_EXTRA": "/opt/x:bin"})


# -- the part that actually has to hold -----------------------------------


@macos
def test_the_shell_cannot_read_a_secret_outside_the_workspace(cfg, session_dir, tmp_path):
    """The measured hole: unconfined, this shell could read the deployment's own API
    keys, `~/.aws`, and the GitHub CLI's token.
    """
    secret = Path.home() / ".kingfisher-confinement-probe"
    secret.write_text("token", encoding="utf-8")
    try:
        backend = backend_at(cfg, session_dir)
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
        backend = backend_at(cfg, session_dir)
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
    backend = backend_at(cfg, session_dir, runner=runner)
    backend.default.confinement = replace(
        backend.default.confinement, wrap=lambda c: f"fenced({c})"
    )

    backend.execute("echo hi")

    assert runner.seen == [("echo hi", None)], "the local prefix must not travel"


def test_a_runner_that_says_nothing_keeps_the_fence(cfg, session_dir):
    """The default is the safe one on purpose.

    This half only: that `execute` wraps the command when the runner reads as local.
    It replaces the confinement below, so it cannot see what `LocalSessions` decided
    about that runner -- which is the other half, and was held by nothing until
    `test_a_runner_that_says_nothing_is_confined_here` was written.
    """

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
    backend = backend_at(cfg, session_dir, runner=runner)  # ty: ignore[invalid-argument-type]
    backend.default.confinement = replace(
        backend.default.confinement, wrap=lambda c: f"fenced({c})"
    )

    backend.execute("echo hi")

    assert runner.seen == ["fenced(echo hi)"]


def test_a_runner_that_says_nothing_is_confined_here(cfg, session_dir):
    """What the build decides about a runner that declares nothing, rather than what
    `execute` then does with it.

    Measured before this existed: flipping that default to `False` left all 2,109 tests
    passing. A supplied local runner saying nothing would then be handed
    `Confinement(wrap=_unwrapped, elsewhere=True)` -- so `execute` still takes the wrap
    branch and wraps with the identity, the shell runs with no sandbox, and the record
    beside it says the boundary is somebody else's.

    Asserted on `elsewhere` rather than on the wrapped text, because the text is the
    platform's: Landlock's wrap is the identity by design, so a command that comes back
    unchanged is correct on Linux and a failure on macOS.
    """

    class SaysNothing:
        def run(self, command, *, timeout=None):
            return CommandResult(output="", exit_code=0)

    # `ty: ignore` for the reason the sibling above gives: the duck-typed runner is
    # the whole case, and the checker refuses one without `local`.
    backend = backend_at(cfg, session_dir, runner=SaysNothing())  # ty: ignore[invalid-argument-type]
    confined = backend.default.confinement

    assert confined.elsewhere is False, "a runner that said nothing was read as remote"
    assert confined.supplied is True, "and the record should still say a runner was given"


def test_a_runner_that_says_it_is_elsewhere_is_taken_at_its_word(cfg, session_dir):
    """The control the rule above needs: `elsewhere` is not simply always false."""

    class SaysRemote:
        local = False

        def run(self, command, *, timeout=None):
            return CommandResult(output="", exit_code=0)

    confined = backend_at(cfg, session_dir, runner=SaysRemote()).default.confinement

    assert confined.elsewhere is True
    assert confined.supplied is True


def test_a_runner_is_given_the_command_already_confined(cfg, session_dir):
    """Applying the confinement stays on this side of the seam."""
    runner = Recorder()
    backend = backend_at(cfg, session_dir, runner=runner)
    backend.default.confinement = replace(
        backend.default.confinement, wrap=lambda c: f"fenced({c})"
    )

    backend.execute("echo hi", timeout=7)

    assert runner.seen == [("fenced(echo hi)", 7)]


def test_what_a_runner_returns_reaches_the_model(cfg, session_dir):
    """The seam is only useful if the result travels."""
    backend = backend_at(cfg, session_dir, runner=Recorder(output="elsewhere", exit_code=3))

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
    backend = backend_at(cfg, session_dir)

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
    backend = backend_at(cfg, session_dir, runner=runner)

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
    backend = backend_at(cfg, session_dir)
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
    backend = backend_at(cfg, session_dir)
    backend.write("/outputs/note.txt", "hello")

    result = backend.execute("cat outputs/note.txt")

    assert result.exit_code == 0
    assert "hello" in str(result.output)


@macos
@needs_a_real_toolchain
def test_python_still_runs_with_its_dependencies(cfg, session_dir):
    """The whole point of the re-allowed roots."""
    backend = backend_at(cfg, session_dir)

    result = backend.execute('python3 -c "import yaml; print(\'deps ok\')"')

    assert result.exit_code == 0, f"python broke under confinement: {result.output}"
    assert "deps ok" in str(result.output)


@macos
def test_a_command_with_shell_metacharacters_still_runs_confined(cfg, session_dir):
    """The agent's command is quoted into the outer `sandbox-exec` invocation."""
    backend = backend_at(cfg, session_dir)

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
        backend = backend_at(cfg, session_dir, shell=UNFENCED)

        assert "token" in str(backend.execute(f"cat {secret}").output)
    finally:
        secret.unlink(missing_ok=True)


# -- writes: the rules system.md only asked for ---------------------------


@macos
def test_the_shell_cannot_write_outside_the_workspace(cfg, session_dir):
    """`system.md` says to stop and report rather than reach outside the workspace."""
    target = Path.home() / ".kingfisher-write-probe"
    backend = backend_at(cfg, session_dir)
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
    backend = backend_at(cfg, session_dir)
    try:
        backend.execute(f"echo pwned > {target}")

        assert not target.exists(), "the shell wrote to a literal /tmp"
    finally:
        target.unlink(missing_ok=True)


@macos
def test_installing_into_the_environment_is_refused(cfg, session_dir):
    """Two `pip install` attempts in one observed run."""
    import sys

    backend = backend_at(cfg, session_dir)
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
    backend = backend_at(cfg, session_dir)

    result = backend.execute('echo hi > "$TMPDIR/note.txt" && cat "$TMPDIR/note.txt"')

    assert result.exit_code == 0, f"$TMPDIR is not writable: {result.output}"
    assert "hi" in str(result.output)


@macos
def test_redirecting_to_dev_null_still_works(cfg, session_dir):
    """It appears in about half the commands an agent writes."""
    backend = backend_at(cfg, session_dir)

    result = backend.execute("echo noise 2>/dev/null && echo ok")

    assert result.exit_code == 0
    assert "ok" in str(result.output)


@macos
def test_the_agent_can_still_write_everything_it_is_meant_to(cfg, session_dir):
    """Confinement that broke the deliverable would be reverted, so this is the other
    half of the bargain: `/outputs` survives the turn, the working-files directory
    holds scratch, and both are the agent's to write.
    """
    backend = backend_at(cfg, session_dir)

    for command in (
        "echo kept > outputs/report.md",
        "echo scratch > scratchpad/notes.txt",
    ):
        result = backend.execute(command)
        assert result.exit_code == 0, f"{command!r} was refused: {result.output}"

    assert (session_dir / "outputs" / "report.md").read_text().strip() == "kept"


@macos
def test_a_catalogue_deployed_outside_the_workspace_stays_readable(cfg, session_dir):
    """`KINGFISHER_DEFINITIONS_DIR` is a folder of its own so several deployments can
    share one reviewed catalogue -- and a shared directory lives in somebody's home as
    often as not, which is the one directory the profile denies.
    """
    catalogue = Path.home() / "kingfisher-catalogue-probe" / "skills"
    (catalogue / "demo").mkdir(parents=True, exist_ok=True)
    (catalogue / "demo" / "run.sh").write_text("echo from-the-catalogue\n")
    try:
        shared = replace(cfg, definitions=catalogue.parent, skills_enabled=True)
        backend = backend_at(shared, session_dir)

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
    session = ensure_session_layout(workspace_in_the_home / "sessions" / "s", DEFAULT_LAYOUT)
    (session / "scratchpad" / "t001").mkdir(parents=True)
    backend = backend_at(replace(cfg, workspace=workspace_in_the_home), session)

    result = backend.execute("cd scratchpad && pwd")

    assert result.exit_code == 0, f"the shell cannot walk into the workspace: {result.output}"
    assert str(session / "scratchpad") in str(result.output)


@macos
def test_walking_in_does_not_open_the_home_it_walks_through(cfg, workspace_in_the_home):
    """The way in is metadata only: the directories on it can be `stat`ed and nothing
    more.
    """
    session = ensure_session_layout(workspace_in_the_home / "sessions" / "s", DEFAULT_LAYOUT)
    secret = Path.home() / ".kingfisher-traversal-probe"
    secret.write_text("token", encoding="utf-8")
    backend = backend_at(replace(cfg, workspace=workspace_in_the_home), session)
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
        confinement.EXTERNAL, **_places(cfg), layout=DEFAULT_LAYOUT
    )

    assert chosen.elsewhere
    assert not chosen.confined, "nothing is wrapped -- that is the point of it"
    assert chosen.warning == "", "the deployment asserted the boundary; there is nothing to warn"
    assert chosen.wrap("ls") == "ls"


def test_nothing_configured_is_not_confined_elsewhere(cfg, tmp_path):
    """The other side, or the flag would say yes to everything."""
    chosen = confinement.resolve(
        confinement.OFF, **_places(cfg), layout=DEFAULT_LAYOUT
    )

    assert not chosen.elsewhere


# -- what is doing the confining, by name -------------------------------------

#: Why a mechanism is not one `fence_for` builds a runner for. A mechanism that is
#: in neither this table nor `LINUX_FENCES` is one `fence_for` answers `None` for:
#: the shell then runs unfenced while the `Confinement` beside it says confined.
NOT_A_LINUX_FENCE = {
    "sandbox-exec": "confines by wrapping the command string, not the process",
}


def _mechanisms_in(module) -> list[ast.keyword]:
    """Every `mechanism=` this module hands a `Confinement`, as written."""
    source = ast.parse(Path(inspect.getfile(module)).read_text(encoding="utf-8"))
    return [
        word
        for node in ast.walk(source)
        if isinstance(node, ast.Call)
        for word in node.keywords
        if word.arg == "mechanism"
    ]


def test_every_mechanism_is_named_and_accounted_for():
    """The vocabulary is closed, and a fourth mechanism has to say which half it is in.

    `fence_for` builds a runner for the names in `LINUX_FENCES` and nothing else, so
    one added here and not there is a shell running unfenced while `doctor` reports a
    fence. Nothing connected the two until they were named -- three files outside
    `confinement` branched on the spellings.
    """
    written = _mechanisms_in(confinement)

    assert written, "nothing was parsed, so this asserts nothing"
    literal = [word for word in written if not isinstance(word.value, ast.Name)]
    assert not literal, (
        "a mechanism is handed over by name, never as a literal -- a spelling written "
        "out here is one `fence_for` and `doctor` cannot be renamed along with"
    )
    produced = {getattr(confinement, word.value.id) for word in written}

    assert produced == set(confinement.LINUX_FENCES) | set(NOT_A_LINUX_FENCE)
    assert not set(confinement.LINUX_FENCES) & set(NOT_A_LINUX_FENCE)


def test_a_mode_and_a_mechanism_are_different_vocabularies():
    """They overlap on one word, which is why this says so: `bubblewrap` is a mode a
    deployment asks for *and* the mechanism that answers, and `auto` -- a mode -- is
    never a mechanism.
    """
    assert set(confinement.MODES) & set(confinement.LINUX_FENCES) == {confinement.BUBBLEWRAP}
    assert confinement.LANDLOCK not in confinement.MODES


# -- what runs a command, said once ------------------------------------------


def test_a_supplied_runner_that_is_not_here_stops_the_confinement_claiming(cfg, session_dir):
    """Measured before this existed: a runner declaring `local = False` ran the command
    with no wrap applied, and the `Confinement` still reported
    `mechanism='sandbox-exec'` and `confined=True`.
    """
    backend = backend_at(cfg, session_dir, runner=Elsewhere())
    confined = backend.default.confinement

    assert not confined.confined, "nothing this process applies reaches the command"
    assert confined.mechanism == ""
    assert confined.elsewhere, "which is exactly what EXTERNAL means"
    assert confined.supplied


def test_a_supplied_runner_that_is_here_keeps_the_mechanism_and_adds_itself(cfg, session_dir):
    """The other case, and it is not the same fact."""
    backend = backend_at(cfg, session_dir, runner=Recorder())
    confined = backend.default.confinement

    assert confined.supplied
    assert confined.mechanism == confinement.shell_confinement(cfg, DEFAULT_SHELL).mechanism


def test_no_supplied_runner_says_nothing_new(cfg, session_dir):
    """The case every existing deployment is in."""
    assert not backend_at(cfg, session_dir).default.confinement.supplied


# -- saying so --------------------------------------------------------------


def test_an_unconfined_shell_is_warned_about_when_its_backend_is_built(cfg, session_dir):
    """`off` was documented as warned about on every start while only `doctor` and the
    test driver ever said it -- `kingfisher run` started unconfined and silent, and
    `test_off_is_warned_about_on_every_start` stayed green because it checks the text
    exists, not that anything says it.
    """
    with pytest.warns(UserWarning, match="unconfined"):
        backend_at(cfg, session_dir, shell=UNFENCED)


def test_the_warning_is_said_once_however_many_places_build_a_backend(cfg, session_dir):
    """A server builds a backend every turn, and a warning per turn is a log nobody reads.

    Two call sites rather than a loop: a loop is one location, and would stay green
    with the warning attributed to the caller, which repeats it for every site.
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("default")
        backend_at(cfg, session_dir, shell=UNFENCED)
        backend_at(cfg, session_dir, shell=UNFENCED)

    assert len([w for w in caught if "unconfined" in str(w.message)]) == 1


@pytest.mark.parametrize(
    ("mode", "runner"),
    [(confinement.EXTERNAL, None), (confinement.OFF, Elsewhere())],
    ids=["external", "a runner that is not here"],
)
def test_a_shell_confined_elsewhere_is_not_warned_about(cfg, session_dir, mode, runner):
    """The control beside the warning: a deployment told its host is exposed when
    nothing runs on it learns to ignore the one line that matters.
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        backend_at(cfg, session_dir, runner=runner, shell=confinement.ShellSettings(sandbox=mode))

    assert not [w for w in caught if "unconfined" in str(w.message)]


# -- the definition roots are not the agent's to edit ---------------------


@macos
def test_the_shell_cannot_write_into_a_definition_root(cfg, session_dir):
    """A definition the agent can rewrite says whatever the agent likes."""
    for kind in ["agents", "skills", "subagents", "tools"]:
        root = cfg.catalogue_roots[kind]
        # Created first, or the write fails for want of a directory and the test
        # passes without the profile doing anything. That vacuity was real: with
        # `agents/` missing, dropping the protection entirely left it green.
        root.mkdir(parents=True, exist_ok=True)
        backend = backend_at(cfg, session_dir)
        target = root / "written-by-the-agent"

        backend.execute(f'printf x > "{target}"')

        assert not target.exists(), (
            f"the shell wrote into {kind}/, so a definition is still the agent's to edit"
        )


@macos
def test_a_definition_root_stays_readable(cfg, session_dir):
    """Denied writes, not denied access."""
    for kind in ["agents", "skills", "subagents", "tools"]:
        root = cfg.catalogue_roots[kind]
        root.mkdir(parents=True, exist_ok=True)
        (root / "readable.txt").write_text("from the catalogue\n", encoding="utf-8")
        backend = backend_at(cfg, session_dir)

        result = backend.execute(f'cat "{root / "readable.txt"}"')

        assert result.exit_code == 0, f"the shell cannot read {kind}/: {result.output}"
        assert "from the catalogue" in str(result.output)


@macos
def test_the_rest_of_the_workspace_is_still_writable(cfg, session_dir):
    """The bound on the rule."""
    backend = backend_at(cfg, session_dir)
    target = cfg.workspace / "ordinary-work.txt"

    backend.execute(f'printf x > "{target}"')

    assert target.exists(), "the workspace itself stopped being writable"


def test_every_definition_root_is_protected(cfg):
    """The property, not the names, so a new kind arrives covered.

    `DEFINITION_KINDS` is derived from the fields of `Definitions`, so adding a kind
    adds a directory to `catalogue_roots` without anyone editing a list -- which the
    parametrised tests above would not notice.
    """
    protected = confinement.protected_roots(
        cfg.kingfisher_dir, (cfg.skills_dir,), tuple(cfg.catalogue_roots.values())
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

    protected = confinement.protected_roots(
        cfg.kingfisher_dir, (cfg.catalogue_roots["skills"],), roots
    )

    assert len(protected) == len(set(protected))
    assert len(protected) == len(roots) + 1, "a duplicate survived, or a root was dropped"


def test_a_definition_root_that_does_not_exist_is_still_named(cfg, tmp_path):
    """A profile is written once, and `seed` runs after it at least once."""
    absent = tmp_path / "not-created"

    protected = confinement.protected_roots(tmp_path / "ws" / ".kingfisher", (), (absent,))

    assert absent.resolve() in protected


# -- nor are the files a deployment authors -----------------------------------


@macos
def test_the_shell_cannot_change_the_files_a_deployment_authors(cfg, session_dir):
    """`models.yaml` and `source_ids.yaml` were left writable on the reasoning that an
    edit lands at the next restart -- which is when an edited definition lands too --
    and `source_ids.yaml` is who may reach what. Tried four ways: overwrite, replace by
    rename, delete, and create where there was none.
    """
    models = cfg.authored_files["models.yaml"]
    source_ids = cfg.authored_files["source_ids.yaml"]
    beside = cfg.workspace / "notes.yaml"
    for written in (models, beside):
        written.write_text("as written\n", encoding="utf-8")
    source_ids.unlink(missing_ok=True)
    scratch = session_dir / "outputs" / "swap"
    backend = backend_at(cfg, session_dir)

    for target in (models, beside):
        backend.execute(f'printf x > "{target}"')
        backend.execute(f'printf y > "{scratch}" && mv "{scratch}" "{target}"')
    backend.execute(f'rm -f "{models}"')
    backend.execute(f'printf "open: [everyone]" > "{source_ids}"')

    assert models.read_text(encoding="utf-8") == "as written\n"
    assert not source_ids.exists(), "the shell created the access policy"
    # The control: the same commands against a file beside them, so a pass above is
    # the profile refusing rather than the commands never working.
    assert beside.read_text(encoding="utf-8") == "y"


@macos
def test_the_shell_cannot_lift_the_bits_on_the_callers_inputs(cfg, session_dir):
    """`/inputs` was kept from the shell by dropping its write bits, and the shell runs
    as the uid that owns them -- so `chmod u+w` and the same write replaced the
    caller's file, and `rm -rf inputs` took the folder. Measured before this.

    The profile refuses the folder now, as it already refused `.harness`. Reads are
    kept, because the agent is given the caller's files to read, and the rest of the
    session stays writable -- both asserted, so a rule that refused everything would
    not pass this.
    """
    backend = backend_at(cfg, session_dir)
    backend.upload_files([(f"{INPUTS_ROUTE}given.txt", b"as written")])

    lifted = backend.execute(
        "chmod u+w inputs inputs/given.txt && printf FORGED > inputs/given.txt"
    )
    removed = backend.execute("rm -rf inputs")
    (held,) = backend.download_files([f"{INPUTS_ROUTE}given.txt"])

    assert lifted.exit_code != 0 and held.content == b"as written"
    assert removed.exit_code != 0 and (session_dir / "inputs").is_dir()
    # The controls: still readable, and the session's own work still writable.
    assert backend.execute("cat inputs/given.txt").output.strip() == "as written"
    assert backend.execute("printf ok > outputs/mine.txt").exit_code == 0


@macos
@pytest.mark.parametrize(
    "layout", [DEFAULT_LAYOUT, Layout(scratchpad="work", tool_results="kept")],
    ids=["default", "renamed"],
)
def test_the_shell_cannot_rewrite_a_saved_result(cfg, layout):
    """A ledger row names its file and a later turn reads the file back as what the tool
    returned, so a shell that can write there forges a result. Tried by overwriting, by
    adding a file, through a hard link, by removing the folder, and by renaming the
    scratchpad away and back --
    which beat a rule on the two folders alone: measured, the row's file held `FORGED`.

    Through `open`, as a turn gets its backend, so the renamed case also catches a
    layout that reaches the folders but not the fence. Reads stay and the scratchpad
    stays writable, both asserted, so a rule refusing everything would not pass this.
    """
    named = replace(cfg, layout=layout)
    backend = LocalSessions().open(named, "results")
    scratch, kept = layout.scratchpad, layout.tool_results
    routes = (layout.tool_results_route, layout.offloads_route)
    backend.upload_files([(f"{route}A-0001.txt", b"as written") for route in routes])

    for folder in layout.saved_results:
        backend.execute(f"printf FORGED > {folder}/A-0001.txt")
        backend.execute(f"printf FORGED > {folder}/A-0002.txt")
        backend.execute(f"ln {folder}/A-0001.txt linked && printf FORGED > linked; rm -f linked")
        backend.execute(f"rm -rf {folder}")
    moved = backend.execute(
        f"mv {scratch} moved && printf FORGED > moved/{kept}/A-0001.txt; mv moved {scratch}"
    )

    held = backend.download_files([f"{route}A-0001.txt" for route in routes])
    added = backend.download_files([f"{route}A-0002.txt" for route in routes])
    assert [h.content for h in held] == [b"as written", b"as written"]
    assert all(a.error is not None for a in added), "the shell added a result"
    assert moved.exit_code != 0
    # The controls: the results still read, and the agent's own scratch still writes.
    assert backend.execute(f"cat {layout.saved_results[0]}/A-0001.txt").output == "as written"
    made = backend.execute(
        f"printf ok > {scratch}/mine.txt && mkdir -p {scratch}/d/e && rm -rf {scratch}/d"
    )
    assert made.exit_code == 0, made.output


@macos
def test_the_shell_cannot_change_the_keys_file(cfg, session_dir):
    """A keys file was writable where `models.yaml` beside it was not, and on no
    argument: the reason `profile` gives for protecting an authored file is that a shell
    able to edit it picks what the next start sends, which a credentials file is the
    strongest case of rather than an exception to.

    Three of the four ways above -- overwrite, replace by rename, delete. Creating one
    where there was none is already held for `source_ids.yaml`, and the profile names a
    path whether or not it exists.
    """
    keys = cfg.workspace / "keys.yaml"
    keys.write_text("minimax: sk-as-written\n", encoding="utf-8")
    beside = cfg.workspace / "notes.yaml"
    beside.write_text("as written\n", encoding="utf-8")
    named = replace(cfg, models=replace(cfg.models, keys_file=keys))
    scratch = session_dir / "outputs" / "swap"
    backend = backend_at(named, session_dir)

    backend.execute(f'printf x > "{keys}"')
    backend.execute(f'printf y > "{scratch}" && mv "{scratch}" "{keys}"')
    backend.execute(f'rm -f "{keys}"')
    backend.execute(f'printf z > "{beside}"')

    assert keys.read_text(encoding="utf-8") == "minimax: sk-as-written\n"
    # The control, as for the files above: the same commands against a file beside it,
    # so a pass here is the profile refusing rather than the commands never running.
    assert beside.read_text(encoding="utf-8") == "z"


#: Directory names a macOS filesystem accepts and `re.escape` leaves untouched. The
#: `"` is the one that mattered -- it is what ends an sbpl string literal -- and the
#: rest are beside it because a rule written for one character passes against that
#: character alone.
AWKWARD_NAMES = ['my "work" stuff', "back\\slash", "a'quote", "paren(s)", "star*", "plus+sign"]


def _as_sbpl_reads_it(line: str) -> tuple[str, str]:
    """The pattern sbpl finds in one `(regex #"...")` line, and whatever followed it.

    Raw: the literal ends at the first `"` and a backslash is not an escape here --
    measured, which is why `\\"` cannot put a quote in and why doubling the backslashes
    `re.escape` writes broke every path holding a `-`.

    What followed is the half that matters. A `"` inside ends the literal there and
    sbpl reads the rest of the line as code, which is how a directory name became
    `unbound variable: work"`.
    """
    rest = line[line.index("(regex ") + len("(regex ") :].removeprefix('#"')
    pattern, _, after = rest.partition('"')
    return pattern, after



@pytest.mark.parametrize("name", AWKWARD_NAMES)
def test_every_pattern_in_the_profile_still_means_the_path_it_is_about(name, tmp_path):
    """Two layers, and `re.escape` is only the inner one: sbpl reads `#"..."` as a
    string -- a `"` ends it, a backslash escapes -- and compiles what it finds. So this
    decodes each literal the way sbpl would and matches the result against the real
    paths, which is the only way to see both layers at once. Measured before it: a
    workspace called `my "work" stuff` ended the literal early, the profile did not
    parse, and every command in the deployment came back exit 65 with `sandbox-exec:
    unbound variable: work"` -- reads included.

    Platform-independent, because the text is generated the same everywhere and this is
    about the text. A Mac is what reads it, and the rule below drives one.
    """
    workspace = ensure_layout(tmp_path / name)
    sessions = (workspace / "sessions").resolve()

    text = sandbox_exec.profile(
        home=tmp_path / "home",
        sessions_root=workspace / "sessions",
        readable=(),
        writable=(workspace,),
        itself=workspace / ".kingfisher" / "shell.sb",
        layout=DEFAULT_LAYOUT,
    )

    read = [_as_sbpl_reads_it(line) for line in text.splitlines() if "(regex " in line]
    assert len(read) == 6, f"six rules are written as patterns; this found {len(read)}"
    for _, after in read:
        assert after == "))", f"the literal ends early, and sbpl reads {after!r} as code"
    denied = [re.compile(pattern) for pattern, _ in read]
    one = sessions / "s1"
    for path in (
        sessions,
        one / "inputs" / "given.txt",
        one / HARNESS,
        one / "scratchpad",
        one / "scratchpad" / "tool_results" / "A-0001-execute.txt",
        one / "scratchpad" / "large_tool_results" / "c1",
    ):
        assert any(rule.match(str(path)) for rule in denied), f"{path} is denied by nothing"
    for own in (one / "outputs" / "mine.txt", one / "scratchpad" / "mine.txt"):
        assert not any(rule.match(str(own)) for rule in denied), f"{own} is denied"


def test_a_renamed_inputs_leaf_is_the_one_the_profile_denies(tmp_path):
    """A layout that renames `inputs` moves the macOS deny with it.

    The kernel rule is keyed on the leaf's name, so the one the profile writes has to
    be the configured one and not the default spelling -- otherwise a renamed inputs
    folder is writable to the shell and the default name, which no session has, is
    guarded instead.
    """
    workspace = ensure_layout(tmp_path / "ws")
    sessions = (workspace / "sessions").resolve()

    text = sandbox_exec.profile(
        home=tmp_path / "home",
        sessions_root=workspace / "sessions",
        readable=(),
        writable=(workspace,),
        itself=workspace / ".kingfisher" / "shell.sb",
        layout=Layout(inputs="vault"),
    )

    denied = [
        re.compile(_as_sbpl_reads_it(line)[0])
        for line in text.splitlines()
        if "(regex " in line
    ]
    assert any(one.match(str(sessions / "s1" / "vault" / "given.txt")) for one in denied), (
        "the renamed inputs leaf is denied by nothing"
    )
    assert not any(one.match(str(sessions / "s1" / "inputs" / "given.txt")) for one in denied), (
        "the default name is denied though no session has it, and the renamed one is not"
    )


def test_a_renamed_layout_moves_the_saved_results_denies_with_it(tmp_path):
    """The saved-results rules are spelled from the configured scratchpad and
    `tool_results`, for the reason the renamed inputs leaf above gives -- and the
    scratchpad rule with them, or a renamed one could be moved away from under its
    results as the default could before it was denied.
    """
    workspace = ensure_layout(tmp_path / "ws")
    one = (workspace / "sessions").resolve() / "s1"

    text = sandbox_exec.profile(
        home=tmp_path / "home",
        sessions_root=workspace / "sessions",
        readable=(),
        writable=(workspace,),
        itself=workspace / ".kingfisher" / "shell.sb",
        layout=Layout(scratchpad="work", tool_results="kept"),
    )

    denied = [
        re.compile(_as_sbpl_reads_it(line)[0])
        for line in text.splitlines()
        if "(regex " in line
    ]
    work = one / "work"
    for path in (work, work / "kept" / "A.txt", work / "large_tool_results" / "c1"):
        assert any(rule.match(str(path)) for rule in denied), f"{path} is denied by nothing"
    for path in (one / "scratchpad" / "tool_results" / "A.txt", work / "mine.txt"):
        assert not any(rule.match(str(path)) for rule in denied), f"{path} is denied"


def test_what_stands_in_for_a_quote_cannot_reach_across_a_directory(tmp_path):
    """`.` matches a quote as well and is the shorter answer, and it also matches `/` --
    so a workspace called `a"b` would deny `sessions/*/inputs` under a *directory*
    called `a` holding one called `b`, which is a tree this profile was never asked
    about. The stand-in is a class excluding the separator for that reason, and this
    rule is what makes the choice between them visible.
    """
    workspace = ensure_layout(tmp_path / 'a"b')

    text = sandbox_exec.profile(
        home=tmp_path / "home",
        sessions_root=workspace / "sessions",
        readable=(),
        writable=(workspace,),
        itself=workspace / ".kingfisher" / "shell.sb",
        layout=DEFAULT_LAYOUT,
    )

    denied = [
        re.compile(_as_sbpl_reads_it(line)[0])
        for line in text.splitlines()
        if "(regex " in line
    ]
    elsewhere = tmp_path.resolve() / "a" / "b" / "sessions" / "s1" / "inputs" / "given.txt"
    assert any(one.match(str((workspace / "sessions").resolve())) for one in denied), (
        "the workspace this profile is for is denied by nothing, so the rule proves nothing"
    )
    assert not any(one.match(str(elsewhere)) for one in denied), f"{elsewhere} is denied"


@macos
def test_the_fence_holds_for_a_workspace_whose_name_holds_a_quote(tmp_path, cfg):
    """The rule above read as sbpl reads; this hands the file to sbpl. A quote is legal
    in a macOS directory name and `re.escape` leaves it alone, so the profile did not
    parse at all -- and a profile that does not parse takes the deployment with it, not
    just the fence.

    The mode bits are lifted first, because `inputs` has its write bits dropped as well
    and a refusal from those satisfies an `exit_code != 0` on its own, with a different
    errno. That is the trap a review found in the rule this one is modelled on.
    """
    workspace = ensure_layout(tmp_path / 'my "work" stuff')
    session = ensure_session_layout(workspace / "sessions" / "s1", DEFAULT_LAYOUT)
    (session / "inputs" / "given.txt").write_text("as written", encoding="utf-8")
    shell = backend_at(replace(cfg, workspace=workspace), session)

    own = shell.execute("printf ok > outputs/mine.txt")
    lifted = shell.execute("chmod -R u+w inputs 2>/dev/null; printf FORGED > inputs/given.txt")
    harnessed = shell.execute(f"chmod -R u+w {HARNESS} 2>/dev/null; printf x > {HARNESS}/forged")
    sibling = shell.execute("mkdir ../fabricated")

    assert own.exit_code == 0, f"the deployment is dead, not fenced: {own.output}"
    assert (session / "outputs" / "mine.txt").read_text(encoding="utf-8") == "ok"
    assert lifted.exit_code != 0 and harnessed.exit_code != 0 and sibling.exit_code != 0
    assert (session / "inputs" / "given.txt").read_text(encoding="utf-8") == "as written"
    assert not (session / HARNESS / "forged").exists()


def test_an_authored_file_is_denied_by_its_exact_path(tmp_path):
    """By `path`, as the profile's own rule is, so the deny names the file rather than
    whatever directory it happens to sit in, which may be one that has to stay writable.
    """
    workspace = tmp_path / "ws"
    models = workspace / "config" / "models.yaml"

    text = sandbox_exec.profile(
        home=tmp_path / "home",
        sessions_root=workspace / "sessions",
        readable=(),
        writable=(workspace,),
        itself=workspace / ".kingfisher" / "shell.sb",
        protected_files=(models,),
        layout=DEFAULT_LAYOUT,
    )

    assert f'(deny file-write* (path "{models}"))' in text
    assert f'(deny file-write* (subpath "{models}"))' not in text


@macos
def test_an_authored_file_is_denied_by_its_real_path(cfg, tmp_path):
    """Catches the access policy going unprotected when the definitions folder is
    named through a link, because the profile matches real paths.
    """
    link = tmp_path / "linked-definitions"
    link.symlink_to(cfg.definitions)

    written = _written(confinement.shell_confinement(replace(cfg, definitions=link), DEFAULT_SHELL))
    real = cfg.definitions.resolve() / "source_ids.yaml"

    assert f'(deny file-write* (path "{real}"))' in written
    assert str(link) not in written


@macos
def test_a_catalogue_read_from_elsewhere_is_denied_where_it_was_read(cfg, tmp_path):
    """Catches a `Config` built in code, whose `models.yaml` was read from outside the
    definitions folder, leaving the file the next start reads writable by the shell --
    which then chooses where prompts go.
    """
    elsewhere = tmp_path / "shared" / "models.yaml"

    built = replace(cfg, models=replace(cfg.models, source=elsewhere))
    written = _written(confinement.shell_confinement(built, DEFAULT_SHELL))

    assert f'(deny file-write* (path "{elsewhere.resolve()}"))' in written


def _written(confined: confinement.Confinement) -> str:
    """The rules a confinement runs commands under, as written."""
    assert confined.profile is not None, "no profile written"
    return confined.profile.read_text(encoding="utf-8")


# -- the profile is not the agent's to edit either ------------------------


@macos
def test_the_shell_cannot_rewrite_the_profile_it_runs_under(cfg, session_dir):
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
    for how in [
            'printf x > "{profile}"',
            'printf x >> "{profile}"',
            'rm -f "{profile}"',
            'printf x > "{beside}"; mv "{beside}" "{profile}"',
        ]:
        backend = backend_at(cfg, session_dir)
        profile = backend.default.confinement.profile
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
    backend = backend_at(cfg, session_dir)
    try:
        profile = backend.default.confinement.profile
        backend.execute(f'printf "(version 1)\\n(allow default)\\n" > "{profile}"')

        result = backend.execute(f'cat "{secret}"')
    finally:
        secret.unlink(missing_ok=True)

    assert "PRIVATE" not in str(result.output), "the shell rewrote its way into the home"


def test_the_profile_refuses_itself_last(tmp_path):
    """`sandbox-exec` takes the last matching rule, and this one has to beat the
    allow that covers the directory it sits in."""
    written = tmp_path / "ws" / ".kingfisher" / "shell.sb"

    lines = sandbox_exec.profile(
        home=tmp_path / "home",
        sessions_root=tmp_path / "ws" / "sessions",
        readable=(tmp_path / "ws",),
        writable=(tmp_path / "ws",),
        itself=written,
        protected=(tmp_path / "ws" / "skills",),
        layout=DEFAULT_LAYOUT,
    ).splitlines()

    # The session-folder denials that follow it are the same kind of rule over
    # different paths, so all of them have to sit after the allows. One per folder
    # refused that way -- `inputs`, `.harness` and the saved results -- and the profile
    # ends with them.
    tail = lines[-(3 + len(DEFAULT_LAYOUT.saved_results)) :]
    assert f'(deny file-write* (path "{written}"))' in tail
    assert all("deny file-write*" in line for line in tail), tail


@macos
def test_a_profile_is_replaced_rather_than_truncated(tmp_path):
    """A turn reading the profile while another rewrites it must not see half of one.

    Every input to `profile` comes from one `Config`, so concurrent writers write the
    same bytes; what is being prevented is the window in which the file is empty.
    """
    workspace = tmp_path / "ws"
    for _ in range(2):
        chosen = confinement.resolve(
        confinement.AUTO,
        workspace=workspace,
        sessions_root=workspace / "sessions",
        kingfisher_dir=workspace / ".kingfisher",
        layout=DEFAULT_LAYOUT,
    )

    assert chosen.profile is not None
    assert list(chosen.profile.parent.iterdir()) == [chosen.profile], (
        "a temporary profile was left beside the real one"
    )


@macos
def test_no_turn_can_read_the_profile_half_written(tmp_path, monkeypatch):
    """The window the replace closes, asserted by looking through it.

    A plain `write_text` truncates before it fills, and `sandbox-exec -f` re-reads the
    file at the start of every command -- so a concurrent turn binds itself to whatever
    is there. That is not a broken file failing closed: the profile is `(version 1)`
    then `(allow default)` then the denies, so its first two lines are a valid profile
    permitting everything. Measured by truncating a real 22-line profile and running
    `cat` on a file under the operator's home through `confined.wrap`: exit 1 and
    "Operation not permitted" at full length, exit 0 and the file's contents at two
    lines.

    Driven by watching the rename, because the race cannot be provoked reliably: what
    sits at the profile's path the instant before the replace is what any reader could
    have seen, and it has to be the whole of the last profile rather than a prefix of
    the next. The two rules below assert that no scratch file is *left* -- which a
    truncating write satisfies too, so neither of them held this: replacing the body
    with `write_text` left all 2,390 tests green.
    """
    workspace = tmp_path / "ws"
    path = confinement.resolve(
        confinement.AUTO,
        workspace=workspace,
        sessions_root=workspace / "sessions",
        kingfisher_dir=workspace / ".kingfisher",
        layout=DEFAULT_LAYOUT,
    ).profile
    assert path is not None
    whole = path.read_text(encoding="utf-8")

    seen: list[str] = []
    renaming = Path.replace

    def watched(self, target):
        if Path(target) == path:
            seen.append(path.read_text(encoding="utf-8") if path.exists() else "")
        return renaming(self, target)

    monkeypatch.setattr(Path, "replace", watched)
    confinement.resolve(
        confinement.AUTO,
        workspace=workspace,
        sessions_root=workspace / "sessions",
        kingfisher_dir=workspace / ".kingfisher",
        layout=DEFAULT_LAYOUT,
    )

    assert seen == [whole], "the profile was not replaced by a rename over a whole file"


def test_every_rule_reading_the_written_profile_is_marked_for_macos():
    """A rule that both drives `resolve` and reads the file it writes needs the marker:
    on Linux `resolve` returns a fence that writes no profile, so the assertion is a
    `FileNotFoundError` rather than a skip.

    Written because `test_no_turn_can_read_the_profile_half_written` arrived without the
    marker and CI's Linux job is what found it -- every other job here is macOS, where
    the mistake cannot show. Both halves are needed: eight rules drive `resolve` for the
    mechanism it picks and are platform-independent, and two build the profile text
    without driving anything.

    Matched over the syntax tree rather than the source text, so the predicate does not
    find its own description -- the first version of this rule named itself.

    Over every test file and not `Path(__file__)`, which was the second version's
    mistake: six other files wear the `macos` marker, so the one file this rule sits in
    was the only one it could see. The same test planted unmarked in
    `test_harness_directory.py` left the suite green and this rule silent.
    """
    import ast

    def reads_the_written_profile(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        drives = reads = False
        # The callees, so `sandbox_exec.profile(...)` -- which builds the text and writes
        # nothing -- is not taken for the field naming the written file.
        called = {
            id(inner.func) for inner in ast.walk(node) if isinstance(inner, ast.Call)
        }
        for inner in ast.walk(node):
            # `confinement.resolve` and no other: `Models.resolve` writes nothing.
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr == "resolve"
                and isinstance(inner.func.value, ast.Name)
                and inner.func.value.id == "confinement"
            ):
                drives = True
            # The written profile is reached through the confinement `resolve` returns,
            # whose `profile` names it, or by the function naming it from its rules.
            if isinstance(inner, ast.Attribute) and id(inner) not in called:
                reads = reads or inner.attr == "profile"
            if isinstance(inner, ast.Attribute) and inner.attr == "profile_path":
                reads = True
        return drives and reads

    tests_root = repository_root() / "tests"
    files = sorted(tests_root.rglob("test_*.py"))
    # The collection is the whole rule: pointed at nothing it reports success over
    # nothing, which is what `test_no_rule_here_is_parametrized_over_nothing` exists for
    # and what reading one file amounted to. Counted against the tree rather than
    # against one, because `> 1` passed on two files of a hundred and more -- a
    # threshold that is satisfied by almost nothing is the same mistake one step along.
    on_disk = sum(1 for _ in tests_root.rglob("test_*.py"))
    assert len(files) == on_disk and on_disk > 1, (
        f"this rule walks {len(files)} of the {on_disk} test files on disk"
    )

    unmarked = [
        f"{path.relative_to(tests_root.parent).as_posix()}::{node.name}"
        for path in files
        # `walk`, not `body`: a rule written as a method of a `Test*` class binds the
        # same mistake and was invisible here. None exists today, which is why only a
        # planted one finds it.
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name.startswith("test_")
        and reads_the_written_profile(node)
        and not any("macos" in ast.unparse(mark) for mark in node.decorator_list)
    ]

    assert not unmarked, f"{unmarked} read the profile `resolve` writes, without @macos"


@macos
def test_concurrent_writers_do_not_share_one_scratch_file(tmp_path):
    """Written concurrently, because the test above writes twice in a row -- the one
    arrangement in which a scratch file named after the process cannot collide.

    The scratch was named `shell.sb.<pid>`, and every thread of a process has the
    same pid: eight concurrent turns wrote one file and raced to rename it, four of
    them failing with `FileNotFoundError` because the winner had already moved
    theirs. Found by measuring whether turns overlap on threads, which is what
    `decisions.md` sends a caller wanting that to do.
    """
    workspace = tmp_path / "ws"
    writers = 8

    with futures.ThreadPoolExecutor(max_workers=writers) as pool:
        done = [
            pool.submit(
                confinement.resolve,
                confinement.AUTO,
                workspace=workspace,
                sessions_root=workspace / "sessions",
                kingfisher_dir=workspace / ".kingfisher",
                layout=DEFAULT_LAYOUT,
            )
            for _ in range(writers)
        ]
    failed = [task.exception() for task in done if task.exception() is not None]

    assert not failed, f"{len(failed)} of {writers} concurrent writers failed: {failed[0]!r}"
    profiles = {task.result().profile for task in done}
    assert len(profiles) == 1, "the same rules were written under more than one name"
    (profile,) = profiles
    assert profile is not None
    assert list(profile.parent.iterdir()) == [profile], (
        "a temporary profile was left beside the real one"
    )


# -- what the shell reads, by each fence's shape -----------------------------


def test_what_the_shell_reads_follows_each_fences_shape(tmp_path):
    """macOS allows everything, denies the home and gives some of it back; the Linux
    fences grant a list and nothing else. A rule asking only "is it in the workspace"
    would call `/tmp` safe on a Mac, where it is not.
    """
    from kingfisher.plugins.sessions.sandbox.confinement import (
        BUBBLEWRAP,
        LANDLOCK,
        SANDBOX_EXEC,
        shell_reads,
    )

    home, system = tmp_path / "home", tmp_path / "usr"
    workspace = home / "ws"
    hidden = home / ".config" / "keys.yaml"
    inside = workspace / "keys.yaml"
    outside = tmp_path / "opt" / "keys.yaml"

    def reads(path, mechanism, readable):
        return shell_reads(path, mechanism=mechanism, home=home, readable=readable)

    assert reads(hidden, SANDBOX_EXEC, (workspace,)) is None
    assert str(workspace) in (reads(inside, SANDBOX_EXEC, (workspace,)) or "")
    assert "outside the home folder" in (reads(outside, SANDBOX_EXEC, (workspace,)) or "")
    for fence in (BUBBLEWRAP, LANDLOCK):
        assert reads(outside, fence, (system,)) is None, f"{fence} granted what it never listed"
        assert reads(inside, fence, (system,)) is None
        assert str(system) in (reads(system / "keys.yaml", fence, (system,)) or "")
    assert "nothing" in (reads(hidden, "", ()) or ""), "with no fence, everything is read"


def test_each_linux_fence_is_read_from_its_own_list_of_system_paths(cfg):
    """One list serving both is how a mechanism gains a path it meant to deny:
    bubblewrap binds no `/proc` or `/dev`, and `shell_readable` is what `doctor` asks
    to say whether a fence can reach the keys file.

    Asserted as a difference rather than against either list, so adding a system path
    to both fences leaves this alone and giving one fence the other's list reddens it.
    """
    from kingfisher.plugins.sessions.sandbox import bubblewrap, fence

    assert set(fence.SYSTEM_PATHS) != set(bubblewrap.SYSTEM_PATHS), (
        "the two lists are identical, so this rule cannot tell them apart"
    )

    landlock = confinement.shell_readable(cfg, DEFAULT_SHELL, confinement.LANDLOCK)
    bwrap = confinement.shell_readable(cfg, DEFAULT_SHELL, confinement.BUBBLEWRAP)

    assert set(landlock) != set(bwrap)
