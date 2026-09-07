"""Skills held in a directory on this host.

The mirror of `subagents`, and here for the same reason: `skills.spec` knows what a
definition means, and this knows where they are. deepagents owns the format itself —
what is needed here is only which names a directory offers, which is a directory
listing and nothing more.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

from kingfisher.skills.spec import FILENAME

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
    def names(self) -> tuple[str, ...]:
        """Skill names in this directory, which are its subdirectory names."""
        directory = Path(self.root)
        if not directory.is_dir():
            return ()
        return tuple(sorted(p.name for p in directory.iterdir() if (p / FILENAME).is_file()))

    def files(self, name: str) -> Mapping[str, str]:
        """Every file this skill ships, keyed by path relative to the skill."""
        directory = Path(self.root) / name
        if not (directory / FILENAME).is_file():
            msg = f"no skill named {name!r} in {self.root}"
            raise KeyError(msg)
        # `replace` rather than strict: see the port. Decoded here, once, so
        # the mount does not have to know what a skill is made of.
        return {
            str(path.relative_to(directory)): path.read_text(encoding="utf-8", errors="replace")
            for path in sorted(directory.rglob("*"))
            if path.is_file()
        }

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
