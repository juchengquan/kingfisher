"""Who is calling, and the one place that decides what that means over HTTP."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

from kingfisher import AccessError

if TYPE_CHECKING:
    from fastapi import Request

#: What a deployment supplies: a request in, the caller's groups out.
GroupsFrom = Callable[[Any], Iterable[str]]


class MissingGroupsError(AccessError):
    """The source could not say who is calling."""


def from_header(name: str) -> GroupsFrom:
    """Read the caller's groups from a header a gateway sets.

    Comma-separated, and a repeated header is the same list. RFC 9110 defines those
    two as equivalent for a list-valued field, so accepting both is one spelling
    rather than two -- and comma-separated is what `--as A,B` already takes on the
    command.
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
