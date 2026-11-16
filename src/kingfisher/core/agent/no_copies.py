"""The backend deepagents is handed, with the two folders it copies into on its own refused.

deepagents' summariser writes what it summarised to `conversation_history/` and a batch
too long to send to `large_tool_results/`, both under the backend's `artifacts_root`.
Nothing in a rewind reaches either, so a turn after a rewind could read back the words of
the turns it abandoned. kingfisher keeps the conversation itself, in
`/records/conversation.md`, rebuilt from the turn log along the current path.
"""

from __future__ import annotations

import copy
import logging
import warnings
from typing import Any, override

from deepagents.backends import CompositeBackend
from deepagents.backends.protocol import (
    FILE_NOT_FOUND,
    PERMISSION_DENIED,
    BackendProtocol,
    DeleteResult,
    EditResult,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)

from kingfisher.core.layout import HISTORY, OFFLOADS

from .prompting import piece

#: What a refused write answers. A result, never an exception: deepagents' overflow cut
#: reads a raise as a failure of the whole cut, not of the one result it could not copy.
REFUSED = "refusals/no_copy_here"

#: deepagents' words for the write `NothingKept` refuses, on every summary. Matched on the
#: message, not silenced by level: the same logger carries warnings that matter.
_REFUSED_OFFLOAD = (
    "Failed to offload conversation history",
    "Offloading conversation history to backend failed",
)


class NothingKept(BackendProtocol):
    """A folder that holds nothing and takes nothing: every read finds no file, every
    write is refused."""

    @override
    def ls(self, path: str) -> LsResult:
        return LsResult(entries=[])

    @override
    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return ReadResult(error=f"File '{file_path}' not found")

    @override
    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        return GrepResult(matches=[])

    @override
    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        return GlobResult(matches=[])

    @override
    def write(self, file_path: str, content: str) -> WriteResult:
        return WriteResult(error=piece(REFUSED))

    @override
    def edit(
        self, file_path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> EditResult:
        return EditResult(error=piece(REFUSED))

    @override
    def delete(self, file_path: str) -> DeleteResult:
        return DeleteResult(error=f"File '{file_path}' not found")

    @override
    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [FileUploadResponse(path=path, error=PERMISSION_DENIED) for path, _ in files]

    @override
    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return [FileDownloadResponse(path=path, error=FILE_NOT_FOUND) for path in paths]


def keeping_no_copies(backend: Any) -> Any:
    """`backend` with deepagents' two folders under its `artifacts_root` routed to
    `NothingKept`.

    A copy of the deployment's own `CompositeBackend`, its class and every other route
    kept, so a sessions plugin's overrides still run; the prefixes are read off
    `artifacts_root` as deepagents reads them, so they hold whatever root a plugin set. A
    backend of another kind is `refuse_unusable_backend`'s to refuse, and comes back as
    it is.
    """
    if not isinstance(backend, CompositeBackend):
        return backend
    _quieten_refused_offload()
    root = backend.artifacts_root.rstrip("/")
    refused = {f"{root}/{folder}/": NothingKept() for folder in (HISTORY, OFFLOADS)}
    kept = copy.copy(backend)
    kept.routes = {**backend.routes, **refused}
    # As `CompositeBackend.__init__` orders them, longest first, which is what routes a
    # path under the scratchpad here rather than to the shell's default.
    kept.sorted_routes = sorted(kept.routes.items(), key=lambda route: len(route[0]),
                                reverse=True)
    return kept


class _RefusedOffload(logging.Filter):
    """Drop deepagents' report of the write `NothingKept` refuses on every summary."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not str(record.msg).startswith(_REFUSED_OFFLOAD)


def _quieten_refused_offload() -> None:
    """Install the filter once, on the logger that emits it, and drop the matching
    warning deepagents raises beside it."""
    logger = logging.getLogger("deepagents.middleware.summarization")
    if not any(isinstance(f, _RefusedOffload) for f in logger.filters):
        logger.addFilter(_RefusedOffload())
    # `filterwarnings` replaces an identical filter rather than adding a second.
    warnings.filterwarnings("ignore", message=_REFUSED_OFFLOAD[1], category=UserWarning)
