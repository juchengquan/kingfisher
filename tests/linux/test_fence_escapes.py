"""The escape list, run against a real kernel."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from kingfisher.core.layout import DEFAULT_LAYOUT
from kingfisher.plugins.sessions.sandbox.confinement import (
    REQUIRED_LANDLOCK_ABI,
    landlock_abi,
    landlock_ready,
    toolchain_roots,
)


def _why_not() -> str:
    abi = landlock_abi()
    if abi is None:
        return "no Landlock on this kernel"
    if abi < REQUIRED_LANDLOCK_ABI:
        return f"Landlock ABI {abi}, below the {REQUIRED_LANDLOCK_ABI} sandlock needs"
    return "sandlock is not installed -- pip install 'kingfisher[fence]'"


needs_landlock = pytest.mark.skipif(not landlock_ready(), reason=_why_not())


@pytest.fixture
def two_sessions(tmp_path):
    """A tenant with a secret, and a tenant without one.

    Laid out by `ensure_session_layout` rather than one `mkdir`, because the writable
    rules are one per directory in a session: a session missing them is fenced by a
    policy that grants nothing, and every escape below would fail for that reason
    instead of the one being tested.
    """
    from kingfisher.plugins.sessions.folders import ensure_session_layout

    mine = tmp_path / "sessions" / "a"
    theirs = tmp_path / "sessions" / "b"
    for session in (mine, theirs):
        ensure_session_layout(session, DEFAULT_LAYOUT)
    (mine / "outputs" / "secret.txt").write_text("TENANT-A-PRIVATE\n", encoding="utf-8")
    return mine, theirs


@pytest.fixture
def fenced(two_sessions):
    """A runner confined to the second session, as `LocalSessions` builds one."""
    from kingfisher.plugins.sessions.sandbox.fence import LandlockRunner, policy_for

    _, theirs = two_sessions
    return LandlockRunner(
        policy_for(theirs, DEFAULT_LAYOUT, readable=toolchain_roots()),
        cwd=theirs,
        env={"PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin:/usr/local/bin"},
    )


def unfenced(command: str, cwd):
    """The same command with no fence, so a denial can be told from a typo."""
    import subprocess

    done = subprocess.run(  # noqa: S602 -- the control, deliberately unconfined
        command, shell=True, cwd=str(cwd), capture_output=True, text=True, check=False
    )
    return done.returncode, done.stdout + done.stderr


#: The two that need somewhere to put a link or a mountpoint use `outputs/` and not
#: the working directory. The session directory is readable and not writable -- see
#: `_session_writable` -- so scratch made there fails before the escape it sets up is
#: ever attempted, and the case passes without having tried anything.
ESCAPES = [
    ("read it directly", "cat {secret}"),
    ("climb out with a relative path", "cd .. && cat a/outputs/secret.txt"),
    (
        "follow a symlink into it",
        "ln -sf {secret} outputs/link.txt && cat outputs/link.txt",
    ),
    ("go round through /proc", "cat /proc/self/root{secret}"),
    (
        "bind-mount it somewhere allowed",
        "mkdir -p outputs/in && mount --bind {theirs} outputs/in",
    ),
    ("hide the fence under a tmpfs", "mount -t tmpfs none {theirs}"),
    ("take a new mount namespace", "unshare -m sh -c 'cat {secret}'"),
]


@needs_landlock
def test_the_fence_holds(fenced, two_sessions):
    """Every way out that was tried, and every one of them fails.

    One runner for all of them, which is safe only because every case fails: the
    escapes that write before they fail leave a symlink or an empty directory inside
    the session that is allowed to have them, and nothing later reads either.
    """
    assert ESCAPES, "no escapes listed -- this walks nothing and passes"
    mine, theirs = two_sessions
    leaked = []
    for what, command in ESCAPES:
        spelled = command.format(secret=mine / "outputs" / "secret.txt", theirs=theirs)

        result = fenced.run(spelled)

        if "TENANT-A-PRIVATE" in result.output:
            leaked.append(f"the fence let a tenant {what}")
        elif result.exit_code == 0:
            leaked.append(f"{what!r} was allowed to succeed, so it read nothing by luck")

    assert not leaked, "\n".join(leaked)


#: The escapes whose control needs nothing but a filesystem, so it runs on the
#: machine this is written on. `/proc/self/root` needs a `/proc` and the three
#: mount cases need `SYS_ADMIN`, so their controls belong in the container
#: beside the fenced runs they pair with.
PORTABLE = ESCAPES[:3]


def test_each_escape_works_when_nothing_is_fencing_it(two_sessions):
    """The control, and it runs everywhere rather than only where Landlock does.

    Without it the test above would pass against a misspelled path just as happily as
    against a working fence, which is the failure that makes a security test worse than
    none.

    `PORTABLE` is the three that need no `SYS_ADMIN`, so none of them mounts anything:
    two read and the third overwrites its own symlink, which is why one pair of
    sessions serves all three.
    """
    assert PORTABLE, "no portable escapes -- this control proves nothing"
    mine, theirs = two_sessions
    blunt = []
    for what, command in PORTABLE:
        spelled = command.format(secret=mine / "outputs" / "secret.txt", theirs=theirs)

        _, output = unfenced(spelled, cwd=theirs)

        if "TENANT-A-PRIVATE" not in output:
            blunt.append(f"the control cannot {what}, so it proves nothing")

    assert not blunt, "\n".join(blunt)


@needs_landlock
def test_the_session_itself_stays_usable(fenced, two_sessions):
    """A fence that broke the agent's own working directory would be swapped straight
    back out, which is the fastest way to end up with no fence.
    """
    result = fenced.run("echo written > outputs/note.txt && cat outputs/note.txt")

    assert result.exit_code == 0
    assert "written" in result.output


@needs_landlock
def test_a_fence_cannot_be_widened_from_inside(fenced, two_sessions):
    """Landlock rulesets only ever narrow, and a child inherits the parent's."""
    mine = two_sessions[0]
    secret = mine / "outputs" / "secret.txt"

    result = fenced.run(f"sh -c \"sh -c 'cat {secret}'\"")

    assert "TENANT-A-PRIVATE" not in result.output


@needs_landlock
def test_the_agent_reaches_its_own_interpreter(fenced):
    """The promise `pyproject.toml` makes, held against a real kernel."""
    result = fenced.run('python3 -c "import sys, yaml; print(sys.prefix)"')

    assert result.exit_code == 0, f"the agent could not run Python: {result.output}"
    assert sys.prefix in result.output, (
        f"the fenced shell reached a different interpreter: {result.output.strip()}"
    )


# -- the harness directory, which only a real kernel can answer for -------


def _runner_for(session):
    from kingfisher.plugins.sessions.sandbox.fence import LandlockRunner, policy_for

    return LandlockRunner(
        policy_for(session, DEFAULT_LAYOUT, readable=toolchain_roots()),
        cwd=session,
        env={"PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin:/usr/local/bin"},
    )


@needs_landlock
def test_the_shell_cannot_write_the_sessions_own_harness(two_sessions):
    """The Linux half of the pair `.harness` is denied by.

    It caught the bug it was written to catch. `policy_for` used to grant the session
    writable and name `<session>/.harness` readable, on the reading that the kernel
    resolves a path by its most nested matching rule; it does not, so the shell
    overwrote the agent definition its own session was pinned to. Landlock rules only
    grant, and a write walks up until one of them answers.
    """
    from kingfisher.core.layout import HARNESS

    _, theirs = two_sessions
    pinned = theirs / HARNESS / "agent.yaml"
    pinned.write_text("name: pinned\n", encoding="utf-8")

    _runner_for(theirs).run(f'printf "name: mine" > {pinned}')

    assert pinned.read_text(encoding="utf-8") == "name: pinned\n", (
        "the shell rewrote the agent definition its own session is pinned to"
    )


@needs_landlock
def test_the_shell_cannot_write_the_harness_of_a_session_it_opened(cfg):
    """Driven through `LocalSessions`, which decides where the session is, rather than
    handed a path.
    """
    from kingfisher.core.layout import HARNESS
    from kingfisher.plugins import LocalSessions

    shell = LocalSessions().open(cfg, "the-one")
    harness = cfg.workspace / "sessions" / "the-one" / HARNESS
    pinned = harness / "agent.yaml"
    pinned.write_text("name: pinned\n", encoding="utf-8")

    shell.execute(f'printf "name: mine" > {pinned}')
    shell.execute(f"rm -rf {harness}")

    assert pinned.read_text(encoding="utf-8") == "name: pinned\n", (
        "the shell rewrote the agent definition the one session is pinned to"
    )
    assert shell.execute("echo fine > outputs/ok.txt").exit_code == 0


@needs_landlock
def test_the_rest_of_the_session_stays_writable(two_sessions):
    """The bound on the rule above: one directory is left out of the grants, not the
    session's own.
    """
    _, theirs = two_sessions

    outcome = _runner_for(theirs).run(f'echo fine > {theirs / "outputs" / "ok.txt"}')

    assert outcome.exit_code == 0, outcome.output


@needs_landlock
def test_the_shell_cannot_write_into_the_session_directory_itself(two_sessions):
    """What denying `.harness` costs, asserted so that paying it stays a decision.

    Landlock cannot take a grant back, so the only way to deny `.harness` is to grant
    nothing above it -- which leaves the directory the shell starts in readable and not
    writable. Anything that makes a write here succeed has re-opened `.harness` with it.
    """
    _, theirs = two_sessions

    outcome = _runner_for(theirs).run("echo scratch > note.txt")

    assert outcome.exit_code != 0
    assert not (theirs / "note.txt").exists()


@needs_landlock
def test_the_shell_can_still_list_the_directory_it_starts_in(two_sessions):
    """The bound on that cost: the session is readable, only not writable. A shell that
    cannot `ls` its own working directory is one an operator switches the fence off for.
    """
    _, theirs = two_sessions

    outcome = _runner_for(theirs).run("ls")

    assert outcome.exit_code == 0, outcome.output
    assert "outputs" in outcome.output


@needs_landlock
def test_the_fenced_shell_keeps_the_runner_contract(two_sessions):
    """A command's leftovers die with it behind Landlock, which confines between fork
    and exec and so shares the launcher's process group handling with no fence at all."""
    from kingfisher.plugins.sessions.sandbox.fence import LandlockRunner, policy_for
    from tests.checks.runner import RUNNER_CHECKS

    _, theirs = two_sessions

    def fenced_runner() -> LandlockRunner:
        return LandlockRunner(
            policy_for(theirs, DEFAULT_LAYOUT, readable=toolchain_roots()),
            cwd=theirs,
            env={
                "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin:/usr/local/bin",
                "TMPDIR": str(theirs / DEFAULT_LAYOUT.scratchpad),
            },
        )

    for check in RUNNER_CHECKS:
        check(fenced_runner)


@needs_landlock
def test_a_command_may_throw_its_output_away(fenced):
    """`2>/dev/null` is in half the commands a model writes, and Landlock refused it:
    `cannot create /dev/null: Permission denied`, measured on the Linux job, so every
    such command failed before it ran."""
    result = fenced.run("echo gone > /dev/null && ls /nowhere 2>/dev/null; echo kept")

    assert "kept" in result.output
    assert "Permission denied" not in result.output


@needs_landlock
def test_the_rest_of_dev_stays_shut(fenced):
    """The control: `/dev/null` is the one device opened, not `/dev`."""
    result = fenced.run("touch /dev/kingfisher-probe")

    assert result.exit_code != 0
    assert not Path("/dev/kingfisher-probe").exists()



#: Each leaves its session -- `setsid` -- before it says it began, and the command waits
#: for that, so it ends with the escape done: one killed before it called `setsid` proved
#: nothing, which is what the first measurement mistook for a fence.
SETSID_ESCAPES = {
    "setsid(1)": "setsid sh -c 'touch \"$B\"; sleep 1.5; touch \"$M\"' >\"$M.log\" 2>&1 &",
    "fork, setsid, fork": (
        "python3 -c 'import os, pathlib, time\n"
        "if os.fork() == 0:\n"
        "    os.setsid()\n"
        "    if os.fork() == 0:\n"
        "        pathlib.Path(os.environ[\"B\"]).touch(); time.sleep(1.5)\n"
        "        pathlib.Path(os.environ[\"M\"]).touch()\n"
        "    os._exit(0)\n"
        "os.wait()' >\"$M.log\" 2>&1"
    ),
}


def _escaped(launch, session, escape: str, *, then: str = "", timeout: int = 20) -> bool:
    """Whether what `escape` left behind wrote its mark after the command had returned."""
    mark = session / "outputs" / f"mark-{time.monotonic_ns()}"
    began = Path(f"{mark}-began")
    launch(
        f"export M={mark} B={began}\n{escape}\n"
        f"while [ ! -e {began} ]; do sleep 0.05; done\n{then}",
        timeout,
    )
    assert began.exists(), f"the escape never began: {Path(f'{mark}.log').read_text()!r}"
    time.sleep(2.5)
    return mark.exists()


def _unreaped(runner):
    """`runner`'s fence and group kill with no reaper: the control."""
    import importlib

    from kingfisher.plugins.sessions.sandbox.process import run_command

    # By name: the wheel is Linux-only, and this file is type-checked on macOS too.
    confine = importlib.import_module("sandlock").confine

    def launch(command, timeout):
        return run_command(
            command,
            env=runner.env,
            timeout=timeout,
            cwd=runner.cwd,
            preexec_fn=lambda: confine(runner.policy),
        )

    return launch


@needs_landlock
@pytest.mark.parametrize("escape", SETSID_ESCAPES.values(), ids=SETSID_ESCAPES)
def test_a_process_that_left_the_group_dies_with_its_command(fenced, two_sessions, escape):
    """Measured on this job: a fork-setsid-fork outlived the group kill, as on macOS.
    The control is the same fence and kill with no reaper, where it must survive."""
    _, theirs = two_sessions

    assert _escaped(_unreaped(fenced), theirs, escape), "the escape did not escape"
    assert not _escaped(lambda c, t: fenced.run(c, timeout=t), theirs, escape)


@needs_landlock
@pytest.mark.parametrize("escape", SETSID_ESCAPES.values(), ids=SETSID_ESCAPES)
def test_a_process_that_left_the_group_dies_when_its_command_times_out(
    fenced, two_sessions, escape
):
    """Killed outright, the reaper hands what it adopted to init: measured, every escape
    survived a timeout until the reaper was asked first."""
    _, theirs = two_sessions

    assert not _escaped(
        lambda c, t: fenced.run(c, timeout=t), theirs, escape, then="sleep 30", timeout=2
    )
