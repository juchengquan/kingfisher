"""What every route needs, in one place so the routers do not import the app."""

from __future__ import annotations

from typing import TYPE_CHECKING

# Imported for real rather than under `TYPE_CHECKING`: fastapi resolves these
# annotations at runtime to decide what each parameter *is*. Left as a string it
# cannot resolve, `request` is read as a query parameter and every route answers
# 422 asking for one.
from fastapi import Request

if TYPE_CHECKING:
    from kingfisher import Kingfisher


def kingfisher_of(request: Request) -> Kingfisher:
    """The instance this app serves.

    The cost measured there is the process, not the instance -- resolving deepagents
    is 1310ms and 115MB, a further instance is 1.1ms and 0.16MB -- so process count
    follows concurrency rather than tenancy.
    """
    return request.app.state.kingfisher


def groups_of(request: Request) -> tuple[str, ...] | None:
    """The caller's groups, from whatever this deployment wired."""
    source = request.app.state.groups_from
    return None if source is None else tuple(source(request))
