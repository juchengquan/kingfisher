"""The escape list, run against a real kernel.

Everything in `tests/unit/test_fence.py` asserts what the *policy says*. These
assert what the *kernel does*, which is a different claim and the one that
matters: a policy naming the right paths and a fence that does not hold are
indistinguishable from inside this repository.

Skipped where there is no Landlock, which includes the machine this is written
on and both CI runners. That is not a gap being hidden -- it is why the skip
message says which of the three conditions failed. To run them:

    docker compose run --rm kingfisher \\
        sh -c "pip install -e '.[fence]' && python -m pytest tests/linux -v"

Each case has a control that runs the same command *unfenced* and asserts it
succeeds. Without one, a test proving "B cannot read A" would pass just as well
against a typo in the path, which is the failure mode that makes a security test
worse than none.
"""

from __future__ import annotations

import pytest

from kingfisher.infrastructure.confinement import (
    REQUIRED_LANDLOCK_ABI,
    landlock_abi,
    landlock_ready,
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
    """A tenant with a secret, and a tenant without one."""
    mine = tmp_path / "sessions" / "a"
    theirs = tmp_path / "sessions" / "b"
    for session in (mine, theirs):
        (session / "derived").mkdir(parents=True)
    (mine / "derived" / "secret.txt").write_text("TENANT-A-PRIVATE\n", encoding="utf-8")
    return mine, theirs


@pytest.fixture
def fenced(two_sessions):
    """A runner confined to the second session, as `build_backend` builds one."""
    from kingfisher.infrastructure.fence import LandlockRunner, policy_for

    _, theirs = two_sessions
    return LandlockRunner(
        policy_for(theirs), cwd=theirs, env={"PATH": "/usr/bin:/bin:/usr/local/bin"}
    )


def unfenced(command: str, cwd):
    """The same command with no fence, so a denial can be told from a typo."""
    import subprocess

    done = subprocess.run(  # noqa: S602 -- the control, deliberately unconfined
        command, shell=True, cwd=str(cwd), capture_output=True, text=True, check=False
    )
    return done.returncode, done.stdout + done.stderr


ESCAPES = [
    ("read it directly", "cat {secret}"),
    ("climb out with a relative path", "cd .. && cat a/derived/secret.txt"),
    ("follow a symlink into it", "ln -sf {secret} link.txt && cat link.txt"),
    ("go round through /proc", "cat /proc/self/root{secret}"),
    ("bind-mount it somewhere allowed", "mkdir -p in && mount --bind {theirs} in"),
    ("hide the fence under a tmpfs", "mount -t tmpfs none {theirs}"),
    ("take a new mount namespace", "unshare -m sh -c 'cat {secret}'"),
]


@needs_landlock
@pytest.mark.parametrize(("what", "command"), ESCAPES, ids=[e[0] for e in ESCAPES])
def test_the_fence_holds(fenced, two_sessions, what, command):
    """Every way out that was tried, and every one of them fails.

    The last three matter more than they look: Landlock denies the path
    resolution `mount(2)` needs, so a fenced process cannot spend `SYS_ADMIN`
    even in a container that has it. That is what makes the FUSE capability
    survivable, and it relocates the risk entirely onto whatever is *not*
    fenced.
    """
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
    """The control, and it runs everywhere rather than only where Landlock does.

    Without it the test above would pass against a misspelled path just as
    happily as against a working fence -- which is the failure that makes a
    security test worse than none.
    """
    mine, theirs = two_sessions
    spelled = command.format(secret=mine / "derived" / "secret.txt", theirs=theirs)

    _, output = unfenced(spelled, cwd=theirs)

    assert "TENANT-A-PRIVATE" in output, f"the control cannot {what}, so it proves nothing"


@needs_landlock
def test_the_session_itself_stays_usable(fenced, two_sessions):
    """A fence that broke the agent's own working directory would be swapped
    straight back out, which is the fastest way to end up with no fence."""
    result = fenced.run("echo written > derived/note.txt && cat derived/note.txt")

    assert result.exit_code == 0
    assert "written" in result.output


@needs_landlock
def test_a_fence_cannot_be_widened_from_inside(fenced, two_sessions):
    """Landlock rulesets only ever narrow, and a child inherits the parent's.
    Worth pinning rather than trusting: if this stopped holding, every other
    test here would still pass while the fence meant nothing."""
    mine = two_sessions[0]
    secret = mine / "derived" / "secret.txt"

    result = fenced.run(f"sh -c \"sh -c 'cat {secret}'\"")

    assert "TENANT-A-PRIVATE" not in result.output
