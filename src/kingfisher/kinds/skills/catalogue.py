"""Skills held in a directory on this host."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

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


def reachable(root: Path) -> tuple[Path, ...]:
    """Every directory holding a `SKILL.md` the agent could actually open."""
    if not root.is_dir():
        return ()
    return tuple(
        sorted(
            found.parent
            for found in root.rglob(FILENAME)
            if len(found.relative_to(root).parts) <= DEEPEST
        )
    )


@dataclass(frozen=True)
class LocalSkillRepository:
    """The skills in one directory."""

    root: Path

    @cached_property
    def misplaced(self) -> tuple[str, ...]:
        """Skills sitting below the deepest place anything will look for them."""
        directory = Path(self.root)
        if not directory.is_dir():
            return ()
        return tuple(
            sorted(
                str(found.parent.relative_to(directory))
                for found in directory.rglob(FILENAME)
                if len(found.relative_to(directory).parts) > DEEPEST
            )
        )
