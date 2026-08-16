"""An application for a server to point at: `kingfisher.server.asgi:app`.

A module whose only job is to exist under a name uvicorn can resolve. It has to
be a separate one, which is not obvious until you try: the package already has a
submodule called `app`, so `kingfisher.server:app` resolves to that module and
whatever is served is not an application at all. A `__getattr__` cannot rescue
it either -- importing the submodule binds the name first.

Built at import, deliberately. Laziness is worth having where a module is
imported for other reasons; this one exists to be the application, and a server
resolving it is the only reason it is ever loaded.

Configured from the environment, by the same `ServerConfig.from_env` the entry
point uses, so the two ways of starting a server cannot disagree.
"""

from __future__ import annotations

from kingfisher.server.app import create_app

app = create_app()
