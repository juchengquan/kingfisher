"""kingfisher's HTTP surface — a consumer of the library, not part of it.

    from kingfisher_service import create_app
    app = create_app()

Its own distribution, `kingfisher-service`, installed with
`pip install kingfisher[service]` or by name. Not a subpackage of `kingfisher`
and it cannot be one: `kingfisher` is a normal package owned by the base wheel,
so a second wheel adding a directory inside it breaks on upgrade and uninstall.

It used to be `kingfisher.presentation`, named for where it sat in the
dependency graph -- alongside `domain`, `application` and `infrastructure`, one
vocabulary so a reader could tell at the top level what kind of thing each was.
That argument went with the move: this no longer sits alongside them. What is
left is the audience, which wanted the word *service* all along, and so now do
the wheel, the import, the command and the extra.

Nothing in `kingfisher` imports this -- checked by the library's own
architecture test, because packaging does not enforce that direction -- and this
imports `kingfisher` and nothing deeper, checked here.

To serve it, point a server at `kingfisher_service.asgi:app` -- not at
`kingfisher_service:app`, which resolves to the `app` submodule. Or run
`kingfisher-service`, which does the same thing with the same settings.

That rule is the point of the split. It puts the service on the same footing as
anyone outside the package, so when it needs something the library does not
export, the answer is to export it deliberately. It found three such things
before a line of this was written: the errors a caller must tell apart were all
private, `async_checkpointer` was private while `astream` refused to run without
it, and there is still no way for a remote caller to send a file.

The second of those has since answered itself. `astream` takes the in-memory
saver the library now defaults to, so this holds no database open and passes no
`threads=` at all -- which also drops the one-database-per-process shape that
came with it.
"""

from kingfisher_service.app import create_app
from kingfisher_service.config import ServiceConfig

__all__ = ["ServiceConfig", "create_app"]
