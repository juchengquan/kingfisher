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

## Who is calling

This server authenticates nobody, and that has not changed. What it now does is
*ask*: a deployment with a `groups.yaml` cannot serve a request without knowing
which groups the caller holds, so it is given something that says.

```python
from kingfisher_service import create_app
from kingfisher_service.identity import from_header

app = create_app(groups_from=from_header("X-Kingfisher-Groups"))
```

Or your own, for a JWT or a lookup — anything taking the request and returning
group names:

```python
def groups_from(request):
    return claims(request)["groups"]
```

**The header is an argument, not a setting**, and there is no default. Trusting
a header should be a line somebody wrote in a file a reviewer reads, rather than
what happens when nobody decides — and whatever sets that header **must strip it
from inbound requests**, or a caller names their own groups and the policy
decides nothing. That is the one part of this arrangement the code cannot check
for you.

A source returns names and nothing else: there is no way to say "run this one
unscoped" from a request. "Reaches everything" is a group that contains the
others, written in `groups.yaml` where it can be read.

**A deployment whose policy and identity disagree refuses to start**, both ways
round. A vocabulary with no source could not serve a single request; a source
with no vocabulary is a server somebody wired identity into and believes is
locked down, which is the worse of the two.

Refusals say little and log much. A session a caller cannot reach answers 404,
exactly as a wrong id does. A group the vocabulary does not declare answers 500
`misconfigured` without naming a group; the message that names them all goes to
the `kingfisher_service` logger, and the caller's groups go to the audit log —
which, like session ids, has no handler until you attach one.

## Settings

Read from `KINGFISHER_SERVICE_*` — host, port, max body bytes, heartbeat, file
store directory, audit content.

Not the group source: see above. It is code because it decides what identity
means here, and that is not a thing to set with a string.

`KINGFISHER_SERVER_*` is the old spelling and is still read, with a warning. It
will stop being read; rename when convenient. Both are honoured rather than one
being swapped for the other because that rename is the one that fails in
silence — an unread variable falls back to its default and the service comes up
on the wrong port with nothing to show for it.

## Versions

This depends on `kingfisher>=0.1,<0.2`. Under 0.x the minor is where breaks
land, and this calls the library's public names on every request — so a base
that moved ahead should stop the install rather than fail inside a request.
