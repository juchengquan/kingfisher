"""Reading what a workspace is sitting on, as far as the platform will say.

One record and the four reads that fill it: the filesystem type under the
workspace, its size, the cgroup's memory limit, and whether swap is permitted.
`MemoryBacking` carries what those four are for, and why the arrangement is not
what the obvious reading of them predicts.

`presentation/cli/health.py` is what turns them into a verdict. Nothing here
decides anything, which is why it is four reads and a dataclass.
"""

from __future__ import annotations

import os
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MemoryBacking:
    """What is underneath a workspace, for a deployment that must keep nothing.

    Facts rather than a verdict. `kingfisher doctor` decides what they mean; this
    reads them, because a deployment that has asserted "nothing at rest on this
    machine" is relying on a configuration nothing in the process can see.

    Measured, and the arrangement is not what the obvious reading predicts. A
    memory filesystem *larger* than the container's memory limit does not fail
    when it fills: the kernel swaps its pages out. That is data at rest, arrived
    at silently, with the write succeeding and no error anywhere. With swap off
    the same overrun becomes an OOM kill, which takes every session in the
    container. Only when the filesystem is smaller than the limit does a full
    one give a clean `ENOSPC` -- which is a thing kingfisher can refuse on.

    `None` where the question cannot be asked: not Linux, no cgroup, or a
    workspace on an ordinary disk where none of this applies.
    """

    #: The filesystem type under the workspace, e.g. `tmpfs`, `ext4`, `apfs`.
    filesystem: str | None = None
    #: Its total size in bytes.
    size_bytes: int | None = None
    #: What this process is allowed to use, from the cgroup.
    limit_bytes: int | None = None
    #: Whether the cgroup permits swapping. `True` is the dangerous answer.
    swap_enabled: bool | None = None

    @property
    def in_memory(self) -> bool:
        """Whether the workspace is on a memory filesystem at all."""
        return self.filesystem in {"tmpfs", "ramfs"}

    @property
    def fits(self) -> bool | None:
        """Whether the filesystem is small enough to fill without killing this.

        `None` when either number is unknown, which is not the same as `False`
        and must not be reported as one.
        """
        if self.size_bytes is None or self.limit_bytes is None:
            return None
        return self.size_bytes < self.limit_bytes


def _mounted_filesystem(path: Path) -> str | None:
    """The filesystem type under `path`, from `/proc/mounts`.

    The longest matching mount point wins, because `/` matches everything and a
    workspace is almost always under something more specific.
    """
    mounts = Path("/proc/mounts")
    if not mounts.is_file():
        return None
    best: tuple[int, str] | None = None
    with suppress(OSError):
        for line in mounts.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split()
            if len(parts) < 3:  # noqa: PLR2004 -- device, mountpoint, type
                continue
            point, kind = parts[1], parts[2]
            under = str(path) == point or str(path).startswith(point.rstrip("/") + "/")
            if under and (best is None or len(point) > best[0]):
                best = (len(point), kind)
    return best[1] if best else None


def _cgroup_number(name: str) -> int | None:
    """One cgroup v2 value, or `None` for absent, `max`, or unreadable."""
    raw = Path("/sys/fs/cgroup") / name
    if not raw.is_file():
        return None
    with suppress(OSError, ValueError):
        text = raw.read_text(encoding="utf-8").strip()
        return None if text == "max" else int(text)
    return None


def memory_backing(workspace: Path) -> MemoryBacking:
    """Read what is underneath this workspace, as far as the platform will say.

    Everything here is absent outside Linux and outside a container, and an
    all-`None` answer is the honest one rather than a failure: a laptop is not
    misconfigured for not being a cgroup.
    """
    workspace = Path(workspace)
    swap = _cgroup_number("memory.swap.max")
    return MemoryBacking(
        filesystem=_mounted_filesystem(workspace),
        size_bytes=_size_of(workspace),
        limit_bytes=_cgroup_number("memory.max"),
        # `memory.swap.max` of 0 is swapping disabled; any other number, or the
        # file being absent on a host that has swap, permits it.
        swap_enabled=None if not Path("/sys/fs/cgroup/memory.swap.max").is_file() else swap != 0,
    )


def _size_of(path: Path) -> int | None:
    """The total size of the filesystem holding `path`."""
    with suppress(OSError):
        stat = os.statvfs(path)
        return stat.f_blocks * stat.f_frsize
    return None
