"""A name that came from outside and names somewhere it was not allowed to."""

from __future__ import annotations


class UnsafeReferenceError(ValueError):
    """A reference names somewhere other than where it was allowed to."""
