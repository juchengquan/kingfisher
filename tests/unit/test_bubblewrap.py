"""The second Linux fence, and the sandbox it builds."""

from __future__ import annotations

import subprocess

import pytest

from kingfisher.infrastructure.sandbox.bubblewrap import SYSTEM_PATHS, BubblewrapRunner, argv_for
from kingfisher.infrastructure.sandbox.linux import present
from kingfisher.infrastructure.workspace import ensure_session_layout
from kingfisher.layout import HARNESS


@pytest.fixture
def session(tmp_path):
    """A session built the way a turn builds one, not by hand.

    `argv_for` passes every bind through `present`, which drops a source that is not
    there -- so a hand-made directory silently removes the `.harness` bind from every
    assertion in this file, and the line that makes it never runs. Measured: with
    `mkdir` alone the whole suite stayed green with that bind deleted.
    """
    return ensure_session_layout(tmp_path / "sessions" / "s1")


def pairs(argv: list[str], flag: str) -> set[tuple[str, str]]:
    """The (source, destination) pairs bound with `flag`."""
    return {
        (argv[i + 1], argv[i + 2]) for i, word in enumerate(argv) if word == flag
    }


def chosen(argv: list[str]) -> set[tuple[str, str]]:
    """The read-only binds this fence chose, less the host's own system paths.

    Subtracted rather than listed, because which of `/usr`, `/lib64` and the rest
    exist is the host's business -- `test_no_system_path_bound_here_holds_a_session`
    is what holds that half.
    """
    return pairs(argv, "--ro-bind") - {(path, path) for path in present(SYSTEM_PATHS)}


def index_of(argv: list[str], flag: str, source: str) -> int:
    """Where `argv` binds `source` with `flag`. Order is a rule here, not a detail."""
    return next(i for i, word in enumerate(argv) if word == flag and argv[i + 1] == source)


def test_the_session_is_the_only_thing_written_to(session):
    """The claim the fence exists to make, and the one a stray `--bind` would quietly
    undo.

    The whole writable set, not a list of paths that must be absent from it. Written
    the second way this named `tmp_path` and the sibling session, and passed while
    `--bind / /` handed over the entire host: a mutation adding it was noticed by
    nothing in the suite, because the host root is neither of the two paths named.
    """
    argv = argv_for(session)

    assert pairs(argv, "--bind") == {(str(session), str(session))}


def test_another_session_is_not_bound_at_all(session):
    """Not denied -- absent."""
    sibling = str(session.parent / "s2")
    argv = argv_for(session)

    assert not any(sibling in word for word in argv)


def test_the_harness_is_read_only_and_lands_over_the_session(session):
    """What kingfisher keeps about a session is the shell's to read and not to write,
    and the bind that says so only works where it falls *after* the session's own.

    Both halves are asserted because both were unguarded: deleting the bind, and moving
    it above the session bind -- which the comment beside it says "would cover this
    again" -- each left the whole suite green.
    """
    argv = argv_for(session)
    harness = str(session / HARNESS)

    assert chosen(argv) == {(harness, harness)}
    assert index_of(argv, "--ro-bind", harness) > index_of(argv, "--bind", str(session))


def test_no_system_path_bound_here_holds_a_session(session):
    """`/usr`, `/lib` and the rest are the host's, and none of them may be a parent of
    the session: bound read-only, such a path covers the writable bind underneath it,
    and the set is what a reader would otherwise have to check by eye.

    Driven over the constant rather than a copy of it, so a path added there is checked
    rather than trusted. Measured: adding `/` to it made the whole host readable inside
    the sandbox and nothing in the suite noticed.
    """
    for path in SYSTEM_PATHS:
        assert not session.is_relative_to(path), f"{path} holds the session"


def test_the_catalogue_is_bound_read_only(session, tmp_path):
    """Skills are workspace-level and their scripts are run by the shell, so a sandbox
    that hid them would break the feature it protects.
    """
    catalogue = tmp_path / "skills"
    catalogue.mkdir()
    argv = argv_for(session, readable=[catalogue])

    assert (str(catalogue), str(catalogue)) in pairs(argv, "--ro-bind")
    assert not any(src == str(catalogue) for src, _ in pairs(argv, "--bind"))


def test_the_network_is_closed_and_is_not_a_separate_choice(session):
    """`--unshare-all` includes the network namespace."""
    assert "--unshare-all" in argv_for(session)


def test_proc_is_not_bound(session):
    """Measured with a token generated at runtime: through a bound `/proc` a sandboxed
    shell reads *other processes' command lines*.
    """
    argv = argv_for(session)

    assert "/proc" not in SYSTEM_PATHS
    assert not any(word == "/proc" for word in argv), "no bind, and no --proc"


def test_dev_is_built_rather_than_bound(session):
    """`--dev` makes a fresh minimal one."""
    argv = argv_for(session)

    assert "--dev" in argv
    assert not any(src == "/dev" for src, _ in pairs(argv, "--ro-bind"))


def test_a_path_that_is_not_there_is_dropped(session, tmp_path):
    """`bwrap` refuses a bind whose source is absent, and `/lib64` is missing on arm64
    Debian -- the same finding that stopped the Landlock policy building, arriving
    through a different mechanism.
    """
    argv = argv_for(session, readable=[tmp_path / "never-made"])

    assert not any("never-made" in word for word in argv)


def test_the_command_starts_in_the_session(session):
    """`--chdir` rather than `subprocess`'s `cwd`, because the working directory has to
    be a path that exists *inside* the sandbox.
    """
    argv = argv_for(session)

    assert argv[argv.index("--chdir") + 1] == str(session)


# -- what the runner does ----------------------------------------------------


def test_the_runner_puts_the_command_after_the_sandbox(session):
    """Everything before `/bin/sh` is policy; the command is the last word."""
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


def test_the_environment_is_given_rather_than_inherited(session, monkeypatch):
    """This process's credentials must not reach the agent's shell -- the one thing a
    filesystem fence would not catch, and the reason the runner keeps an `env` at all.

    Asserted at the `subprocess` boundary rather than by running `env`, because `bwrap`
    is absent on this host; `tests/linux/test_bubblewrap_escapes.py` would be the place
    to drive it, and no CI job runs that file. The Landlock runner has had
    `test_the_environment_is_given_rather_than_inherited` since it was written, and
    making this one inherit `os.environ` left the whole suite green.
    """
    monkeypatch.setenv("A_SERVICE_CREDENTIAL", "sk-do-not-leak")
    handed: list[object] = []

    def fake_run(argv, **kwargs):
        handed.append(kwargs.get("env"))
        return Done(stdout="ok")

    import kingfisher.infrastructure.sandbox.linux as module

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    BubblewrapRunner(argv_for(session), env={"PATH": "/usr/bin"}).run("env")
    # The empty case too, and it is the one that matters: `dict(env or ...)` returns
    # what it was given whenever that is truthy, so a runner built with no `env` is
    # the only call that can fall back to this process's. Written the other way
    # first, and a mutation inheriting `os.environ` passed it.
    BubblewrapRunner(argv_for(session)).run("env")

    assert handed == [{"PATH": "/usr/bin"}, {}]


def _launching(monkeypatch, outcome):
    """A `subprocess.run` that answers `outcome`, whatever it is handed.

    The runner's own half -- what a command comes back as -- is what these drive, and
    it is reachable on any host: `bwrap` never runs. What the sandbox *does* is
    asserted against a real one in `tests/linux/test_bubblewrap_escapes.py`.
    """
    import kingfisher.infrastructure.sandbox.linux as module

    def fake_run(*args, **kwargs):
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(module.subprocess, "run", fake_run)


class Done:
    """`CompletedProcess`, as the runner reads it."""

    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


def test_long_output_truncates_where_an_unfenced_command_would(session, monkeypatch):
    """Untested until the shaping moved: this runner had its own copy of it, identical
    to the Landlock one to the byte, and only that one was ever driven.
    """
    _launching(monkeypatch, Done(stdout="x" * 200))

    result = BubblewrapRunner(["bwrap"], max_output_bytes=50).run("anything")

    assert result.truncated is True
    assert "truncated at 50 bytes" in result.output


def test_both_streams_reach_the_model(session, monkeypatch):
    """`stderr` is where a denied path is reported, so a sandbox whose refusals were
    invisible would look like a broken command.
    """
    _launching(monkeypatch, Done(stdout="out", stderr="denied", returncode=1))

    result = BubblewrapRunner(["bwrap"]).run("anything")

    assert result.output == "outdenied"
    assert result.exit_code == 1


def test_a_timeout_is_a_result_rather_than_an_exception(session, monkeypatch):
    """The shell's own exit code, so a caller cannot tell a sandboxed timeout from an
    unsandboxed one.
    """
    _launching(monkeypatch, subprocess.TimeoutExpired(cmd="bwrap", timeout=1))

    result = BubblewrapRunner(["bwrap"]).run("sleep 5", timeout=1)

    assert result.exit_code == 124
    assert "timed out" in result.output


def test_a_sandbox_that_will_not_start_says_so_rather_than_looking_empty(
    session, monkeypatch
):
    """`bwrap` failing to build a namespace used to arrive as an exit code with no
    output, which is what a command with no output looks like -- so a sandbox that
    never applied read as a broken image.
    """
    _launching(monkeypatch, OSError("no user namespaces"))

    result = BubblewrapRunner(["bwrap"]).run("anything")

    assert result.exit_code == 1
    assert "[fence] the command did not run" in result.output
    assert "bubblewrap failed" in result.output
    assert "no user namespaces" in result.output


def test_the_runner_says_it_is_local(session):
    """The command runs on this machine, so kingfisher's own confinement still applies
    beforehand.
    """
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
    """It costs nothing -- no capability, no container change, no relaxed syscall filter
    -- and it denies the path resolution `mount(2)` needs, so a fenced process cannot
    spend `SYS_ADMIN`.
    """
    confinement = a_linux_host(monkeypatch, landlock=True, bwrap=True)

    assert confinement._linux().mechanism == "Landlock"


def test_bubblewrap_takes_over_where_landlock_cannot_run(monkeypatch):
    """The gap this closes, and it is not a small one: below the ABI a full ruleset
    needs -- 6.12, where EKS nodes are commonly on 6.1 -- the shell read every
    session's files and `doctor` said so.
    """
    confinement = a_linux_host(monkeypatch, landlock=False, bwrap=True)
    confined = confinement._linux()

    assert confined.mechanism == "bubblewrap"
    assert confined.confined


def test_neither_available_still_warns_rather_than_pretending(monkeypatch):
    """The case a fallback must not swallow."""
    confinement = a_linux_host(monkeypatch, landlock=False, bwrap=False)
    confined = confinement._linux()

    assert not confined.confined
    assert confined.warning
