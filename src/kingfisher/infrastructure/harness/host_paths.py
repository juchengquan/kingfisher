"""Refusing a host path handed to a file tool, and telling the model what to use instead."""

from __future__ import annotations

from pathlib import Path

from kingfisher.layout import SCRATCH_ROUTE

#: Absolute prefixes that can never name a workspace directory, so a file-tool
#: path starting with one is a host path that was passed to the wrong kind of
#: tool. Deliberately a short, explicit list rather than a rule inferred from
#: the filesystem: it has to be readable, and it has to be the same on every
#: machine regardless of what happens to exist at `/`.
HOST_ROOTS: tuple[str, ...] = (
    "/Users/",
    "/home/",
    # S108 reads a "/tmp" literal as insecure temp-file use. This is the
    # inverse: an entry in a deny-list, naming the prefix a file tool must
    # refuse. Nothing is written here.
    "/tmp/",  # noqa: S108
    "/private/",
    "/etc/",
    "/var/",
    "/usr/",
    "/opt/",
)


class HostPathError(ValueError):
    """A host path reached a file tool."""


def reject_host_path(key: str, workspace: Path, *, session_dir: Path) -> None:
    """Refuse a host path handed to a file tool.

    Two prefixes, because they can be answered differently. A path inside *this*
    session can be turned into the virtual path the caller meant, so that refusal
    carries it; anything else under the workspace -- another session, a catalogue,
    `models.yaml` -- has no virtual path to offer and gets the general sentence.

    Both, and not one: the session is the only prefix a suggestion can be built from,
    and passing it as the scope narrows the refusal to one session -- leaving the rest
    of the workspace to `HOST_ROOTS`, which names no workspace at all.
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
            f"{SCRATCH_ROUTE}<name> for files that belong to this task."
        )
        raise HostPathError(msg)

