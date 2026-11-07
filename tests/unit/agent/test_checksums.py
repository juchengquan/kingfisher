"""`GlobWithChecksums`: what it says of each file, and what it says when it cannot."""

from __future__ import annotations

import hashlib
import os
import subprocess
from typing import TYPE_CHECKING

import pytest
from deepagents.backends.protocol import ExecuteResponse

from kingfisher.core.agent.checksums import GlobWithChecksums

if TYPE_CHECKING:
    from pathlib import Path


class _Here(GlobWithChecksums):
    """A sandbox whose shell is this host's, so deepagents' listing script and the hashing
    script both run as written rather than as a stand-in for them."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.checksummed = (f"{root}/outputs/",)
        self.commands: list[str] = []

    @property
    def id(self) -> str:
        return "here"

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        self.commands.append(command)
        done = subprocess.run(  # noqa: S602 -- `execute` is a shell by definition
            command, shell=True, capture_output=True, text=True, timeout=10, check=False
        )
        return ExecuteResponse(output=done.stdout + done.stderr, exit_code=done.returncode)

    def hashed(self) -> list[str]:
        return [one for one in self.commands if "hashlib" in one]

    def upload_files(self, files):
        raise NotImplementedError

    def download_files(self, paths):
        raise NotImplementedError


def _outputs(tmp_path: Path) -> Path:
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    return outputs


def _checksums(sandbox: _Here, tmp_path: Path) -> dict[str, str | None]:
    listed = sandbox.glob("**", path=f"{tmp_path}/outputs/")
    assert listed.error is None
    return {
        one["path"].rsplit("/", 1)[-1]: one.get("checksum")
        for one in listed.matches or ()
    }


def test_a_rewrite_that_keeps_the_size_and_time_gets_a_new_checksum(tmp_path):
    """Told apart by size and time, a file rewritten to the same length and given its old
    time back, as `cp -p`, `rsync -a` and `tar` do, read as unchanged: the turn kept no copy,
    and a rewind to it put back the version before."""
    report = _outputs(tmp_path) / "report.md"
    report.write_bytes(b"aaaa")
    before = report.stat()
    sandbox = _Here(tmp_path)
    first = _checksums(sandbox, tmp_path)["report.md"]

    report.write_bytes(b"bbbb")
    os.utime(report, ns=(before.st_atime_ns, before.st_mtime_ns))

    assert (report.stat().st_size, report.stat().st_mtime_ns) == (
        before.st_size, before.st_mtime_ns
    )
    assert first == f"sha256:{hashlib.sha256(b'aaaa').hexdigest()}"
    assert _checksums(sandbox, tmp_path)["report.md"] == (
        f"sha256:{hashlib.sha256(b'bbbb').hexdigest()}"
    )


def _cut_in_the_last_line(sandbox: _Here, command: str) -> ExecuteResponse:
    whole = _Here.execute(sandbox, command).output
    return ExecuteResponse(output=whole[: whole.rindex("sha256") + 20], truncated=True)


def _gone_before_it_is_read(sandbox: _Here, command: str) -> ExecuteResponse:
    (sandbox.root / "outputs" / "b.md").unlink()
    return _Here.execute(sandbox, command)


def _a_pipe_since(sandbox: _Here, command: str) -> ExecuteResponse:
    (sandbox.root / "outputs" / "b.md").unlink()
    os.mkfifo(sandbox.root / "outputs" / "b.md")
    return _Here.execute(sandbox, command)


def _timed_out(sandbox: _Here, command: str) -> ExecuteResponse:
    raise TimeoutError


FAILURES = {
    "no python3": (lambda _, __: ExecuteResponse("sh: 1: python3: not found", 127), set()),
    "it raises": (_timed_out, set()),
    "garbage": (lambda _, __: ExecuteResponse('Traceback\n0\n{"path": "/x", "sha', 1), set()),
    "cut short": (_cut_in_the_last_line, {"a.md"}),
    "a file gone": (_gone_before_it_is_read, {"a.md"}),
    "a pipe since": (_a_pipe_since, {"a.md"}),
}


@pytest.mark.parametrize("failure", list(FAILURES))
def test_a_file_the_command_does_not_answer_is_listed_by_name(tmp_path, failure):
    """A hashing command that failed failed the listing, or named a file it never read, and
    a turn whose listing fails copies nothing at all; a file unanswered has to be listed as
    deepagents lists it, by name, which kingfisher reads back."""
    answer, answered = FAILURES[failure]

    class Failing(_Here):
        def execute(self, command, *, timeout=None):
            if "hashlib" not in command:
                return super().execute(command, timeout=timeout)
            return answer(self, command)

    outputs = _outputs(tmp_path)
    (outputs / "a.md").write_text("a", encoding="utf-8")
    (outputs / "b.md").write_text("b", encoding="utf-8")
    sandbox = Failing(tmp_path)

    got = _checksums(sandbox, tmp_path)

    assert set(got) == {"a.md", "b.md"}
    assert {name for name, checksum in got.items() if checksum} == answered


def test_a_glob_outside_the_folders_kingfisher_lists_hashes_nothing(tmp_path):
    """Hashed wherever it lists, the agent's own glob over a large tree runs past the ten
    seconds deepagents' glob tool allows it, and fails."""
    (_outputs(tmp_path) / "report.md").write_text("r", encoding="utf-8")
    (tmp_path / "code").mkdir()
    (tmp_path / "code" / "main.py").write_text("pass", encoding="utf-8")
    sandbox = _Here(tmp_path)

    listed = sandbox.glob("**", path=f"{tmp_path}/code/")

    assert [one.get("checksum") for one in listed.matches or ()] == [None]
    assert sandbox.hashed() == []
    # The control: under a folder kingfisher lists, the same call hashes.
    assert _checksums(sandbox, tmp_path)["report.md"]
    assert len(sandbox.hashed()) == 1


def test_many_files_are_hashed_in_commands_the_shell_takes(tmp_path):
    """Named all in one command, a folder of a few thousand files made an argument Linux
    refuses past 128 KB, and every file was read back across the network."""
    outputs = _outputs(tmp_path)
    for at in range(1200):
        (outputs / f"{at:04d}-{'x' * 60}.txt").write_text(str(at), encoding="utf-8")
    sandbox = _Here(tmp_path)

    got = _checksums(sandbox, tmp_path)

    assert len(got) == 1200
    assert all(got.values())
    assert len(sandbox.hashed()) > 1
    assert max(len(one) for one in sandbox.hashed()) < 128 * 1024
