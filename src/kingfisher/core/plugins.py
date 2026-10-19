"""What the `kingfisher` command hands each `Kingfisher` it builds, beside its sessions."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .interfaces import RunEvents

if TYPE_CHECKING:
    from .interfaces import Instructions


@dataclass(frozen=True)
class Plugins:
    """The plugins a deployment replaces for the command line; each left out is kingfisher's.

    The command builds its own `Kingfisher`, with no call to pass keywords through, so
    `KINGFISHER_PLUGINS` names a callable returning one of these instead.
    """

    run_events: RunEvents | None = None
    middlewares: Mapping[str, Callable[..., Any]] | None = None
    instructions: Instructions | None = None

    def keywords(self) -> dict[str, Any]:
        """The fields that are set, as `Kingfisher`'s keywords."""
        return {name: value for name, value in vars(self).items() if value is not None}
