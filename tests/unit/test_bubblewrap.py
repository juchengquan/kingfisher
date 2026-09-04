"""The second Linux fence, and the sandbox it builds.

Everything here is about the *argv*, which is where this repository's decisions
live: which paths are bound, which are read-only, and that the network is closed.
What the kernel then does with it is asserted in `tests/linux/`, which needs a
container with its syscall filter relaxed.

The split matters more here than it did for Landlock. A bubblewrap policy is a
list of binds, and a bind that should not be there is invisible in behaviour
until the day two tenants share a host.
"""

from __future__ import annotations

import pytest

from kingfisher.infrastructure.sandbox.bubblewrap import SYSTEM_PATHS, BubblewrapRunner, argv_for


@pytest.fixture
def session(tmp_path):
    directory = tmp_path / "sessions" / "s1"
    directory.mkdir(parents=True)
    return directory


def pairs(argv: list[str], flag: str) -> set[tuple[str, str]]:
    """The (source, destination) pairs bound with `flag`."""
    return {
        (argv[i + 1], argv[i + 2]) for i, word in enumerate(argv) if word == flag
    }


def test_the_session_is_the_only_thing_written_to(session, tmp_path):
    """The claim the fence exists to make, and the one a stray `--bind` would
    quietly undo."""
    argv = argv_for(session)

    assert (str(session), str(session)) in pairs(argv, "--bind")
    assert not any(src == str(tmp_path) for src, _ in pairs(argv, "--bind"))
    assert not any(src == str(session.parent) for src, _ in pairs(argv, "--bind"))


def test_another_session_is_not_bound_at_all(session):
    """Not denied -- absent. That is the difference from Landlock worth
    knowing: a session created *after* this argv was built is unreachable for
    the same reason as one created before, because reachability is the mount
    namespace rather than a rule."""
    sibling = str(session.parent / "s2")
    argv = argv_for(session)

    assert not any(sibling in word for word in argv)


def test_the_catalogue_is_bound_read_only(session, tmp_path):
    """Skills are workspace-level and their scripts are run by the shell, so a
    sandbox that hid them would break the feature it protects. Writable and the
    agent could rewrite a skill for every later request in every session."""
    catalogue = tmp_path / "skills"
    catalogue.mkdir()
    argv = argv_for(session, readable=[catalogue])

    assert (str(catalogue), str(catalogue)) in pairs(argv, "--ro-bind")
    assert not any(src == str(catalogue) for src, _ in pairs(argv, "--bind"))


def test_the_network_is_closed_and_is_not_a_separate_choice(session):
    """`--unshare-all` includes the network namespace. Deliberately not its own
    setting: choosing bubblewrap is already an assertion about an unusual
    container, and one more property in that assertion is cheaper than another
    axis for `doctor` to describe honestly."""
    assert "--unshare-all" in argv_for(session)


def test_proc_is_not_bound(session):
    """Measured with a token generated at runtime: through a bound `/proc` a
    sandboxed shell reads *other processes' command lines*. `/proc/1/environ`
    was denied either way by the user namespace mapping, but a command line is
    enough.

    A fresh `proc` cannot be mounted here -- Docker's masked paths inside
    `/proc` are locked mounts and the kernel refuses a new one that would hide
    them -- so having none is the only remaining answer.
    """
    argv = argv_for(session)

    assert "/proc" not in SYSTEM_PATHS
    assert not any(word == "/proc" for word in argv), "no bind, and no --proc"


def test_dev_is_built_rather_than_bound(session):
    """`--dev` makes a fresh minimal one. Binding the container's would hand the
    sandbox every device node the container can see."""
    argv = argv_for(session)

    assert "--dev" in argv
    assert not any(src == "/dev" for src, _ in pairs(argv, "--ro-bind"))


def test_a_path_that_is_not_there_is_dropped(session, tmp_path):
    """`bwrap` refuses a bind whose source is absent, and `/lib64` is missing on
    arm64 Debian -- the same finding that stopped the Landlock policy building,
    arriving through a different mechanism."""
    argv = argv_for(session, readable=[tmp_path / "never-made"])

    assert not any("never-made" in word for word in argv)


def test_the_command_starts_in_the_session(session):
    """`--chdir` rather than `subprocess`'s `cwd`, because the working directory
    has to be a path that exists *inside* the sandbox."""
    argv = argv_for(session)

    assert argv[argv.index("--chdir") + 1] == str(session)


# -- what the runner does ----------------------------------------------------


def test_the_runner_puts_the_command_after_the_sandbox(session):
    """Everything before `/bin/sh` is policy; the command is the last word. A
    command that leaked into the argv earlier would be arguments to `bwrap`."""
    runner = BubblewrapRunner(["bwrap", "--unshare-all", "--chdir", str(session)])
    seen: list[list[str]] = []

    class Done:
        stdout, stderr, returncode = "ok", "", 0

    def fake_run(argv, **kwargs):
        seen.append(argv)
        return Done()

    import kingfisher.infrastructure.sandbox.bubblewrap as module

    original, module.subprocess.run = module.subprocess.run, fake_run
    try:
        runner.run("echo hi")
    finally:
        module.subprocess.run = original

    assert seen[0][-3:] == ["/bin/sh", "-c", "echo hi"]


def test_the_runner_says_it_is_local(session):
    """The command runs on this machine, so kingfisher's own confinement still
    applies beforehand. On Linux that wrap is the identity, which is why nothing
    is applied twice."""
    assert BubblewrapRunner(["bwrap"]).local is True


# -- when `auto` reaches for it ---------------------------------------------


def a_linux_host(monkeypatch, *, landlock: bool, bwrap: bool):
    """A Linux host with either fence available, or neither."""
    import kingfisher.infrastructure.sandbox.bubblewrap as bwrap_module
    from kingfisher.infrastructure.sandbox import confinement

    monkeypatch.setattr(confinement.platform, "system", lambda: "Linux")
    monkeypatch.setattr(confinement, "landlock_ready", lambda: landlock)
    monkeypatch.setattr(bwrap_module, "bubblewrap_available", lambda: bwrap)
    return confinement


def test_landlock_is_preferred_where_it_runs(monkeypatch):
    """It costs nothing -- no capability, no container change, no relaxed
    syscall filter -- and it denies the path resolution `mount(2)` needs, so a
    fenced process cannot spend `SYS_ADMIN`. bubblewrap grants that same
    capability inside its own user namespace, which is the opposite trade."""
    confinement = a_linux_host(monkeypatch, landlock=True, bwrap=True)

    assert confinement._linux().mechanism == "Landlock"


def test_bubblewrap_takes_over_where_landlock_cannot_run(monkeypatch):
    """The gap this closes, and it is not a small one: below the ABI a full
    ruleset needs -- 6.12, where EKS nodes are commonly on 6.1 -- the shell read
    every session's files and `doctor` said so. Something beats that."""
    confinement = a_linux_host(monkeypatch, landlock=False, bwrap=True)
    confined = confinement._linux()

    assert confined.mechanism == "bubblewrap"
    assert confined.confined


def test_neither_available_still_warns_rather_than_pretending(monkeypatch):
    """The case a fallback must not swallow. Two fences that both cannot run is
    an unconfined shell, and saying so is the whole reason the check exists."""
    confinement = a_linux_host(monkeypatch, landlock=False, bwrap=False)
    confined = confinement._linux()

    assert not confined.confined
    assert confined.warning
