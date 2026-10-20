"""What the two Linux fences share: which paths exist.

Not each fence's own path list: they differ on purpose, and one list serving both is how
a mechanism gains a path the other meant to deny. What a command comes back as is
`process.run_command`'s, shared with the unfenced shell.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


def present(paths: Iterable[Path | str]) -> list[str]:
    """The ones that exist, as strings.

    Both fences refuse to build over an absent path (`/lib64` on arm64 Debian), and a
    fence that never applied reads as a broken image.
    """
    return [str(path) for path in paths if Path(path).exists()]
