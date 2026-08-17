# kingfisher-service

kingfisher's HTTP surface — a consumer of the library, not part of it.

```bash
pip install 'kingfisher[service]'    # or: pip install kingfisher-service
kingfisher-service
```

Or point your own server at the app:

```bash
gunicorn -k uvicorn.workers.UvicornWorker kingfisher_service.asgi:app
```

`kingfisher_service.asgi:app`, not `kingfisher_service:app` — the latter
resolves to the `app` submodule rather than the application it defines.

## Why it ships separately

`pip install kingfisher` should not put a web service on disk. An optional
dependency group separates *dependencies*; only a second distribution separates
*code*, and this is the second kind — the library's wheel carries no part of it.

It could not be a subpackage of `kingfisher` even if that were wanted.
`kingfisher/__init__.py` exists, so the package belongs to the library's wheel,
and a second wheel adding a directory inside it breaks on upgrade and uninstall.

## What it may reach for

`from kingfisher import X`, and nothing deeper. No `kingfisher.domain`, no
`kingfisher.application`, no `kingfisher.infrastructure` — a rule this
package's own tests hold, and the reason the library exports what it does. Three
things became public because this needed them: the errors a caller must tell
apart, `async_checkpointer`, and there is still no way for a remote caller to
send a file.

The library holds the other direction, since packaging cannot: nothing in
`kingfisher` may import `kingfisher_service`, even when it is installed.

## Settings

Read from `KINGFISHER_SERVICE_*` — host, port, max body bytes, heartbeat, file
store directory, audit content.

`KINGFISHER_SERVER_*` is the old spelling and is still read, with a warning. It
will stop being read; rename when convenient. Both are honoured rather than one
being swapped for the other because that rename is the one that fails in
silence — an unread variable falls back to its default and the service comes up
on the wrong port with nothing to show for it.

## Versions

This depends on `kingfisher>=0.1,<0.2`. Under 0.x the minor is where breaks
land, and this calls the library's public names on every request — so a base
that moved ahead should stop the install rather than fail inside a request.
