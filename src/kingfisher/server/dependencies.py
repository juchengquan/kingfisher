"""What every route needs, in one place so the routers do not import the app.

`app.py` assembles; the routers are assembled into it. Putting the dependency
here rather than there is what keeps that direction one-way -- a router that
imported `create_app` to reach this would be a cycle, and the fix for a cycle
is usually a module like this one.
"""

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
    """The instance this app serves. One per process, per T4.

    The cost measured there is the process, not the instance -- resolving
    deepagents is 1310ms and 115MB, a further instance is 1.1ms and 0.16MB --
    so process count follows concurrency rather than tenancy. With identity
    outside this server, there is nothing here to key a registry on anyway.
    """
    return request.app.state.kingfisher
