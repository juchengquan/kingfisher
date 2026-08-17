"""A workspace tool that exists so seeding has Python to copy."""

from __future__ import annotations

from langchain_core.tools import tool


@tool
def probe(text: str) -> str:
    """Return what it was given. A fixture, not a capability."""
    return text


TOOLS = [probe]
