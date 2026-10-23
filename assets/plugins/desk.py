"""A `TurnInstructions` telling every graph of a turn today's date, in the caller's timezone,
and who it is working with.

For code that builds its own `Kingfisher`: `Kingfisher(..., turn_instructions=DeskInstructions())`.
It reads the request's `context`: `tz`, an IANA zone name, and `name` and `team`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

if TYPE_CHECKING:
    from kingfisher import Caller


class DeskInstructions:
    """The date in the caller's timezone, and the caller's name and team."""

    def text(self, cfg: Any, session_id: str, caller: Caller) -> str:  # noqa: ARG002 -- the interface's
        zone = _zone(caller.context.get("tz", "UTC"))
        here = caller.started.astimezone(zone)
        # The zone is named in the sentence, so a misspelt one reads as UTC to whoever
        # looks, rather than as a date that is quietly a day out.
        said = f"Today is {here:%A} {here.day} {here:%B %Y}, in {zone.key}."
        named = (caller.context.get("name"), caller.context.get("team"))
        if who := ", ".join(one for one in named if one):
            said += f" You are working with {who}."
        return said


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")

