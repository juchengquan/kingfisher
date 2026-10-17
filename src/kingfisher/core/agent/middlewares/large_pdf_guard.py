"""Handing a PDF too large to read whole back to the model as advice on reading it."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

#: The largest PDF a tool result may hand the model whole, in bytes: half the ~500 KB at
#: which one PDF alone reaches the summarizer of a model with no profile.
MAX_PDF_BYTES = 256 * 1024

#: The tool recommended for a PDF too large to read whole, one saving its text a range at a
#: time. A documented convention: a tool called anything else is not recommended.
PDF_TEXT = "pdf_text"

KB = 1024


def _pdf_bytes(block: Any) -> int:
    """The size of the PDF a content block carries inline, or 0 for any other block."""
    if block.get("type") != "file" or block.get("mime_type") != "application/pdf":
        return 0
    return len(block.get("base64") or "") * 3 // 4


def _size(count: int) -> str:
    """A byte count as a person reads one."""
    if count < KB * KB:
        return f"{count / KB:.0f} KB"
    return f"{count / KB / KB:.1f} MB"


class LargePdfGuard(AgentMiddleware):
    """Replace a PDF too large to read whole with advice on reading it in parts."""

    def __init__(self) -> None:
        super().__init__()
        # What the model was last offered. `None` until a model call has passed: a call
        # replayed on a resume follows none, and is not told to call a tool it may not hold.
        self._offered: frozenset[str] | None = None

    def _seen(self, request: Any) -> Any:
        # Before `ToolAllowlist` narrows, which is right: a graph holds only its granted
        # workspace tools. Not the catalogue's names: `tool_guards` gets every tool, granted
        # or not.
        self._offered = frozenset(
            name for tool in request.tools if isinstance(name := getattr(tool, "name", None), str)
        )
        return request

    def _advice(self, request: Any, result: ToolMessage, block: Any) -> dict[str, str]:
        call = request.tool_call
        where = result.additional_kwargs.get("read_file_path") or f"{call.get('name')}'s result"
        text = (
            f"{where} is a {_size(_pdf_bytes(block))} PDF, too large to hand over whole: it "
            "would be sent again with every model call after this one, and can end the turn."
        )
        if self._offered is not None and PDF_TEXT in self._offered:
            text += f" Call {PDF_TEXT} on it to save its text, then read that a range at a time."
        else:
            text += " Work from its text, or from a part of it, instead."
        return {"type": "text", "text": text}

    def _guarded(self, request: Any, result: Any) -> Any:
        # A `Command` passes untouched: `read_file` returns one only for a video, whose
        # frames are images.
        if not isinstance(result, ToolMessage):
            return result
        blocks = result.content_blocks
        large = [_pdf_bytes(block) > MAX_PDF_BYTES for block in blocks]
        if not any(large):
            return result
        content = [
            self._advice(request, result, block) if too else block
            for block, too in zip(blocks, large, strict=True)
        ]
        # An error when nothing else came back, so the call reads as one that did not
        # give the model what it asked for.
        status = "error" if all(large) else result.status
        return result.model_copy(update={"content": content, "status": status})

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return handler(self._seen(request))

    async def awrap_model_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        return await handler(self._seen(request))

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return self._guarded(request, handler(request))

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        return self._guarded(request, await handler(request))
