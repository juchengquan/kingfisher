"""Skills held in a directory on this host."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from types import MappingProxyType

from kingfisher.kinds.skills.spec import FILENAME

#: Where a skill has to be for anything to find it, said once so callers can
#: quote it without knowing the filename themselves. Two shapes, because a
#: folder directly under the root is registered as its own source and a source
#: is listed one level deep -- so one level of grouping works and a second does
#: not.
SKILL_LAYOUT = f"<skills>/<name>/{FILENAME} or <skills>/<source>/<name>/{FILENAME}"

#: How many path parts a reachable `SKILL.md` has, relative to the root:
#: `<name>/SKILL.md` is two, `<source>/<name>/SKILL.md` is three. Anything
#: longer sits below the deepest source and is unreachable.
DEEPEST = 3

#: The same, for a mount. A mount is one source rather than a root that may hold
#: sources, so only `<name>/SKILL.md` is reachable in it.
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
    """The skills in one directory, and in any mounted beside it."""

    root: Path
    #: Each further directory, by the label it is mounted under.
    mounts: Mapping[str, Path] = field(default_factory=lambda: MappingProxyType({}))

    @cached_property
    def misplaced(self) -> tuple[str, ...]:
        """Skills sitting below the deepest place anything will look for them."""
        return tuple(
            sorted([
                *_below(Path(self.root), DEEPEST),
                *(
                    f"{label}/{one}"
                    for label, mount in self.mounts.items()
                    for one in _below(Path(mount), MOUNT_DEEPEST)
                ),
            ])
        )
