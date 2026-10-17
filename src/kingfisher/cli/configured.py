"""What the command builds every `Kingfisher` with: the plugins its settings name.

The command, and not the core, because only the command has a setting and no argument
to be told them by -- and because it may name `LocalSessions` as the fallback, which
the core never does.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from kingfisher.core.wiring import store_named
from kingfisher.plugins import LocalSessions, Plugins, Sessions


def _named(setting: str, environ: Mapping[str, str] | None) -> str | None:
    """What a setting names, or `None` where it is unset or blank."""
    raw = ((os.environ if environ is None else environ).get(setting) or "").strip()
    return raw or None


def configured_sessions(environ: Mapping[str, str] | None = None) -> Sessions:
    """The sessions `KINGFISHER_SESSIONS` names, or `LocalSessions`.

    Without it, `kingfisher sessions` and `reap` would list and delete a local
    `sessions/` while a deployment's own keep them somewhere else.
    """
    named = _named("KINGFISHER_SESSIONS", environ)
    if named is None:
        return LocalSessions()
    return store_named(named, setting="KINGFISHER_SESSIONS", port=Sessions)


def configured_plugins(environ: Mapping[str, str] | None = None) -> Plugins:
    """The plugins `KINGFISHER_PLUGINS` names, or none, for kingfisher's own of each.

    A deployment whose turns record their events somewhere would otherwise have every
    command run without them.
    """
    named = _named("KINGFISHER_PLUGINS", environ)
    if named is None:
        return Plugins()
    return store_named(named, setting="KINGFISHER_PLUGINS", port=Plugins)
