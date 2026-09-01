"""Who is calling, and the one place that decides what that means over HTTP.

The server still authenticates nobody. What changed is that it now has to *ask*:
a deployment with an access policy cannot serve a single request without knowing
which groups the caller holds, so something has to turn a request into names.

That something is supplied, never assumed. `create_app(groups_from=...)` takes a
callable and this module ships one implementation of it -- `from_header` -- which
a deployment has to name explicitly. The header is an argument rather than a
setting on purpose: "we trust this header" is then a line somebody wrote, in a
file a reviewer reads, instead of a variable somebody set. A default header name
would make trusting one the thing that happens when nobody decides.

**A callable returns group names and nothing else.** `UNSCOPED` is deliberately
out of reach here. It exists to be a value a person types at a call site they
can see; reachable from a request it becomes a value a *bug* can produce, and a
callable returning it on a parse failure would hand every caller everything at
once. It is also unnecessary: "reaches everything" is already a group that
contains the others, which is declared in the vocabulary and visible in
`kingfisher list`, where an unscoped run would never appear.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

from kingfisher import AccessError

if TYPE_CHECKING:
    from fastapi import Request

#: What a deployment supplies: a request in, the caller's groups out.
#:
#: `Iterable[str]` rather than `Held`, which is the whole of the rule in the
#: type: there is no spelling of `UNSCOPED` a source can return.
#:
#: Imported for real rather than under `TYPE_CHECKING`, because this is
#: evaluated at module scope -- the same reason `dependencies` imports `Request`
#: for real, one line down from a comment saying so.
GroupsFrom = Callable[[Any], Iterable[str]]


class MissingGroupsError(AccessError):
    """The source could not say who is calling.

    An `AccessError` rather than a type of its own on the wire, because the two
    ways this deployment can fail to resolve a caller -- a header that is not
    set, and a group the vocabulary does not declare -- are the same problem
    seen from two places, and neither is the caller's to fix. A caller telling
    them apart would learn something about the deployment and could act on
    neither.
    """


def from_header(name: str) -> GroupsFrom:
    """Read the caller's groups from a header a gateway sets.

    The shipped implementation, and the shape most deployments want: something
    in front authenticates, resolves group membership, and states it on a
    header it also *strips from inbound requests*. That last part is the whole
    security of this arrangement and cannot be checked from here -- a header a
    caller can set is not identity, it is a request field.

    Comma-separated, and a repeated header is the same list. RFC 9110 defines
    those two as equivalent for a list-valued field, so accepting both is one
    spelling rather than two -- and comma-separated is what `--as A,B` already
    takes on the command.

    **An absent or empty header refuses.** A gateway that is meant to set this
    and did not is broken, and that is a different problem from a caller who
    reaches nothing -- read as "no groups" the two look identical, for every
    user at once, while the fixes are in completely different places. It is the
    same argument the closed vocabulary makes one layer down: a mistake must not
    wear a denial's clothes.
    """

    def read(request: Request) -> Iterable[str]:
        # `getlist` joined rather than `get`: starlette keeps repeated headers,
        # and a gateway emitting one per group is as correct as one emitting a
        # list.
        written = ",".join(request.headers.getlist(name))
        groups = tuple(part.strip() for part in written.split(",") if part.strip())
        if not groups:
            msg = (
                f"header {name!r} is not set on this request, so there is nothing "
                f"to resolve the caller's groups from. Whatever sits in front of "
                f"this server sets it -- and must strip it from inbound requests, "
                f"or a caller can name their own groups"
            )
            raise MissingGroupsError(msg)
        return groups

    return read
