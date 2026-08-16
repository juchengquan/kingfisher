"""Finding skill definitions on disk.

The mirror of `subagent_store`, and here for the same reason: `domain.skill`
knows what a definition means, and this knows where they are. deepagents owns
the format itself — what is needed here is only which names a directory offers,
which is a directory listing and nothing more.
"""

from __future__ import annotations

from pathlib import Path

from kingfisher.domain.skill import FILENAME

#: Where a skill has to be for anything to find it, said once so callers can
#: quote it without knowing the filename themselves.
LAYOUT = f"<skills>/<name>/{FILENAME}"


def names(directory: Path) -> tuple[str, ...]:
    """Skill names in one directory, which are its subdirectory names.

    Given the directory rather than a workspace to derive one from: a catalogue
    may be deployed outside any workspace and shared by all of them, and a
    session's uploads live somewhere else again.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return ()
    return tuple(sorted(p.name for p in directory.iterdir() if (p / FILENAME).is_file()))


def misplaced(directory: Path) -> tuple[str, ...]:
    """Directories that hold a skill somewhere below, but not where it counts.

    Discovery is one level deep -- `<source>/<name>/SKILL.md` -- because
    deepagents' own listing is, and going deeper here would advertise skills
    the agent then could not load. So the layout is a contract, not a
    preference.

    What makes it worth reporting is that breaking it is *silent*. Grouping
    skills into folders is the obvious thing to try, and it yields nothing: no
    error, no warning, just a catalogue that appears empty. This finds those
    folders so a caller can say so.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return ()

    found = []
    for child in sorted(directory.iterdir()):
        if not child.is_dir() or (child / FILENAME).is_file():
            continue  # not a directory, or a perfectly good skill
        if any(child.rglob(FILENAME)):
            found.append(child.name)
    return tuple(found)
