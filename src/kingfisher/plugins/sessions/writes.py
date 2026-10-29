"""One write at a time to any one file, so two a model sends at once leave it whole."""

from __future__ import annotations

import threading
from contextlib import AbstractContextManager, nullcontext
from typing import TYPE_CHECKING

from deepagents.backends import FilesystemBackend

if TYPE_CHECKING:
    from deepagents.backends.protocol import EditResult, FileUploadResponse, WriteResult

#: By path and shared by the whole process, not one per backend: two backends over one
#: folder would each take a lock the other never sees. A fixed number, so a service that
#: writes many files does not keep one lock for each.
_STRIPES = tuple(threading.Lock() for _ in range(64))


class OneWriterPerFile(FilesystemBackend):
    """A `FilesystemBackend` that lets one write, edit or upload at a time at each file.

    Upstream empties a file and then writes it, and an edit reads a file whole and writes
    it back. Two at once at one file can leave the end of the longer write behind the
    shorter one, or lose an edit, and each call is told it succeeded.
    """

    def _one_at(self, key: str) -> AbstractContextManager[object]:
        try:
            where = self._resolve_path(key)
        except (OSError, ValueError, RuntimeError):
            # Nothing is written, and upstream resolves it again and answers it.
            return nullcontext()
        return _STRIPES[hash(where) % len(_STRIPES)]

    def write(self, file_path: str, content: str) -> WriteResult:
        with self._one_at(file_path):
            return super().write(file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        with self._one_at(file_path):
            return super().edit(file_path, old_string, new_string, replace_all)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        # A file at a time, so a long batch holds no lock but the current file's.
        answers: list[FileUploadResponse] = []
        for path, data in files:
            with self._one_at(path):
                answers += super().upload_files([(path, data)])
        return answers
