"""The throwaway folder a scratch run happens in, reached only through descriptors.

Kingfisher's own process writes the inputs and reads the outputs here, outside every
fence, in a folder the agent's shell can also write. Every step goes through a descriptor
held from the session down, opened refusing links, so a folder swapped for a link mid-run
yields missing outputs rather than a read or a write somewhere else.
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import stat
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path
    from types import TracebackType

_LOG = logging.getLogger(__name__)

#: Whether this host opens a name relative to a held folder, which every step below rests
#: on. Windows does not, and `run_in_scratch` refuses there.
HELD = os.open in os.supports_dir_fd

# 0 where the host lacks one, which is only where `HELD` is false and no open below runs.
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_FOLDER = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | _NOFOLLOW | _CLOEXEC
_INPUT = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC
# Not blocking, or a FIFO left under an output's name would hold the turn on its read.
_OUTPUT = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | _NOFOLLOW | _CLOEXEC

#: What one run's outputs may come to, read into kingfisher's own process.
MAX_BROUGHT_BACK = 32 * 1024 * 1024


def plain(name: str) -> bool:
    """One file name, so a descriptor opened from the run's folder cannot reach past it."""
    return bool(name) and not name.startswith(".") and "/" not in name and "\0" not in name


class ScratchFolder:
    """A new folder in one session's scratchpad, removed when the run is done."""

    def __init__(self, session: Path, scratchpad: str) -> None:
        self.name = f".run-{uuid.uuid4().hex}"
        self.path = session / scratchpad / self.name
        top = os.open(session, _FOLDER)
        try:
            self._parent = os.open(scratchpad, _FOLDER, dir_fd=top)
        finally:
            os.close(top)
        try:
            os.mkdir(self.name, 0o700, dir_fd=self._parent)
            self._fd = os.open(self.name, _FOLDER, dir_fd=self._parent)
        except OSError:
            os.close(self._parent)
            raise

    def __enter__(self) -> ScratchFolder:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        try:
            shutil.rmtree(self.name, dir_fd=self._parent)
        except OSError as left:
            # Swapped for a link or emptied by the command: what is there is the shell's.
            _LOG.warning("a scratch run's folder %s was left behind: %s", self.path, left)
        finally:
            os.close(self._fd)
            os.close(self._parent)

    def write(self, files: Mapping[str, bytes]) -> None:
        """Each file as new, so nothing the shell put there first is written through."""
        for name, data in files.items():
            fd = os.open(name, _INPUT, 0o600, dir_fd=self._fd)
            with os.fdopen(fd, "wb") as written:
                written.write(data)

    def read(self, outputs: Sequence[str]) -> tuple[dict[str, bytes], dict[str, str]]:
        """The outputs that came back, and why each other one did not."""
        got: dict[str, bytes] = {}
        missing: dict[str, str] = {}
        left = MAX_BROUGHT_BACK
        for name in outputs:
            try:
                fd = os.open(name, _OUTPUT, dir_fd=self._fd)
            except FileNotFoundError:
                missing[name] = "the command did not make it"
                continue
            except OSError as refused:
                missing[name] = f"not a file that can be read: {refused.strerror}"
                continue
            with os.fdopen(fd, "rb") as opened:
                # Judged on the open file, which a rename cannot change under the read.
                found = os.fstat(opened.fileno())
                if not stat.S_ISREG(found.st_mode):
                    missing[name] = "not a regular file"
                    continue
                data = opened.read(left + 1)
            if len(data) > left:
                missing[name] = (
                    f"past the {MAX_BROUGHT_BACK} bytes one run may bring back"
                )
                continue
            left -= len(data)
            got[name] = data
        return got, missing


def scratch_command(argv: Sequence[str], folder: Path, env: Mapping[str, str]) -> str:
    """`argv` run in `folder` with `env` and nothing else, as one line a fence can wrap."""
    assignments = [f"{key}={value}" for key, value in env.items()]
    run = shlex.join(["/usr/bin/env", "-i", *assignments, *argv])
    return f"cd {shlex.quote(str(folder))} && exec {run}"
