"""The file tools' view of a session, which follows no link inside it."""

from __future__ import annotations

import os
from pathlib import Path

from deepagents.backends import FilesystemBackend


class UnlinkedBackend(FilesystemBackend):
    """A `FilesystemBackend` that reaches a file only at the path it was named by.

    The deny rules judge the path typed, and upstream follows a link anywhere under the
    root: `/scratchpad/x` pointing into `.harness` passed the rule and wrote the pin. For
    the session root only: every rule on a route covers the whole of it.
    """

    def _resolve_path(self, key: str) -> Path:
        full = super()._resolve_path(key)
        typed = Path(os.path.normpath(self.cwd / key.lstrip("/")))
        if self.virtual_mode and full != typed:
            # An `OSError`, which every operation reports as its error; upstream raises
            # `ValueError` for traversal, which `read` and `write` let escape.
            msg = f"{key} is or runs through a link, and the file tools follow none"
            raise PermissionError(msg)
        return full

    def _to_virtual_path(self, path: Path) -> str:
        # `ls`, `glob` and grep's own walk name what they find through here, and skip a
        # `ValueError` as outside the root: a link found mid-walk is never opened.
        if self.virtual_mode and Path(path).resolve() != Path(os.path.normpath(path)):
            msg = f"{path} is a link"
            raise ValueError(msg)
        return super()._to_virtual_path(path)
