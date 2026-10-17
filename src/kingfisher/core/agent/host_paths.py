"""Refusing a host path handed to a file tool, and telling the model what to use instead."""

from __future__ import annotations

from pathlib import Path

from kingfisher.core.layout import SCRATCH_ROUTE

#: Absolute prefixes that can never name a workspace directory. An explicit list, not
#: one inferred from the filesystem, so it is the same on every machine.
HOST_ROOTS: tuple[str, ...] = (
    "/Users/",
    "/home/",
    # A deny-list entry, not a temp file: nothing is written here.
    "/tmp/",  # noqa: S108
    "/private/",
    "/etc/",
    "/var/",
    "/usr/",
    "/opt/",
)


class HostPathError(ValueError):
    """A host path reached a file tool."""


def reject_host_path(
    key: str, workspace: Path, *, session_dir: Path, scratch_route: str = SCRATCH_ROUTE
) -> None:
    """Refuse a host path handed to a file tool.

    A path inside this session is offered back as the virtual path meant; anything
    else under the workspace has none to offer. Both prefixes, since the session alone
    would leave the rest of the workspace to `HOST_ROOTS`, which names no workspace.
    """
    if not key.startswith("/"):
        return

    prefix = f"{session_dir}/"
    if key.startswith(prefix):
        suggestion = f"/{key[len(prefix) :]}"
        msg = (
            f"{key!r} is a host path, and file tools take virtual paths rooted at the "
            f"workspace. Use {suggestion!r} instead. (Passing the host path would have "
            f"created it inside the workspace, under a mirror of its own location.)"
        )
        raise HostPathError(msg)

    if key.startswith((f"{workspace}/", *HOST_ROOTS)):
        msg = (
            f"{key!r} is a host path, and file tools take virtual paths rooted at the "
            f"workspace — it would have been created inside the workspace, not where "
            f"you meant. Use the shell for host paths, or a virtual path such as "
            f"{scratch_route}<name> for files that belong to this task."
        )
        raise HostPathError(msg)

