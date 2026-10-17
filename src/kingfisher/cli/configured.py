"""The plugins the command's settings name, which every `Kingfisher` it builds is given.

Here and not in the core: only the command has settings and no argument to be told them by,
and only it may fall back to `LocalSessions`.
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

    Without it, `sessions` and `reap` would act on a local `sessions/`, not the deployment's.
    """
    named = _named("KINGFISHER_SESSIONS", environ)
    if named is None:
        return LocalSessions()
    return store_named(named, setting="KINGFISHER_SESSIONS", port=Sessions)


def configured_plugins(environ: Mapping[str, str] | None = None) -> Plugins:
    """The plugins `KINGFISHER_PLUGINS` names, or none, for kingfisher's own of each.

    Without it, a deployment recording its turns' events would have every command skip them.
    """
    named = _named("KINGFISHER_PLUGINS", environ)
    if named is None:
        return Plugins()
    return store_named(named, setting="KINGFISHER_PLUGINS", port=Plugins)
