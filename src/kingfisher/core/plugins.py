"""What the `kingfisher` command hands each `Kingfisher` it builds, beside its sessions."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from kingfisher.core.interfaces import RunEvents

if TYPE_CHECKING:
    from kingfisher.core.interfaces import Instructions


@dataclass(frozen=True)
class Plugins:
    """The plugins a deployment replaces for the command line, each left out for
    kingfisher's own.

    The library takes these as `Kingfisher`'s keywords. The command builds its own
    `Kingfisher` and has no call to be handed them on, so `KINGFISHER_PLUGINS` names a
    callable returning one of these, and the command passes on each field that is set.
    """

    #: A langgraph checkpointer every session shares, or a factory given a session id.
    threads: Any = None
    run_events: RunEvents | None = None
    middlewares: Mapping[str, Callable[..., Any]] | None = None
    instructions: Instructions | None = None

    def keywords(self) -> dict[str, Any]:
        """The fields that are set, as `Kingfisher`'s keywords."""
        return {name: value for name, value in vars(self).items() if value is not None}
