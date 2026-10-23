"""The `TurnInstructions` the shipped agents are written for: today's date for every graph,
and who the user is for a definition that receives `user` -- `agents/analyst.yaml` does.

A deployment seeding `analyst` has to wire it, or startup refuses the `receives:` line
naming a label nothing offers. For the command, from the checkout's root:

    PYTHONPATH=. KINGFISHER_PLUGINS=assets.plugins.desk:build_plugins

In code, `Kingfisher(..., turn_instructions=DeskInstructions())`. It reads the request's
`context`: `tz`, an IANA zone name, and `name` and `team` for who the user is.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from kingfisher.plugins import Plugins, Told

if TYPE_CHECKING:
    from kingfisher import Caller


class DeskInstructions:
    """The date in the caller's timezone, and the caller's name and team."""

    labels = ("user",)

    def text(self, cfg: Any, session_id: str, caller: Caller) -> Told:  # noqa: ARG002 -- the interface's
        zone = _zone(caller.context.get("tz", "UTC"))
        here = caller.started.astimezone(zone)
        # The zone is named in the sentence, so a misspelt one reads as UTC to whoever
        # looks, rather than as a date that is quietly a day out.
        everyone = f"Today is {here:%A} {here.day} {here:%B %Y}, in {zone.key}."
        named = (caller.context.get("name"), caller.context.get("team"))
        who = ", ".join(one for one in named if one)
        labelled = {"user": f"You are working for {who}."} if who else {}
        return Told(everyone=everyone, labelled=labelled)


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def build_plugins() -> Plugins:
    """What `KINGFISHER_PLUGINS` names."""
    return Plugins(turn_instructions=DeskInstructions())
