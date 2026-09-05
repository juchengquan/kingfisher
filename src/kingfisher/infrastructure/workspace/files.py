"""Fetching a request's files, and one place to fetch them from.

The counterpart to `uploads.provision`, which does the same job for skills and
subagents. Both exist because a caller with no host paths can still name things,
and a port the deployment wired turns names into content.

Resolution happens while the request can still be refused -- before a turn
directory exists -- for the reason `check_placeable` exists: a request naming
something that is not there must fail without leaving a turn behind. That was a
real defect once, when `--input` named a missing file and stranded `t001`, and a
ref that will not resolve is the same bug spelled differently.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kingfisher.domain.references import (
    UnknownReferenceError,
    UnsafeReferenceError,
    within,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from kingfisher.domain.ports import FileStore
    from kingfisher.domain.request import Request


class MissingStoreError(ValueError):
    """A request named files by id, and no `FileStore` was wired.

    The deployment's fault rather than the caller's, and told apart from a ref
    that does not resolve for exactly that reason: one is a wiring mistake
    nobody can fix from outside, the other is a bad request.
    """


@dataclass(frozen=True)
class Fetched:
    """Content resolved for one request, waiting to be written.

    Two fields because they land in different places and live for different
    lengths of time: `data` survives into the next turn, `inputs` leave with the
    turn. That distinction is the only reason `/data` and a turn's `input/` both
    exist, so it is carried rather than flattened.

    Held in memory between admission and the turn opening -- a few hundred
    milliseconds -- because the alternative is writing the inputs before the
    request is known to be admissible, which is the ordering `Admitted` exists
    to protect.
    """

    inputs: Mapping[str, bytes]
    data: Mapping[str, bytes]

    @property
    def empty(self) -> bool:
        return not self.inputs and not self.data


NOTHING = Fetched(inputs={}, data={})


def _resolve(store: FileStore, refs: tuple[str, ...], *, root: Path) -> dict[str, bytes]:
    """Fetch every ref, checking each name lands where it was meant to.

    `root` is a name rather than a real directory: nothing is written here, and
    `within` is lexical. It is passed so the refusal message says which side of
    the request went wrong.
    """
    found: dict[str, bytes] = {}
    for ref in refs:
        for name, content in store.fetch(ref).items():
            # A store is deployment-wired but its *keys* can come from anywhere
            # a caller uploaded, so they are the untrusted half even when the
            # store is not. `within` raises if one tries to leave.
            within(root, name)
            found[name] = content
    return found


def fetch_refs(request: Request, store: FileStore | None) -> Fetched:
    """Resolve everything this request named by id, or refuse to.

    Refusing here rather than later is the whole point, and it is the same rule
    `provision` follows: a request that names a store it was never given, or a
    ref that does not resolve, should fail before a turn directory exists.
    """
    if not request.input_refs and not request.data_refs:
        return NOTHING
    if store is None:
        msg = "request supplies files by id, but no FileStore is wired"
        raise MissingStoreError(msg)
    return Fetched(
        inputs=_resolve(store, request.input_refs, root=Path("input")),
        data=_resolve(store, request.data_refs, root=Path("data")),
    )


@dataclass(frozen=True)
class LocalFileStore:
    """A `FileStore` over one directory on this host.

    The first store the package ships, and mostly it is a refusal. A ref is
    caller-supplied, so the job is making sure `../../etc/passwd` and
    `/etc/passwd` do not resolve -- which is `within`, shared with every other
    store rather than rewritten here.

    The second check is the one `within` cannot do. It is lexical, because the
    domain may not touch the filesystem, so a symlink inside `root` pointing
    outside it passes the name check and still escapes. This is the layer that
    is allowed to ask, so this is where it is asked.
    """

    root: Path

    def fetch(self, file_id: str) -> Mapping[str, bytes]:
        path = within(self.root, file_id)
        base = Path(self.root).resolve()
        # `strict=False`: a missing file resolves fine and is reported below as
        # what it is, rather than as an escape.
        if base not in path.resolve().parents:
            msg = f"reference {file_id!r} resolves outside the store"
            raise UnsafeReferenceError(msg)
        if not path.is_file():
            # Not `FileNotFoundError`. A caller who named a file that is not
            # there gets a refusal they can act on; a bare OSError is
            # indistinguishable from this deployment's disk being wrong, and
            # answers 500 to a typo.
            msg = f"no such reference: {file_id!r}"
            raise UnknownReferenceError(msg)
        return {path.name: path.read_bytes()}


def file_store_named(spec: str, *, setting: str) -> Any:
    """The `FileStore` a deployment named, imported and built.

    A named wrapper over `wiring.store_named` rather than the generic call,
    because the caller is `kingfisher_service` and a consumer takes `kingfisher`
    and nothing deeper. Exporting this exports one narrow function; exporting
    the generic one would mean exporting `FileStore` as well, for the caller to
    pass as `port=` -- and a port is a much larger promise than a wrapper.

    `setting` comes from the caller because the caller owns the variable's name.
    It is `KINGFISHER_SERVICE_FILE_STORE_FACTORY` today, and this package should
    not be the second place that decides so.
    """
    from kingfisher.domain.ports import FileStore  # noqa: PLC0415
    from kingfisher.infrastructure.wiring import store_named  # noqa: PLC0415

    return store_named(spec, setting=setting, port=FileStore)
