"""The file tools' view of a session, which follows no link inside it, and of a route,
which follows none out of it."""

from __future__ import annotations

import os
from pathlib import Path

from deepagents.backends import FilesystemBackend


class UnlinkedBackend(FilesystemBackend):
    """A `FilesystemBackend` that reaches a file only at the path it was named by.

    The deny rules judge the path typed, and upstream follows a link anywhere under the
    root: `/scratchpad/x` pointing into `.harness` passed the rule and wrote the pin. For
    the session root only: every rule on a route covers the whole of it, so a route needs
    only `RootedBackend`.
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


class RootedBackend(FilesystemBackend):
    """A `FilesystemBackend` that answers a link out of its root as the operation's error.

    Upstream refuses one with `ValueError`, which `read`, `write`, `edit`, `delete` and
    `ls` let escape, so a link the shell left in `/memory` ended the turn. A link inside
    the root is followed, as upstream follows it: it stays under the route's own rules.
    """

    def _resolve_path(self, key: str) -> Path:
        try:
            return super()._resolve_path(key)
        except ValueError:
            # Not upstream's message, which names both host paths; nor `key`, which is the
            # path inside the route, where each operation's error names the one the model wrote.
            msg = "it is or runs through a link out of its folder, and the file tools follow none"
            raise PermissionError(msg) from None
