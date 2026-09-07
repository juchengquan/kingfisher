"""Fetching a request's files, and one place to fetch them from."""

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
    """A request named files by id, and no `FileStore` was wired."""


@dataclass(frozen=True)
class Fetched:
    """Content resolved for one request, waiting to be written."""

    inputs: Mapping[str, bytes]
    data: Mapping[str, bytes]

    @property
    def empty(self) -> bool:
        return not self.inputs and not self.data


NOTHING = Fetched(inputs={}, data={})


def _resolve(store: FileStore, refs: tuple[str, ...], *, root: Path) -> dict[str, bytes]:
    """Fetch every ref, checking each name lands where it was meant to."""
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
    """Resolve everything this request named by id, or refuse to."""
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
    """A `FileStore` over one directory on this host."""

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
    """The `FileStore` a deployment named, imported and built."""
    from kingfisher.domain.ports import FileStore  # noqa: PLC0415
    from kingfisher.infrastructure.wiring import store_named  # noqa: PLC0415

    return store_named(spec, setting=setting, port=FileStore)
