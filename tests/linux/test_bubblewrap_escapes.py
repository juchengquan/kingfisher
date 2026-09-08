"""The escape list again, against the other fence."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from kingfisher.infrastructure.sandbox.bubblewrap import (
    BubblewrapRunner,
    argv_for,
    bubblewrap_available,
)
from kingfisher.infrastructure.sandbox.confinement import toolchain_roots

needs_bubblewrap = pytest.mark.skipif(
    not bubblewrap_available(),
    reason="no bwrap, or this container cannot create a user namespace",
)


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
    """A runner sandboxed to the second session, as `build_backend` builds one."""
    _, theirs = two_sessions
    return BubblewrapRunner(
        argv_for(theirs, readable=toolchain_roots()),
        env={"PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin:/usr/local/bin"},
    )


ESCAPES = [
    ("read it directly", "cat {secret}"),
    ("climb out with a relative path", "cd .. && cat a/derived/secret.txt"),
    ("follow a symlink into it", "ln -sf {secret} link.txt && cat link.txt"),
    ("list what else is there", "ls {sessions}"),
]


@needs_bubblewrap
def test_the_sandbox_holds(fenced, two_sessions):
    """Every way out that was tried.

    One runner for all of them, and it is the failure of every case that makes that
    safe: what an escape manages before it fails is a symlink or an empty directory
    inside the session it is already allowed to write, and no later case reads either.
    """
    assert ESCAPES, "no escapes listed -- this walks nothing and passes"
    mine, theirs = two_sessions
    leaked = []
    for what, command in ESCAPES:
        spelled = command.format(
            secret=mine / "derived" / "secret.txt", sessions=theirs.parent
        )

        result = fenced.run(spelled)

        if "TENANT-A-PRIVATE" in result.output:
            leaked.append(f"the sandbox let a tenant {what}")

    assert not leaked, "\n".join(leaked)


def test_each_escape_works_when_nothing_is_sandboxing_it(two_sessions):
    """The control, and it runs everywhere.

    The first three need no `SYS_ADMIN` and so mount nothing: two read and the third
    overwrites its own symlink, which is why one pair of sessions serves all three.
    """
    portable = ESCAPES[:3]
    assert portable, "no portable escapes -- this control proves nothing"
    mine, theirs = two_sessions
    blunt = []
    for what, command in portable:
        spelled = command.format(
            secret=mine / "derived" / "secret.txt", sessions=theirs.parent
        )

        done = subprocess.run(  # noqa: S602 -- the control, deliberately unsandboxed
            spelled, shell=True, cwd=str(theirs), capture_output=True, text=True, check=False
        )

        if "TENANT-A-PRIVATE" not in (done.stdout + done.stderr):
            blunt.append(f"the control cannot {what}, so it proves nothing")

    assert not blunt, "\n".join(blunt)


@needs_bubblewrap
def test_the_session_itself_stays_usable(fenced):
    """A sandbox that broke the agent's own working directory would be swapped straight
    back out -- and, as the Landlock work found, a fence that cannot run any command
    passes every escape test while protecting nothing.
    """
    result = fenced.run("echo written > derived/note.txt && cat derived/note.txt")

    assert result.exit_code == 0
    assert "written" in result.output


@needs_bubblewrap
def test_an_interpreter_still_works_without_proc(fenced):
    """`/proc` is deliberately absent, and Python reads it at startup on some platforms."""
    result = fenced.run("python3 -c 'print(6*7)'")

    assert result.exit_code == 0
    assert "42" in result.output


@needs_bubblewrap
def test_the_network_is_closed(fenced):
    """The property Landlock does not have, and the reason this mode exists alongside
    it.
    """
    result = fenced.run(
        "python3 -c \"import socket;socket.create_connection(('1.1.1.1',53),3);print('NET-OPEN')\""
    )

    assert "NET-OPEN" not in result.output
    assert result.exit_code != 0


@needs_bubblewrap
def test_the_agent_reaches_its_own_interpreter(fenced):
    """The promise `pyproject.toml` makes, held against a real kernel."""
    result = fenced.run('python3 -c "import sys, yaml; print(sys.prefix)"')

    assert result.exit_code == 0, f"the agent could not run Python: {result.output}"
    assert sys.prefix in result.output, (
        f"the fenced shell reached a different interpreter: {result.output.strip()}"
    )
