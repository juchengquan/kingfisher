"""kingfisher's HTTP surface — a consumer of the library, not part of it.

    from kingfisher.presentation import create_app
    app = create_app()

Installed with the `server` extra. Nothing in `kingfisher.domain`,
`kingfisher.application` or `kingfisher.infrastructure` imports this, and this
imports `kingfisher` and nothing deeper -- both halves enforced in
`tests/test_architecture.py` rather than left to habit.

The directory is `presentation/` and the command is `kingfisher-server`, which
is not an inconsistency left lying around. A directory is named for where it
sits in the dependency graph, and this one sits alongside `domain`,
`application` and `infrastructure` -- one vocabulary, so a reader can tell at
the top level what kind of thing each is. A command is named for what it
starts, at a person who wants a server running. `kingfisher-presentation` would
be a layer name pointed at the wrong audience, and so would the extra.

To serve it, point a server at `kingfisher.presentation.asgi:app` -- not at
`kingfisher.presentation:app`, which resolves to the `app` submodule. Or run
`kingfisher-server`, which does the same thing with the same settings.

That rule is the point of the split. It puts the server on the same footing as
anyone outside the package, so when it needs something the library does not
export, the answer is to export it deliberately. It found three such things
before a line of this was written: the errors a caller must tell apart were all
private, `async_checkpointer` was private while `astream` refuses to run
without it, and there is still no way for a remote caller to send a file.
"""

from kingfisher.presentation.app import create_app
from kingfisher.presentation.config import ServerConfig

__all__ = ["ServerConfig", "create_app"]
