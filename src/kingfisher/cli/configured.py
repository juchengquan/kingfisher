"""What the command builds every `Kingfisher` with: the sessions its settings name, its own
turn text, and where `--log` keeps a turn's events.

Here and not in the core: only the command has settings and no argument to be told them by,
and only it may fall back to `LocalSessions`. It takes no other plugin; code that needs one
builds its own `Kingfisher`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from kingfisher.core.logged import logged
from kingfisher.core.wiring import built_from_setting
from kingfisher.plugins import JsonlRunEvents, LocalSessions, Sessions, TurnInstructions


def configured_sessions(environ: Mapping[str, str] | None = None) -> Sessions:
    """The sessions `KINGFISHER_SESSIONS` names, or `LocalSessions`.

    Without it, `sessions` and `reap` would act on a local `sessions/`, not the deployment's.
    """
    named = ((os.environ if environ is None else environ).get("KINGFISHER_SESSIONS") or "").strip()
    if not named:
        return LocalSessions()
    return built_from_setting(named, setting="KINGFISHER_SESSIONS", interface=Sessions)


class CommandTurnInstructions(TurnInstructions):
    """The command's `TurnInstructions`, sending every graph what `--tell` gave it."""

    def __init__(self, said: str = "") -> None:
        self.said = said

    def text(self, cfg: Any, session_id: str, caller: Any) -> str:  # noqa: ARG002 -- the interface's
        return self.said


class KeptAndSaid(JsonlRunEvents):
    """The file `--log` names, and each event on stderr as well where `--log-level` shows
    it: a command's user reads the terminal, and a plugin they wired that broke must still
    say so there."""

    def record(self, event: Mapping[str, object]) -> None:
        # Unfiltered: a deployment may total its tokens from the file.
        super().record(event)
        logged(event)
