"""What the command builds every `Kingfisher` with: the plugins its settings name.

The command, and not the core, because only the command has a setting and no argument
to be told them by -- and because it may name `LocalSessions` as the fallback, which
the core never does.
"""

from __future__ import annotations

from kingfisher import Config, LocalSessions, Plugins
from kingfisher.core.agent.backend import Sessions
from kingfisher.infrastructure.wiring import store_named


def configured_sessions(cfg: Config) -> Sessions:
    """The sessions `KINGFISHER_SESSIONS` names, or `LocalSessions`.

    Without it, `kingfisher sessions` and `reap` would list and delete a local
    `sessions/` while a deployment's own keep them somewhere else.
    """
    if cfg.sessions_factory is None:
        return LocalSessions()
    return store_named(cfg.sessions_factory, setting="KINGFISHER_SESSIONS", port=Sessions)


def configured_plugins(cfg: Config) -> Plugins:
    """The plugins `KINGFISHER_PLUGINS` names, or none, for kingfisher's own of each.

    A deployment whose turns record their events somewhere, or keep their conversations
    in a database, would otherwise have every command run without either.
    """
    if cfg.plugins_factory is None:
        return Plugins()
    return store_named(cfg.plugins_factory, setting="KINGFISHER_PLUGINS", port=Plugins)
