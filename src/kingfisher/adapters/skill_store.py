"""Finding skill definitions on disk.

The mirror of `subagent_store`, and here for the same reason: `domain.skill`
knows what a definition means, and this knows where they are. deepagents owns
the format itself — what is needed here is only which names a directory offers,
which is a directory listing and nothing more.
"""

from __future__ import annotations

from pathlib import Path

from kingfisher.domain.skill import FILENAME


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
