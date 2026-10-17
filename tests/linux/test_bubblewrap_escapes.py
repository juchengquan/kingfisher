"""The escape list again, against the other fence."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from kingfisher.core.layout import DEFAULT_LAYOUT
from kingfisher.plugins.sessions.folders import ensure_session_layout
from kingfisher.plugins.sessions.sandbox.bubblewrap import (
    BubblewrapRunner,
    argv_for,
    bubblewrap_available,
)
from kingfisher.plugins.sessions.sandbox.confinement import toolchain_roots

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
        (session / "outputs").mkdir(parents=True)
    (mine / "outputs" / "secret.txt").write_text("TENANT-A-PRIVATE\n", encoding="utf-8")
    return mine, theirs


@pytest.fixture
def fenced(two_sessions):
    """A runner sandboxed to the second session, as `LocalSessions` builds one."""
    _, theirs = two_sessions
    return BubblewrapRunner(
        argv_for(theirs, DEFAULT_LAYOUT, readable=toolchain_roots()),
        env={"PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin:/usr/local/bin"},
    )


ESCAPES = [
    ("read it directly", "cat {secret}"),
    ("climb out with a relative path", "cd .. && cat a/outputs/secret.txt"),
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
            secret=mine / "outputs" / "secret.txt", sessions=theirs.parent
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
            secret=mine / "outputs" / "secret.txt", sessions=theirs.parent
        )

        done = subprocess.run(  # noqa: S602 -- the control, deliberately unsandboxed
            spelled, shell=True, cwd=str(theirs), capture_output=True, text=True, check=False
        )

        if "TENANT-A-PRIVATE" not in (done.stdout + done.stderr):
            blunt.append(f"the control cannot {what}, so it proves nothing")

    assert not blunt, "\n".join(blunt)


@needs_bubblewrap
def test_a_saved_result_is_the_shells_to_read_and_not_to_write(tmp_path):
    """A ledger row names its file and a later turn reads it back as what the tool
    returned, so a shell that can write there forges a result. Overwrite, add, link,
    remove, and rename the scratchpad away and back -- the last beat a rule matched by position
    on macOS, and here it asks whether the bind moves with the folder it sits in.

    The control runs first, because the rename probe is the one that could leave the
    scratchpad somewhere else if it got half-way.
    """
    session = ensure_session_layout(tmp_path / "sessions" / "s1", DEFAULT_LAYOUT)
    for folder in DEFAULT_LAYOUT.saved_results:
        (session / folder / "A-0001.txt").write_text("as written", encoding="utf-8")
    runner = BubblewrapRunner(
        argv_for(session, DEFAULT_LAYOUT, readable=toolchain_roots()),
        env={"PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin:/usr/local/bin"},
    )

    mine = runner.run("printf ok > scratchpad/mine.txt && cat scratchpad/mine.txt")
    read = runner.run(f"cat {DEFAULT_LAYOUT.saved_results[0]}/A-0001.txt")
    for folder in DEFAULT_LAYOUT.saved_results:
        runner.run(f"printf FORGED > {folder}/A-0001.txt")
        runner.run(f"printf FORGED > {folder}/A-0002.txt")
        runner.run(f"ln {folder}/A-0001.txt linked && printf FORGED > linked; rm -f linked")
        runner.run(f"rm -rf {folder}")
    runner.run(
        "mv scratchpad moved && printf FORGED > moved/tool_results/A-0001.txt; mv moved scratchpad"
    )

    assert mine.output.strip() == "ok", f"the scratchpad stopped being writable: {mine.output}"
    assert read.output.strip() == "as written", f"a saved result stopped reading: {read.output}"
    for folder in DEFAULT_LAYOUT.saved_results:
        assert (session / folder / "A-0001.txt").read_text(encoding="utf-8") == "as written"
        assert not (session / folder / "A-0002.txt").exists(), f"the shell added to {folder}"


@needs_bubblewrap
def test_the_session_itself_stays_usable(fenced):
    """A sandbox that broke the agent's own working directory would be swapped straight
    back out -- and, as the Landlock work found, a fence that cannot run any command
    passes every escape test while protecting nothing.
    """
    result = fenced.run("echo written > outputs/note.txt && cat outputs/note.txt")

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
