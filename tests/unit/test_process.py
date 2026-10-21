"""How a shell command runs on this host, whichever fence is round it."""

from __future__ import annotations

import sys
import time
import tracemalloc

import pytest
from deepagents.backends import LocalShellBackend

from kingfisher.plugins.sessions.sandbox.process import run_command

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")

ENV = {"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8"}

#: One of each shape upstream gives a finished command.
SHAPES = [
    "printf 'one\\ntwo\\n'",
    "printf out; printf 'bad\\nworse\\n' >&2",
    "printf '  padded  \\n' >&2",
    "true",
    "printf partial; exit 3",
    "printf 'x%.0s' $(seq 300)",
]


@pytest.mark.parametrize("command", SHAPES)
def test_a_finished_command_reads_as_upstream_reads_it(command, tmp_path):
    """macOS turns ran through deepagents' `execute` until this launcher replaced it, so
    any difference here is a change in what the model sees.

    Against upstream itself rather than a copy of its strings, which would agree with
    this launcher however both drifted.
    """
    upstream = LocalShellBackend(root_dir=str(tmp_path), env=ENV, max_output_bytes=120)

    ours = run_command(command, env=ENV, timeout=10, cwd=str(tmp_path), max_output_bytes=120)
    theirs = upstream.execute(command, timeout=10)

    assert (ours.output, ours.exit_code, ours.truncated) == (
        theirs.output,
        theirs.exit_code,
        theirs.truncated,
    )


def test_a_timeout_reads_as_upstream_reads_one(tmp_path):
    """The wording `execute` gave on macOS, where the timeout was always passed."""
    upstream = LocalShellBackend(root_dir=str(tmp_path), env=ENV)

    ours = run_command("sleep 5", env=ENV, timeout=1, cwd=str(tmp_path))
    theirs = upstream.execute("sleep 5", timeout=1)

    assert (ours.output, ours.exit_code) == (theirs.output, theirs.exit_code)


def test_output_is_held_only_up_to_the_cap():
    """`yes` for three seconds was 2.87 GB held whole before the cut, in the process
    serving every session; read as it arrives, a chatty command costs the cap."""
    tracemalloc.start()
    try:
        result = run_command(
            "head -c 20000000 /dev/zero | tr '\\0' x", env=ENV, timeout=30, max_output_bytes=100
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert result.truncated is True
    assert peak < 2_000_000, f"held {peak} bytes for a 100-byte cap"


def test_a_process_holding_the_output_open_does_not_hold_the_command():
    """The shell has finished; what it left in the background is killed then, rather
    than waited for until the timeout because it still holds the pipe."""
    started = time.monotonic()

    result = run_command("sleep 30 & echo started", env=ENV, timeout=20)

    assert result.output.strip() == "started"
    assert time.monotonic() - started < 5


def test_the_environment_is_only_what_was_given(monkeypatch):
    """Nothing of this process's reaches the command, credentials least of all."""
    monkeypatch.setenv("A_SERVICE_CREDENTIAL", "sk-do-not-leak")

    result = run_command("env", env={"PATH": "/usr/bin:/bin"}, timeout=10)

    assert "sk-do-not-leak" not in result.output
    assert "PATH=/usr/bin:/bin" in result.output


def test_a_launch_that_fails_names_the_fence(tmp_path):
    """A fence that never started reads as a broken image unless it says so."""
    result = run_command(
        [str(tmp_path / "absent")], env=ENV, timeout=10, unstartable="the fence failed"
    )

    assert result.exit_code == 1
    assert result.output.startswith("[fence] the command did not run: the fence failed")


# -- asked before it is stopped --------------------------------------------------

#: A command that, asked to stop with SIGTERM, leaves a mark and goes.
ASKED = (
    "import pathlib, signal, sys, time\n"
    "signal.signal(signal.SIGTERM, lambda *_: (pathlib.Path(sys.argv[1]).touch(), sys.exit(0)))\n"
    "time.sleep(30)\n"
)


@pytest.mark.parametrize(("grace", "asked"), [(0.5, True), (None, False)])
def test_a_command_given_grace_is_asked_to_stop_before_it_is_killed(tmp_path, grace, asked):
    """The reaper cleans up when asked; killed outright, what it adopted goes to init."""
    mark = tmp_path / "asked"

    result = run_command(
        [sys.executable, "-c", ASKED, str(mark)], env=ENV, timeout=2, grace=grace
    )

    assert result.exit_code == 124
    assert mark.exists() is asked


def test_a_stop_given_grace_asks_first_too(tmp_path):
    import threading

    mark = tmp_path / "asked"
    stop = threading.Event()
    threading.Timer(0.3, stop.set).start()
    started = time.monotonic()

    run_command(
        [sys.executable, "-c", ASKED, str(mark)], env=ENV, timeout=20, grace=0.5, stop=stop
    )

    assert mark.exists()
    assert time.monotonic() - started < 5, "asked at its deadline, not when stopped"


@pytest.mark.parametrize(
    ("command", "answer"),
    [("printf hi; exit 3", ("hi", 3)), ("kill -9 $$", (None, -9))],
)
def test_through_the_reaper_a_command_reads_as_it_would_without_it(command, answer):
    """Where it adopts, on Linux, and where it cannot and steps aside: the same output and
    the same exit, a signal's included."""
    from kingfisher.plugins.sessions.sandbox.fence import reaper

    plain = run_command(command, env=ENV, timeout=10)
    reaped = run_command([sys.executable, "-I", "-c", reaper(), command], env=ENV, timeout=10)

    assert (reaped.output, reaped.exit_code) == (plain.output, plain.exit_code)
    if answer[0] is not None:
        assert answer[0] in reaped.output
    assert reaped.exit_code == answer[1]
