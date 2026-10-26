"""What `kingfisher prompt` prints: what the model would be sent, part by part."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from kingfisher import SENT_SOURCES

if TYPE_CHECKING:
    from collections.abc import Iterator

    from kingfisher import Sent


def render(sent: Sent) -> Iterator[str]:
    """The system message block by block, then the tools, each under a line naming it."""
    yield (
        f"What agent {sent.agent!r} would be sent in session {sent.session_id!r}. "
        "No model was called."
    )
    written = sum(len(block.text) for block in sent.system)
    yield ""
    yield f"system message: {len(sent.system)} blocks, {written:,} characters"
    for number, block in enumerate(sent.system, 1):
        yield ""
        yield (
            f"── {number} of {len(sent.system)} · {SENT_SOURCES[block.source]} · "
            f"{len(block.text):,} characters{_marked(cached=block.cached)}"
        )
        yield block.text.strip("\n")
    described = sum(len(tool.description) for tool in sent.tools)
    yield ""
    yield f"tools: {len(sent.tools)}, {described:,} characters of description"
    for tool in sent.tools:
        kind = "offered by the definitions" if tool.offered else "built in"
        yield ""
        yield (
            f"── {tool.name} · {kind} · {len(tool.description):,} characters"
            f"{_marked(cached=tool.cached)}"
        )
        yield tool.description.strip("\n")


def as_document(sent: Sent) -> dict[str, Any]:
    """The same, as JSON takes it, every tool's parameters included."""
    return asdict(sent)


def _marked(*, cached: bool) -> str:
    return " · cache breakpoint" if cached else ""
