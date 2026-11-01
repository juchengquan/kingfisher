"""Who is told about a skill: `skill_locks.yaml`, beside the definitions.

Not a file in the skill's own folder: a vendor's pack is replaced whole, which would drop
the lock without a word, and the agent can read everything under `/skills/`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

import yaml

from kingfisher.core.access import ALL, Audience, spell, zoned
from kingfisher.core.config import SKILL_LOCKS_FILE

from . import fields
from .documents import load

if TYPE_CHECKING:
    from pathlib import Path

FILENAME = SKILL_LOCKS_FILE

_KNOWN = ("name", "source_ids", "datazone")


class SkillLockError(ValueError):
    """`skill_locks.yaml` does not say who is told about a skill in a way that can be read."""


@dataclass(frozen=True)
class SkillLock:
    """One skill's lock, as the file writes it."""

    #: As written: bare, or `source::name` where two folders offer the name.
    name: str
    source_ids: Audience = ALL
    datazone: str | None = None

    @property
    def audience(self) -> Audience:
        """Who is told about the skill: its source ids, and its datazone as well."""
        return zoned(self.source_ids, self.datazone)


def read_locks(path: Path) -> tuple[SkillLock, ...]:
    """Every lock the file writes, in file order; none where there is no file."""
    if not path.is_file():
        return ()
    try:
        document = load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        msg = f"{FILENAME}: cannot read it ({' '.join(str(exc).split())})"
        raise SkillLockError(msg) from exc
    if document is None:
        return ()
    if not isinstance(document, list):
        msg = (
            f"{FILENAME}: this file is a list, one entry per skill -- "
            f"- {{name: audit, source_ids: [A]}} -- got {type(document).__name__}"
        )
        raise SkillLockError(msg)
    read = fields.Reader(source=FILENAME, error=SkillLockError)
    locks: dict[str, SkillLock] = {}
    for position, entry in enumerate(document, start=1):
        lock = _one(entry, position=position, read=read)
        if lock.name in locks:
            # Both, and no pick: which of two audiences is meant is only the author's to say.
            msg = (
                f"{FILENAME}: {lock.name!r} is locked twice, to "
                f"{_said(locks[lock.name])} and to {_said(lock)}. Keep the one you mean"
            )
            raise SkillLockError(msg)
        locks[lock.name] = lock
    return tuple(locks.values())


def _one(entry: object, *, position: int, read: fields.Reader) -> SkillLock:
    """One entry, refused unless it locks one named skill to somebody."""
    if not isinstance(entry, Mapping):
        msg = (
            f"{FILENAME}: entry {position} is not a mapping; an entry is "
            f"{{name: <skill>, source_ids: [...]}}, with datazone: if it needs one"
        )
        raise SkillLockError(msg)
    read.refuse_unrecognised(entry, known=_KNOWN)
    name = fields.text(entry.get("name"))
    if not name or name == ALL:
        msg = f"{FILENAME}: entry {position} names no skill; write name: <skill>"
        raise SkillLockError(msg)
    where = f"{FILENAME}: {name}"
    source_ids = (
        ALL
        if entry.get("source_ids") is None
        else read.audience_list(
            entry["source_ids"],
            where=f"{where}: source_ids",
            lone_name=True,
            left_out="tell everyone about it",
        )
    )
    zone = read.datazone(entry.get("datazone"))
    lock = SkillLock(name=name, source_ids=source_ids, datazone=zone)
    if lock.audience == ALL:
        msg = (
            f"{where} locks it to nobody in particular. A skill this file does not "
            f"name is told to everyone who opens an agent listing it, so leave it out"
        )
        raise SkillLockError(msg)
    return lock


def _said(lock: SkillLock) -> str:
    """One lock's audience, as a refusal names it."""
    zone = "" if lock.datazone is None else f" in datazone {lock.datazone}"
    who = "everyone" if lock.source_ids == ALL else f"[{spell(lock.source_ids)}]"
    return f"{who}{zone}"
