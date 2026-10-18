"""What a tool's result may hand the model besides text, and the files `read_file` is not
run on at all."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from deepagents.backends.utils import _get_backend_read_file_type
from langchain.agents.middleware import AgentMiddleware

from kingfisher.core.agent.middlewares.results import answer_in, answered, failed
from kingfisher.core.definitions.skills import READ_FILE

KB = 1024
MB = KB * KB

#: The largest PDF a tool result may hand the model whole, in bytes: half the ~500 KB at
#: which one PDF alone reaches the summarizer of a model with no profile.
MAX_PDF_BYTES = 256 * KB

#: The largest image a tool result may hand the model, in base64 characters, which is what
#: a provider measures: Bedrock's and Google Cloud's limit, the smallest (Anthropic's API
#: takes 10 MB). Past it the request is refused, and every one after while the image stays.
MAX_IMAGE_CHARS = 5 * MB

#: The tool recommended for a PDF too large to read whole, one saving its text a range at a
#: time. A documented convention: a tool called anything else is not recommended.
PDF_TEXT = "pdf_text"

PDF = "application/pdf"

#: The block types that carry a file. `audio` and `video` arrive only inside a `Command`:
#: anywhere else langgraph has made the whole result one JSON string first.
_CARRIERS = frozenset({"file", "image", "audio", "video"})


@dataclass(frozen=True)
class _Attachment:
    """One block that carries a file, as far as judging it needs."""

    type: str
    #: `""` where the block names none.
    mime: str
    #: The base64 it carries inline, 0 for a reference the provider fetches.
    chars: int
    inline: bool


def _attachment(block: Any) -> _Attachment | None:
    """The file a content block carries, or `None` for any other block."""
    kind = block.get("type")
    if kind not in _CARRIERS:
        return None
    url = block.get("url")
    if isinstance(url, str) and url.startswith("data:"):
        head, _, data = url.removeprefix("data:").partition(",")
        return _Attachment(kind, head.split(";")[0], len(data), inline=True)
    data = block.get("base64")
    return _Attachment(kind, block.get("mime_type") or "", len(data or ""), "base64" in block)


def _size(count: int) -> str:
    """A count of bytes or characters as a person reads one."""
    if count < MB:
        return f"{count / KB:.0f} KB"
    return f"{count / MB:.1f} MB"


def _unreadable(call: Mapping[str, Any]) -> str | None:
    """Why `read_file` is not run on this call's file, or `None` to run it.

    Audio and video become one JSON string of base64 before any middleware sees the
    result, so only refusing the call stops them; a `.pptx` is counted by the summarizer
    whichever model then drops it.
    """
    if call.get("name") != READ_FILE:
        return None
    path = (call.get("args") or {}).get("file_path")
    if not isinstance(path, str):
        return None
    kind = _get_backend_read_file_type(path)
    suffix = PurePosixPath(path).suffix.lower()
    if kind in ("audio", "video"):
        what, reads = kind, kind
    elif kind == "file" and suffix != ".pdf":
        what, reads = f"a {suffix} file", f"{suffix} files"
    else:
        return None
    return (
        f"Error: {path} is {what}, which can't be handed to the model here. If you hold a "
        f"tool that can read {reads}, call it on {path}; otherwise say that this part "
        "could not be done."
    )


class AttachmentsMiddleware(AgentMiddleware):
    """Replace a file a tool's result may not hand the model with advice, and refuse to run
    `read_file` on one no model here takes as it is."""

    def __init__(self) -> None:
        super().__init__()
        # What the model was last offered. `None` until a model call has passed: a call
        # replayed on a resume follows none, and is not told to call a tool it may not hold.
        self._offered: frozenset[str] | None = None

    def _seen(self, request: Any) -> Any:
        # Inside `GrantedOnlyMiddleware`, so these are the tools the model may call. Not the
        # catalogue's names: `standing_middleware` gets every tool, granted or not.
        self._offered = frozenset(
            name for tool in request.tools if isinstance(name := getattr(tool, "name", None), str)
        )
        return request

    def _pdf(self, where: str, size: int) -> str:
        text = (
            f"{where} is a {_size(size)} PDF, too large to hand over whole: it would be sent "
            "again with every model call after this one, and can end the turn."
        )
        if self._offered is not None and PDF_TEXT in self._offered:
            return (
                f"{text} Call {PDF_TEXT} on it to save its text, then read that a range at a "
                "time."
            )
        return f"{text} Work from its text, or from a part of it, instead."

    def _advice(self, where: str, block: Any) -> str | None:
        """What the model is told instead of `block`, or `None` to hand it over."""
        found = _attachment(block)
        if found is None:
            return None
        # By the type it names, whatever block carries it: an `image` block or a data URL
        # holding a PDF still sends the whole PDF with every call after.
        if found.mime == PDF:
            size = found.chars * 3 // 4
            return self._pdf(where, size) if size > MAX_PDF_BYTES else None
        if found.type == "image" or found.mime.startswith("image/"):
            if found.chars <= MAX_IMAGE_CHARS:
                return None
            return (
                f"{where} is a {_size(found.chars)} image, larger than the "
                f"{MAX_IMAGE_CHARS // MB} MB one image may be."
            )
        # A reference carries nothing the summarizer counts, and the provider fetches it.
        if not found.inline:
            return None
        what = f"a file of type {found.mime}" if found.mime else "a file of no stated type"
        return (
            f"{where} is {what}, which can't be handed to the model here. If you hold a tool "
            "that can read it, use that instead; otherwise say that this part could not be "
            "done."
        )

    def _guarded(self, request: Any, result: Any) -> Any:
        call = request.tool_call
        answer = answer_in(result, call.get("id"))
        if answer is None:
            return result
        where = answer.additional_kwargs.get("read_file_path") or f"{call.get('name')}'s result"
        blocks = answer.content_blocks
        advice = [self._advice(where, block) for block in blocks]
        if not any(advice):
            return result
        content = [
            block if said is None else {"type": "text", "text": said}
            for block, said in zip(blocks, advice, strict=True)
        ]
        # An error when nothing else came back, so the call reads as one that did not
        # give the model what it asked for.
        status = "error" if all(advice) else answer.status
        return answered(
            result, answer, answer.model_copy(update={"content": content, "status": status})
        )

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return handler(self._seen(request))

    async def awrap_model_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        return await handler(self._seen(request))

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        if (why := _unreadable(request.tool_call)) is not None:
            return failed(request.tool_call, why)
        return self._guarded(request, handler(request))

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        if (why := _unreadable(request.tool_call)) is not None:
            return failed(request.tool_call, why)
        return self._guarded(request, await handler(request))
