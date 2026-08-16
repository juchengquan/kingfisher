"""kingfisher's HTTP surface — a consumer of the library, not part of it.

    from kingfisher.server import create_app
    app = create_app()

Installed with the `server` extra. Nothing in `kingfisher.domain`,
`kingfisher.application` or `kingfisher.infrastructure` imports this, and this
imports `kingfisher` and nothing deeper -- both halves enforced in
`tests/test_architecture.py` rather than left to habit.

To serve it, point a server at `kingfisher.server.asgi:app` -- not at
`kingfisher.server:app`, which resolves to the `app` submodule. Or run
`kingfisher-server`, which does the same thing with the same settings.

That rule is the point of the split. It puts the server on the same footing as
anyone outside the package, so when it needs something the library does not
export, the answer is to export it deliberately. It found three such things
before a line of this was written: the errors a caller must tell apart were all
private, `async_checkpointer` was private while `astream` refuses to run
without it, and there is still no way for a remote caller to send a file.
"""

from kingfisher.server.app import create_app
from kingfisher.server.config import ServerConfig

__all__ = ["ServerConfig", "create_app"]
