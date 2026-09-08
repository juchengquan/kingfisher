"""The escape list, run against a real kernel."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from kingfisher.infrastructure.sandbox.confinement import (
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
    from kingfisher.infrastructure.workspace.sessions import ensure_session_layout

    mine = tmp_path / "sessions" / "a"
    theirs = tmp_path / "sessions" / "b"
    for session in (mine, theirs):
        ensure_session_layout(session)
    (mine / "derived" / "secret.txt").write_text("TENANT-A-PRIVATE\n", encoding="utf-8")
    return mine, theirs


@pytest.fixture
def fenced(two_sessions):
    """A runner confined to the second session, as `build_backend` builds one."""
    from kingfisher.infrastructure.sandbox.fence import LandlockRunner, policy_for

    _, theirs = two_sessions
    return LandlockRunner(
        policy_for(theirs, readable=toolchain_roots()),
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


#: The two that need somewhere to put a link or a mountpoint use `derived/` and not
#: the working directory. The session directory is readable and not writable -- see
#: `_session_writable` -- so scratch made there fails before the escape it sets up is
#: ever attempted, and the case passes without having tried anything.
ESCAPES = [
    ("read it directly", "cat {secret}"),
    ("climb out with a relative path", "cd .. && cat a/derived/secret.txt"),
    (
        "follow a symlink into it",
        "ln -sf {secret} derived/link.txt && cat derived/link.txt",
    ),
    ("go round through /proc", "cat /proc/self/root{secret}"),
    (
        "bind-mount it somewhere allowed",
        "mkdir -p derived/in && mount --bind {theirs} derived/in",
    ),
    ("hide the fence under a tmpfs", "mount -t tmpfs none {theirs}"),
    ("take a new mount namespace", "unshare -m sh -c 'cat {secret}'"),
]


@needs_landlock
@pytest.mark.parametrize(("what", "command"), ESCAPES, ids=[e[0] for e in ESCAPES])
def test_the_fence_holds(fenced, two_sessions, what, command):
    """Every way out that was tried, and every one of them fails."""
    mine, theirs = two_sessions
    spelled = command.format(secret=mine / "derived" / "secret.txt", theirs=theirs)

    result = fenced.run(spelled)

    assert "TENANT-A-PRIVATE" not in result.output, f"the fence let a tenant {what}"
    assert result.exit_code != 0


#: The escapes whose control needs nothing but a filesystem, so it runs on the
#: machine this is written on. `/proc/self/root` needs a `/proc` and the three
#: mount cases need `SYS_ADMIN`, so their controls belong in the container
#: beside the fenced runs they pair with.
PORTABLE = ESCAPES[:3]


@pytest.mark.parametrize(("what", "command"), PORTABLE, ids=[e[0] for e in PORTABLE])
def test_each_escape_works_when_nothing_is_fencing_it(two_sessions, what, command):
    """The control, and it runs everywhere rather than only where Landlock does."""
    mine, theirs = two_sessions
    spelled = command.format(secret=mine / "derived" / "secret.txt", theirs=theirs)

    _, output = unfenced(spelled, cwd=theirs)

    assert "TENANT-A-PRIVATE" in output, f"the control cannot {what}, so it proves nothing"


@needs_landlock
def test_the_session_itself_stays_usable(fenced, two_sessions):
    """A fence that broke the agent's own working directory would be swapped straight
    back out, which is the fastest way to end up with no fence.
    """
    result = fenced.run("echo written > derived/note.txt && cat derived/note.txt")

    assert result.exit_code == 0
    assert "written" in result.output


@needs_landlock
def test_a_fence_cannot_be_widened_from_inside(fenced, two_sessions):
    """Landlock rulesets only ever narrow, and a child inherits the parent's."""
    mine = two_sessions[0]
    secret = mine / "derived" / "secret.txt"

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
    from kingfisher.infrastructure.sandbox.fence import LandlockRunner, policy_for

    return LandlockRunner(
        policy_for(session, readable=toolchain_roots()),
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
    from kingfisher.layout import HARNESS

    _, theirs = two_sessions
    pinned = theirs / HARNESS / "agent.yaml"
    pinned.write_text("name: pinned\n", encoding="utf-8")

    _runner_for(theirs).run(f'printf "name: mine" > {pinned}')

    assert pinned.read_text(encoding="utf-8") == "name: pinned\n", (
        "the shell rewrote the agent definition its own session is pinned to"
    )


@needs_landlock
def test_the_rest_of_the_session_stays_writable(two_sessions):
    """The bound on the rule above: one directory is left out of the grants, not the
    session's own.
    """
    _, theirs = two_sessions

    outcome = _runner_for(theirs).run(f'echo fine > {theirs / "derived" / "ok.txt"}')

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
    assert "derived" in outcome.output
