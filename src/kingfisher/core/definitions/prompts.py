"""A definition's `system_prompt`: its text, and the slots in it each caller's grant fills."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

from kingfisher.core.access import ALL, DATAZONE_PREFIX, mentioned, reaches, zoned

from . import fields

if TYPE_CHECKING:
    from kingfisher.core.access import Audience, Vocabulary

FIELD = "system_prompt"

#: A `{{name}}` in a prompt: anything between doubled braces, so `{{ figures }}` is refused
#: as naming no slot rather than reaching the model as braces.
SLOT = re.compile(r"\{\{([^{}]*)\}\}")

#: What a slot may be called. No spaces or braces, which would make a blank that matches none.
SLOT_NAME = re.compile(r"\w[\w-]*")

LONG_FORM = ("text", "slots")
ENTRY = ("source_ids", "datazone", "text")


@dataclass(frozen=True)
class SlotEntry:
    """One way a slot is filled."""

    #: Whose grant fills the slot with `text`, the datazone folded in; `None` for the
    #: slot's default, sent only when no other entry matched.
    audience: Audience | None
    text: str


Slots = Mapping[str, tuple[SlotEntry, ...]]


class Prompt(NamedTuple):
    """What a definition's `system_prompt` says, either form."""

    text: str
    slots: Slots


def read_prompt(document: Mapping[str, object], read: fields.Reader) -> Prompt:
    """A definition's prompt: no slots for the short form."""
    written = document.get(FIELD)
    if not isinstance(written, Mapping):
        read.require(document, FIELD)
        text = fields.text(written)
        _refuse_blanks(text, {}, read)
        return Prompt(text, {})
    read.refuse_unrecognised(
        (f"{FIELD}.{key}" for key in written), known=[f"{FIELD}.{key}" for key in LONG_FORM]
    )
    where = f"{read.source}: {FIELD}"
    if isinstance(written.get("text"), (Mapping, list)) or not fields.text(written.get("text")):
        msg = f"{where} written long needs its text, one piece of text under text: |"
        raise read.error(msg)
    text = fields.text(written["text"])
    slots = _slots(written.get("slots"), read)
    _refuse_blanks(text, slots, read)
    return Prompt(text, slots)


def _slots(written: object, read: fields.Reader) -> Slots:
    where = f"{read.source}: {FIELD}.slots"
    if written is None:
        return {}
    if not isinstance(written, Mapping) or not written:
        msg = f"{where} is a mapping of slot names to their entries -- got {written!r}"
        raise read.error(msg)
    slots: dict[str, tuple[SlotEntry, ...]] = {}
    for name, entries in written.items():
        if not isinstance(name, str) or not SLOT_NAME.fullmatch(name):
            msg = f"{where}: {name!r} is not a slot name -- letters, digits, _ and - only"
            raise read.error(msg)
        if not isinstance(entries, list) or not entries:
            msg = f"{where}.{name} is a list of entries, each with a text -- got {entries!r}"
            raise read.error(msg)
        slots[name] = tuple(_entry(one, f"{where}.{name}", read) for one in entries)
        # Two defaults would both be sent where nothing matched, which no one writes meaning to.
        if sum(one.audience is None for one in slots[name]) > 1:
            msg = (
                f"{where}.{name} has more than one entry naming no source_ids or "
                f"datazone, and a slot has one default -- join their text"
            )
            raise read.error(msg)
    return slots


def _entry(written: object, where: str, read: fields.Reader) -> SlotEntry:
    if not isinstance(written, Mapping):
        msg = f"{where}: an entry is a mapping of source_ids, datazone and text -- got {written!r}"
        raise read.error(msg)
    complaint = fields.unrecognised(written, known=ENTRY, noun="key")
    if complaint is not None:
        msg = f"{where}: {complaint}"
        raise read.error(msg)
    text = written.get("text")
    if isinstance(text, (Mapping, list)) or not fields.text(text):
        msg = f"{where}: every entry needs its text, one piece of text under text: |"
        raise read.error(msg)
    if SLOT.search(str(text)):
        msg = f"{where}: an entry's text names a slot, and slots do not nest"
        raise read.error(msg)
    datazone = read.datazone(written.get("datazone"))
    if "source_ids" not in written:
        audience = zoned(ALL, datazone)
    else:
        audience = zoned(
            read.audience_list(
                written["source_ids"],
                where=f"{where}: source_ids",
                lone_name=True,
                left_out="make this entry the slot's default",
            ),
            datazone,
        )
        if audience == ALL:
            msg = (
                f'{where}: source_ids ["{ALL}"] is every caller, which is what the '
                "slot's default is for -- leave source_ids out"
            )
            raise read.error(msg)
    return SlotEntry(audience=None if audience == ALL else audience, text=fields.text(text))


def _refuse_blanks(text: str, slots: Slots, read: fields.Reader) -> None:
    """Refuse a blank no slot fills, and a slot no blank places."""
    placed = {match.group(1) for match in SLOT.finditer(text)}
    if unknown := sorted(placed - set(slots)):
        named = ", ".join(f"{{{{{one}}}}}" for one in unknown)
        if slots:
            defined = ", ".join(sorted(slots))
            hint = f"the slots defined are {defined}"
        else:
            hint = f"a slot is defined under {FIELD}.slots, written long"
        msg = f"{read.source}: {FIELD} names {named}, which no slot fills -- {hint}"
        raise read.error(msg)
    if unplaced := sorted(set(slots) - placed):
        named = ", ".join(unplaced)
        msg = (
            f"{read.source}: {FIELD}.slots defines {named}, placed nowhere in its text, so "
            f"it is never sent -- write {{{{{unplaced[0]}}}}} where it goes"
        )
        raise read.error(msg)


def told(text: str, slots: Slots, held: frozenset[str] | None) -> str:
    """`text` with each slot filled for a caller holding `held`, `None` reaching everything.

    A slot left empty takes its line with it, and the blank line it sat between.
    """
    if not slots:
        return text
    filled = {name: _filled(entries, held) for name, entries in slots.items()}
    kept: list[str] = []
    dropped = False
    for line in text.split("\n"):
        alone = SLOT.fullmatch(line.strip())
        if alone and not filled[alone.group(1)]:
            dropped = True
            continue
        if dropped and not line.strip() and (not kept or not kept[-1].strip()):
            continue
        dropped = False
        kept.append(SLOT.sub(lambda match: filled[match.group(1)], line))
    return "\n".join(kept)


def _filled(entries: tuple[SlotEntry, ...], held: frozenset[str] | None) -> str:
    """Every entry this caller's grant matches, else the default."""
    # `None` reaches everything, as it does for every lock: the run sees what any caller could.
    matched = [
        one.text
        for one in entries
        if one.audience is not None and (held is None or reaches(one.audience, held))
    ]
    if not matched:
        matched = [one.text for one in entries if one.audience is None]
    return "\n\n".join(matched)


def unused_names(slots: Slots, vocabulary: Vocabulary | None) -> tuple[str, ...]:
    """Every source id and datazone a slot is keyed on that no lock uses."""
    named = {
        name
        for entries in slots.values()
        for one in entries
        if one.audience is not None
        for name in mentioned(one.audience)
    }
    source_ids = set() if vocabulary is None else set(vocabulary.source_ids)
    datazones = set() if vocabulary is None else set(vocabulary.datazones)
    return tuple(
        sorted(
            name
            for name in named
            if (
                name.removeprefix(DATAZONE_PREFIX) not in datazones
                if name.startswith(DATAZONE_PREFIX)
                else name not in source_ids
            )
        )
    )
