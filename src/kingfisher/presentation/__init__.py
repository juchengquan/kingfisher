"""What drives this library from outside it, for a person.

The fourth layer, and the one that keeps leaving. It was `server/`, then
`presentation/`, then a wheel of its own — `kingfisher-service` — and what came
back is the CLI, which had been sitting at the top level beside `domain/` and
`application/` as the odd one out.

Holding only the CLI is not half an answer. A distribution's presentation layer
is the presentation *it ships*; the service is a separate wheel with its own top
level, for a reason that has nothing to do with layering — a library caller
should not pay for fastapi and uvicorn to import `Request`.

What the two share is a contract rather than a directory, and it is enforced as
one. `CONSUMERS` in `test_architecture.py` names both and holds each to the
front door: `from kingfisher import X`, never `from kingfisher.domain.y import
X`. That rule spans distributions, which is the thing a folder could never do —
and the comment beside it is a scar from finding out, when the collector read
`SRC / name`, kept passing, and covered the CLI alone.

So the layer is here and the rule is there, and neither is trying to be the
other.
"""
