"""Middleware a workspace defines, as opposed to middleware a deployment wires.

The kind that could not exist until the definition roots stopped being writable
by the agent's shell: a middleware is wrapped *around* an agent, so a copy in the
directory the agent can edit was a cap the capped thing could rewrite.

A deployment still registers classes in its own program, which is the only way to
hand middleware a live object. `harness/middleware.py` builds what a definition
asked for from either source.
"""

from __future__ import annotations

from kingfisher.middleware.catalogue import (
    EXPORT,
    LocalMiddlewareRepository,
    MiddlewareError,
    NoMiddleware,
    name_of,
)

__all__ = [
    "EXPORT",
    "LocalMiddlewareRepository",
    "MiddlewareError",
    "NoMiddleware",
    "name_of",
]
