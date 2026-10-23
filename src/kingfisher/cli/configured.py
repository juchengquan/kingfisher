"""What the command builds every `Kingfisher` with: the sessions its settings name, and its own
turn text.

Here and not in the core: only the command has settings and no argument to be told them by,
and only it may fall back to `LocalSessions`. It takes no other plugin; code that needs one
builds its own `Kingfisher`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from kingfisher.core.wiring import built_from_setting
from kingfisher.plugins import LocalSessions, Sessions


def _named(setting: str, environ: Mapping[str, str] | None) -> str | None:
    """What a setting names, or `None` where it is unset or blank."""
    raw = ((os.environ if environ is None else environ).get(setting) or "").strip()
    return raw or None


def configured_sessions(environ: Mapping[str, str] | None = None) -> Sessions:
    """The sessions `KINGFISHER_SESSIONS` names, or `LocalSessions`.

    Without it, `sessions` and `reap` would act on a local `sessions/`, not the deployment's.
    """
    named = _named("KINGFISHER_SESSIONS", environ)
    if named is None:
        return LocalSessions()
    return built_from_setting(named, setting="KINGFISHER_SESSIONS", interface=Sessions)


class CommandTurnInstructions:
    """The command's `TurnInstructions`, sending every graph what `--tell` gave it."""

    def __init__(self, said: str = "") -> None:
        self.said = said

    def text(self, cfg: Any, session_id: str, caller: Any) -> str:  # noqa: ARG002 -- the interface's
        return self.said
