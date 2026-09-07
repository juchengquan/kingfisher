"""The Linux fence, and the policy it generates."""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from kingfisher.domain.ports import CommandResult
from kingfisher.infrastructure.sandbox.confinement import (
    REQUIRED_LANDLOCK_ABI,
    Confinement,
    landlock_ready,
)
from kingfisher.infrastructure.sandbox.fence import SYSTEM_PATHS, LandlockRunner, policy_for

#: A message short enough for `TRY003`, since what it says never survives
#: `subprocess` anyway -- see the test that uses it.
FAILED = "sandlock_create failed"


@dataclass
class StubSandbox:
    """`sandlock.Sandbox`'s constructor surface, as this repository uses it."""

    fs_readable: list = field(default_factory=list)
    fs_writable: list = field(default_factory=list)


@pytest.fixture
def sandlock(monkeypatch):
    """A `sandlock` importable enough for `policy_for`, on any platform."""
    module = types.ModuleType("sandlock")
    module.Sandbox = StubSandbox  # type: ignore[attr-defined]
    # A `confine` that confines nothing, so the runner's own half -- launching
    # the process, shaping the output -- is testable on a machine with no
    # Landlock. What the fence *does* is asserted against a kernel, in
    # `tests/linux/test_fence_escapes.py`.
    module.confine = lambda policy: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sandlock", module)
    return module


def a_policy(tmp_path, **kwargs: Any) -> StubSandbox:
    """A policy for a session that exists, because only those are generated."""
    session = tmp_path / "sessions" / "s1"
    session.mkdir(parents=True, exist_ok=True)
    return policy_for(session, **kwargs)


# -- what the policy says ---------------------------------------------------


def test_the_session_is_writable_and_nothing_above_it_is(sandlock, tmp_path):
    """The claim the fence exists to make."""
    policy = a_policy(tmp_path)

    assert str(tmp_path / "sessions" / "s1") in policy.fs_writable
    assert str(tmp_path / "sessions") not in policy.fs_writable
    assert str(tmp_path) not in policy.fs_writable


def test_another_session_appears_in_no_list_at_all(sandlock, tmp_path):
    """Not denied -- absent."""
    policy = a_policy(tmp_path)
    sibling = str(tmp_path / "sessions" / "s2")

    assert sibling not in policy.fs_writable
    assert sibling not in policy.fs_readable


def test_the_catalogue_is_readable_and_not_writable(sandlock, tmp_path):
    """Skills are workspace-level and their scripts are run by the shell against
    `$KINGFISHER_SKILLS`, so a fence that hid them would break the feature it is
    protecting.
    """
    catalogue = tmp_path / "skills"
    catalogue.mkdir()
    policy = a_policy(tmp_path, readable=[catalogue])

    assert str(catalogue) in policy.fs_readable
    assert str(catalogue) not in policy.fs_writable


def test_a_shell_can_still_be_a_shell(sandlock, tmp_path):
    """The fence is applied before `exec`, and Landlock denies by default -- so a policy
    without these cannot start `/bin/sh` at all, and the failure looks like a broken
    image rather than a denied path.
    """
    policy = a_policy(tmp_path)

    for path in SYSTEM_PATHS:
        if Path(path).exists():
            assert path in policy.fs_readable, path
    assert not set(SYSTEM_PATHS) & set(policy.fs_writable), "system paths are read-only"


def test_a_path_that_is_not_there_is_dropped_rather_than_named(sandlock, tmp_path):
    """Measured in a container, and it did not fail loudly."""
    policy = a_policy(tmp_path, readable=[tmp_path / "never-made"])

    assert str(tmp_path / "never-made") not in policy.fs_readable


def test_a_fence_that_could_not_be_built_says_so(sandlock, tmp_path, monkeypatch):
    """Measured in a container, and it did not fail loudly."""
    from kingfisher.infrastructure.sandbox.fence import LandlockRunner

    def refuse(_policy):
        raise OSError(FAILED)

    monkeypatch.setattr(sandlock, "confine", refuse)
    session = tmp_path / "sessions" / "s1"
    session.mkdir(parents=True)

    result = LandlockRunner(policy_for(session), cwd=session).run("echo hi")

    # Not the reason, because `subprocess` does not give one: it discards the
    # child's exception and reports "Exception occurred in preexec_fn." So the
    # message names the fence and says the command did not run, which is the
    # part that was previously invisible.
    assert "fence could not be applied" in result.output
    assert "did not run" in result.output
    assert result.exit_code != 0


# -- what the runner does with a command ------------------------------------


def a_runner(sandlock, tmp_path, **kwargs):
    from kingfisher.infrastructure.sandbox.fence import LandlockRunner

    session = tmp_path / "sessions" / "s1"
    session.mkdir(parents=True, exist_ok=True)
    kwargs.setdefault("env", {"PATH": "/usr/bin:/bin:/usr/local/bin"})
    return LandlockRunner(policy_for(session), cwd=session, **kwargs)


def test_a_command_runs_and_its_output_comes_back(sandlock, tmp_path):
    """The runner owns the process launch, so this is its own behaviour rather than a
    wrapper's.
    """
    result = a_runner(sandlock, tmp_path).run("echo hi")

    assert result.exit_code == 0
    assert result.output.strip() == "hi"


def test_a_failure_reaches_the_model_with_its_message(sandlock, tmp_path):
    """`stderr` is where a denied path is reported, and a fence whose refusals were
    invisible would look like a broken command.
    """
    result = a_runner(sandlock, tmp_path).run("cat /nope/definitely-not-here")

    assert result.exit_code != 0
    assert "not-here" in result.output


def test_the_command_runs_in_the_session(sandlock, tmp_path):
    """`confine` rejects a policy carrying `cwd`, so the working directory is the
    runner's business.
    """
    result = a_runner(sandlock, tmp_path).run("pwd")

    assert result.output.strip().endswith("sessions/s1")


def test_the_environment_is_given_rather_than_inherited(sandlock, tmp_path, monkeypatch):
    """The other thing `confine` rejects, and the one a filesystem fence would not have
    caught anyway.
    """
    monkeypatch.setenv("A_SERVICE_CREDENTIAL", "sk-do-not-leak")

    result = a_runner(sandlock, tmp_path).run("env")

    assert "sk-do-not-leak" not in result.output
    assert "PATH=" in result.output


def test_long_output_truncates_where_an_unfenced_command_would(sandlock, tmp_path):
    """A fence that changed how much output a turn could see would be a fence that
    changed the agent's behaviour, which is how a fence gets turned off.
    """
    result = a_runner(sandlock, tmp_path, max_output_bytes=50).run("printf 'x%.0s' $(seq 200)")

    assert result.truncated is True
    assert "truncated at 50 bytes" in result.output


def test_a_timeout_is_a_result_rather_than_an_exception(sandlock, tmp_path):
    """The shell's own exit code, so a caller cannot tell a fenced timeout from an
    unfenced one.
    """
    result = a_runner(sandlock, tmp_path).run("sleep 5", timeout=1)

    assert result.exit_code == 124
    assert "timed out" in result.output


def test_a_result_is_kingfisher_s_own_type(sandlock, tmp_path):
    """The seam's whole point: no framework type, and no `sandlock` type either, in
    something a deployment could implement.
    """
    assert isinstance(a_runner(sandlock, tmp_path).run("true"), CommandResult)


# -- when the fence is used at all -------------------------------------------


def test_a_named_mechanism_counts_as_confined_even_with_nothing_wrapped():
    """Landlock is applied to the process, not wrapped round the command, so "does
    `wrap` do anything" reports a fenced shell as unfenced.
    """
    from kingfisher.infrastructure.sandbox.confinement import _unwrapped

    assert Confinement(wrap=_unwrapped, mechanism="Landlock").confined
    assert not Confinement(wrap=_unwrapped).confined


def test_three_things_have_to_hold_and_any_one_fails_quietly(monkeypatch):
    """A deployment with two of the three would run unfenced while believing otherwise,
    so they are checked together in one place rather than assumed from the platform.
    """
    import kingfisher.infrastructure.sandbox.confinement as c

    monkeypatch.setattr(c.platform, "system", lambda: "Linux")
    monkeypatch.setattr(c, "landlock_abi", lambda: REQUIRED_LANDLOCK_ABI - 1)
    assert not landlock_ready(), "an ABI below the requirement is not ready"

    monkeypatch.setattr(c, "landlock_abi", lambda: None)
    assert not landlock_ready(), "no Landlock at all is not ready"


def test_the_fence_follows_the_confinement_rather_than_deciding_again(sandlock, cfg, tmp_path):
    """Two places answering "should this be fenced" would eventually disagree, and the
    failure is a shell running unfenced while `doctor` reports it confined.
    """
    from kingfisher.infrastructure.harness.backend import _fence_for
    from kingfisher.infrastructure.sandbox.confinement import _unwrapped

    session = tmp_path / "sessions" / "s1"
    session.mkdir(parents=True)
    unfenced = Confinement(wrap=_unwrapped, warning="nothing here")
    assert _fence_for(cfg, session, unfenced, None, {}) is None

    fenced = Confinement(wrap=_unwrapped, mechanism="Landlock")
    runner = _fence_for(cfg, session, fenced, None, {})
    assert isinstance(runner, LandlockRunner)
    assert str(session) in runner.policy.fs_writable


def test_both_fences_are_handed_the_same_paths(cfg, tmp_path, monkeypatch):
    """The two mechanisms must fence the same shell the same way."""
    from kingfisher.infrastructure.harness.backend import _fence_for
    from kingfisher.infrastructure.sandbox import bubblewrap, fence
    from kingfisher.infrastructure.sandbox.confinement import _unwrapped

    seen: dict[str, tuple[Path, list[Path], list[Path]]] = {}

    def recorder(mechanism, result):
        def fake(session_dir, *, readable=(), writable=()):
            seen[mechanism] = (session_dir, list(readable), list(writable))
            return result

        return fake

    monkeypatch.setattr(bubblewrap, "argv_for", recorder("bubblewrap", ["bwrap"]))
    monkeypatch.setattr(fence, "policy_for", recorder("Landlock", object()))

    session = tmp_path / "sessions" / "s1"
    session.mkdir(parents=True)
    skills = tmp_path / "skills"
    skills.mkdir()

    for mechanism in ("bubblewrap", "Landlock"):
        _fence_for(
            cfg, session, Confinement(wrap=_unwrapped, mechanism=mechanism), skills, {}
        )

    assert set(seen) == {"bubblewrap", "Landlock"}, f"a branch was not reached: {sorted(seen)}"
    assert seen["bubblewrap"] == seen["Landlock"], (
        "the two fences were handed different paths, so a shell is confined "
        f"differently depending on the host: {seen}"
    )
    # And that the paths are the ones meant, so agreeing on nothing would fail.
    _, readable, writable = seen["Landlock"]
    assert skills in readable, "the shared catalogue is what a skill's scripts are read from"
    assert writable == [cfg.scratch_dir], "$TMPDIR has to be writable or the first command fails"


def test_every_directory_on_the_agent_s_path_is_reachable(cfg, tmp_path, monkeypatch):
    """The rule the fence exists to keep, stated the way it actually breaks."""
    from kingfisher.infrastructure.harness.backend import _fence_for, shell_env
    from kingfisher.infrastructure.sandbox import bubblewrap, fence
    from kingfisher.infrastructure.sandbox.confinement import _unwrapped

    seen: dict[str, tuple[list[Path], list[Path]]] = {}

    def recorder(mechanism, result):
        def fake(session_dir, *, readable=(), writable=()):
            seen[mechanism] = ([Path(p) for p in readable], [Path(p) for p in writable])
            return result

        return fake

    monkeypatch.setattr(bubblewrap, "argv_for", recorder("bubblewrap", ["bwrap"]))
    monkeypatch.setattr(fence, "policy_for", recorder("Landlock", object()))

    session = tmp_path / "sessions" / "s1"
    session.mkdir(parents=True)
    env = shell_env(cfg, session)

    _fence_for(cfg, session, Confinement(wrap=_unwrapped, mechanism="Landlock"), None, env)
    readable, writable = seen["Landlock"]
    granted = [Path(p) for p in SYSTEM_PATHS] + readable + writable + [session]

    on_path = [Path(p) for p in env["PATH"].split(":") if p and Path(p).exists()]
    assert on_path, "nothing was checked -- PATH named no directory that exists"

    unreachable = [
        str(entry)
        for entry in on_path
        if not any(entry == root or root in entry.parents for root in granted)
    ]
    assert not unreachable, (
        f"{unreachable} are on the agent's PATH and inside nothing the fence grants. "
        "A fenced shell does not refuse them, it silently runs whatever it finds "
        "next -- see toolchain_roots"
    )
