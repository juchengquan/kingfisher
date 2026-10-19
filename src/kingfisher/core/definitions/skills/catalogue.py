"""Skills held in a directory on this host."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from types import MappingProxyType

from .spec import FILENAME

#: Where a skill has to be for anything to find it, said once so callers can quote it.
#: One level of grouping works, because a folder under the root is a source of its own
#: and a source is listed one level deep; a second level does not.
SKILL_LAYOUT = f"<skills>/<name>/{FILENAME} or <skills>/<source>/<name>/{FILENAME}"

#: The most path parts a reachable `SKILL.md` has below the root,
#: `<source>/<name>/SKILL.md`; anything longer is unreachable.
DEEPEST = 3

#: The same for a mount, which is one source, so only `<name>/SKILL.md` is reachable.
MOUNT_DEEPEST = 2


def reachable(root: Path, deepest: int = DEEPEST) -> tuple[Path, ...]:
    """Every directory holding a `SKILL.md` the agent could actually open."""
    if not root.is_dir():
        return ()
    return tuple(
        sorted(
            found.parent
            for found in root.rglob(FILENAME)
            if len(found.relative_to(root).parts) <= deepest
        )
    )


def _below(root: Path, deepest: int) -> tuple[str, ...]:
    """Skills under `root` deeper than anything will look, relative to it."""
    if not root.is_dir():
        return ()
    return tuple(
        str(found.parent.relative_to(root))
        for found in root.rglob(FILENAME)
        if len(found.relative_to(root).parts) > deepest
    )


@dataclass(frozen=True)
class LocalSkillRepository:
    """The skills in one directory, and in any mounted beside it.

    No listing of its own: which skills there are is `SkillRegistry`'s answer, because
    only deepagents' parse decides what loads.
    """

    root: Path
    #: Each further directory, by the label it is mounted under.
    mounts: Mapping[str, Path] = field(default_factory=lambda: MappingProxyType({}))
    #: Whether the root is one source with every skill directly in it, as a mount and a
    #: subagent's own folder are. Both readers ask it, so the skills a subagent is granted
    #: and the skills it is shown are found the same way.
    flat: bool = False

    @cached_property
    def misplaced(self) -> tuple[str, ...]:
        """Skills sitting below the deepest place anything will look for them."""
        return tuple(
            sorted([
                *_below(Path(self.root), MOUNT_DEEPEST if self.flat else DEEPEST),
                *(
                    f"{label}/{one}"
                    for label, mount in self.mounts.items()
                    for one in _below(Path(mount), MOUNT_DEEPEST)
                ),
            ])
        )
