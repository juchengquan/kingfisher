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
    so process count follows concurrency rather than tenancy.

    Still one per process now that callers have identity, and that is the point
    of `for_groups` returning a handle: the catalogue, the session store and the
    per-session locks belong to the deployment rather than to whoever is
    calling, so a caller is a value passed through rather than an instance to
    key a registry on.
    """
    return request.app.state.kingfisher


def groups_of(request: Request) -> tuple[str, ...] | None:
    """The caller's groups, from whatever this deployment wired.

    `None` where no source is wired, which the library reads as a deployment
    that controls nothing by group -- exactly what it read before any of this
    existed. That default is only safe because `create_app` refuses a
    deployment that has a policy and no source: the refusal and this `None` are
    one mechanism, and weakening the first turns the second into "serve
    everyone everything", silently. The test for the refusal is what guards it.

    A tuple rather than whatever the source returned, so that one conversion
    happens here instead of at each of the five routes -- and so a source
    handing back a generator cannot be consumed by the first reader and empty
    for the second.
    """
    source = request.app.state.groups_from
    return None if source is None else tuple(source(request))
