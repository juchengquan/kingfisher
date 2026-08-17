"""Skills held in a directory on this host.

The mirror of `subagent_store`, and here for the same reason: `domain.skill`
knows what a definition means, and this knows where they are. deepagents owns
the format itself — what is needed here is only which names a directory offers,
which is a directory listing and nothing more.

A class rather than two functions taking the same `Path`, which is what these
were. The directory is state, so it belongs to an object; the reading is then
done once and answered from, rather than repeated per caller. `SkillRepository`
in `domain.ports` is the shape a deployment may replace, and this is the one
backed by a filesystem.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

from kingfisher.domain.skill import FILENAME

#: Where a skill has to be for anything to find it, said once so callers can
#: quote it without knowing the filename themselves. Two shapes, because a
#: folder directly under the root is registered as its own source and a source
#: is listed one level deep -- so one level of grouping works and a second does
#: not.
LAYOUT = f"<skills>/<name>/{FILENAME} or <skills>/<source>/<name>/{FILENAME}"

#: How many path parts a reachable `SKILL.md` has, relative to the root:
#: `<name>/SKILL.md` is two, `<source>/<name>/SKILL.md` is three. Anything
#: longer sits below the deepest source and is unreachable.
DEEPEST = 3


def reachable(root: Path) -> tuple[Path, ...]:
    """Every directory holding a `SKILL.md` the agent could actually open.

    The listing this repo makes on its own, used for the two questions that
    need to know what *looks* like a skill: which ones sit too deep to load,
    and which ones deepagents was offered and dropped. Neither is "what is a
    skill", which stays deepagents' to answer -- this only walks directories.
    """
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
    """The skills in one directory.

    Given the directory rather than a workspace to derive one from: a catalogue
    may be deployed outside any workspace and shared by all of them, and a
    session's uploads live somewhere else again. Both are this same class
    pointed at different roots.

    Frozen, and read at most once per instance. A catalogue's repository is
    built when the deployment is wired and answers every turn from what it read
    then; a session's is built for the turn that needs it. Neither notices a
    directory changing underneath it, which is the trade `warm()` already made
    deliberately -- a catalogue edited mid-run is a redeployment, not a feature.
    """

    root: Path

    @cached_property
    def names(self) -> tuple[str, ...]:
        """Skill names in this directory, which are its subdirectory names."""
        directory = Path(self.root)
        if not directory.is_dir():
            return ()
        return tuple(sorted(p.name for p in directory.iterdir() if (p / FILENAME).is_file()))

    def files(self, name: str) -> Mapping[str, bytes]:
        """Every file this skill ships, keyed by path relative to the skill.

        Read on demand rather than cached with the listing. A catalogue's names
        are read once at wiring and answered from every turn; its *contents* are
        what a skill's scripts and data live in, and holding all of them for the
        life of a deployment would trade a directory listing for a copy of the
        catalogue in memory.

        Not read through `names` either, so an unknown name is a `KeyError`
        rather than an empty mapping -- a skill that is silently empty is the
        failure the neighbours here keep refusing.
        """
        directory = Path(self.root) / name
        if not (directory / FILENAME).is_file():
            msg = f"no skill named {name!r} in {self.root}"
            raise KeyError(msg)
        return {
            str(path.relative_to(directory)): path.read_bytes()
            for path in sorted(directory.rglob("*"))
            if path.is_file()
        }

    @cached_property
    def misplaced(self) -> tuple[str, ...]:
        """Skills sitting below the deepest place anything will look for them.

        Reach is `DEEPEST` parts, because a folder under the root becomes its
        own source and deepagents lists a source one level deep and no further.
        So one level of grouping loads and a second does not -- `research/`
        holds skills, `research/deep/` hides them.

        This used to report the folder rather than the skill, back when *any*
        folder was too deep. Now that one level works, naming the folder would
        indict `research/` for the sins of `research/deep/`, so it answers with
        the path of the skill that is actually out of reach.

        What makes it worth reporting at all is that breaking the layout is
        *silent*. Grouping one level further is the obvious next thing to try
        and it yields nothing: no error, no warning, just a skill that never
        appears. This finds those so a caller can be told.

        It has to say *why*, because tools and subagents nest freely and this
        does not. That reads as an arbitrary inconsistency unless the reason is
        stated: those two are read by kingfisher, which can walk as deep as it
        likes, and a skill is read by the agent through a filesystem route. See
        `LAYOUT`, which is the sentence to quote at someone.

        Not on `SkillRepository`, deliberately. It is a question about
        directories, and a store that is not one has no answer to give.
        """
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
