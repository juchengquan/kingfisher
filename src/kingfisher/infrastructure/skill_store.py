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
#: quote it without knowing the filename themselves.
LAYOUT = f"<skills>/<name>/{FILENAME}"


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
        """Directories that hold a skill somewhere below, but not where it counts.

        Discovery is one level deep -- `<source>/<name>/SKILL.md` -- because
        deepagents' own listing is, and going deeper here would advertise skills
        the agent then could not load. So the layout is a contract, not a
        preference.

        What makes it worth reporting is that breaking it is *silent*. Grouping
        skills into folders is the obvious thing to try, and it yields nothing:
        no error, no warning, just a catalogue that appears empty. This finds
        those folders so a caller can say so.

        It now has to say *why*, because tools and subagents nest freely and
        this one does not. That reads as an arbitrary inconsistency unless the
        reason is stated: those two are read by kingfisher, which can walk as
        deep as it likes, and a skill is read by the agent itself through a
        filesystem route. deepagents lists the skills directory once and looks
        for `SKILL.md` directly inside each entry -- so a nested skill is not
        tidied away, it is unreachable. See `LAYOUT`, which is the sentence to
        quote at someone.

        Not on `SkillRepository`, deliberately. It is a question about
        directories, and a store that is not one has no answer to give.
        """
        directory = Path(self.root)
        if not directory.is_dir():
            return ()

        found = []
        for child in sorted(directory.iterdir()):
            if not child.is_dir() or (child / FILENAME).is_file():
                continue  # not a directory, or a perfectly good skill
            if any(child.rglob(FILENAME)):
                found.append(child.name)
        return tuple(found)
