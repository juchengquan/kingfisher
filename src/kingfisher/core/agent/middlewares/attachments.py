"""What a tool's result may hand the model besides text, and the files `read_file` is not
run on at all."""

from __future__ import annotations

import threading
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Literal

from deepagents.backends.utils import _get_backend_read_file_type
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage

from kingfisher.core.definitions.skills import READ_FILE

from .recorded import readable_size
from .results import answer_in, answered, failed

KB = 1024
MB = KB * KB

#: The largest image a tool result may hand the model, in base64 characters, which is what
#: a provider measures: Bedrock's and Google Cloud's limit, the smallest (Anthropic's API
#: takes 10 MB). Past it the request is refused, and every one after while the image stays.
MAX_IMAGE_CHARS = 5 * MB

#: The images one turn may hold open, in base64 characters: half the 20 MB request Bedrock
#: takes, since each is sent again with every model call after it.
MAX_TURN_IMAGE_CHARS = 10 * MB

#: The tool recommended for a PDF too large to read whole, one saving its text a range at a
#: time. A documented convention: a tool called anything else is not recommended.
PDF_TEXT = "pdf_text"

PDF = "application/pdf"

#: The block types that carry a file. `audio` and `video` arrive only inside a `Command`:
#: anywhere else langgraph has made the whole result one JSON string first.
_CARRIERS = frozenset({"file", "image", "audio", "video"})


def pdf_allowance(trigger: int) -> int:
    """The PDF bytes one turn may hold open on a graph summarised at `trigger` tokens: half
    of it, at the summariser's token per four characters of base64, which carry three bytes."""
    return trigger * 3 // 2


@dataclass(frozen=True)
class _Open:
    """What a conversation holds open: PDFs in bytes, images in base64 characters."""

    pdf: int = 0
    image: int = 0

    def __add__(self, other: _Open) -> _Open:
        return _Open(self.pdf + other.pdf, self.image + other.image)


@dataclass(frozen=True)
class _Attachment:
    """One block that carries a file, as far as judging it needs."""

    kind: Literal["pdf", "image", "other"]
    #: `""` where the block names none.
    mime: str
    #: The base64 it carries inline, 0 for a reference the provider fetches.
    chars: int
    inline: bool

    @property
    def open(self) -> _Open:
        """What handing it over adds to what the turn holds open."""
        if self.kind == "pdf":
            return _Open(pdf=self.chars * 3 // 4)
        if self.kind == "image":
            return _Open(image=self.chars)
        return _Open()


def _attachment(block: Any) -> _Attachment | None:
    """The file a content block carries, or `None` for any other block."""
    carrier = block.get("type")
    if carrier not in _CARRIERS:
        return None
    url = block.get("url")
    if isinstance(url, str) and url.startswith("data:"):
        head, _, data = url.removeprefix("data:").partition(",")
        mime, chars, inline = head.split(";")[0], len(data), True
    else:
        data = block.get("base64")
        mime, chars, inline = block.get("mime_type") or "", len(data or ""), "base64" in block
    # By the type it names, whatever block carries it: an `image` block or a data URL
    # holding a PDF still sends the whole PDF with every call after.
    if mime == PDF:
        return _Attachment("pdf", mime, chars, inline)
    if carrier == "image" or mime.startswith("image/"):
        return _Attachment("image", mime, chars, inline)
    return _Attachment("other", mime, chars, inline)


def _conversation(request: Any) -> Sequence[Any]:
    """The graph's saved conversation, or none where the request carries no state."""
    state = getattr(request, "state", None)
    return state.get("messages", ()) if isinstance(state, Mapping) else ()


def _held(messages: Sequence[Any]) -> _Open:
    """Every PDF and image `messages` hold, those the summariser has folded away included,
    so the count can come out high and never low."""
    total = _Open()
    for message in messages:
        for block in getattr(message, "content_blocks", ()):
            if (found := _attachment(block)) is not None:
                total += found.open
    return total


def _asked_by(messages: Sequence[Any], call_id: Any) -> str | None:
    """The id of the message asking for `call_id`, which names the calls run beside it."""
    for message in reversed(messages):
        if isinstance(message, AIMessage) and any(
            one.get("id") == call_id for one in message.tool_calls
        ):
            return message.id
    return None


def _image(where: str, chars: int, beside: int) -> str | None:
    """What the model is told instead of an image beside `beside` already open, or `None`."""
    if chars > MAX_IMAGE_CHARS:
        return (
            f"{where} is a {readable_size(chars)} image, larger than the "
            f"{MAX_IMAGE_CHARS // MB} MB one image may be."
        )
    if beside + chars <= MAX_TURN_IMAGE_CHARS:
        return None
    return (
        f"{where} is a {readable_size(chars)} image. The images already open in this turn "
        f"total {readable_size(beside)}, and a turn holds at most "
        f"{MAX_TURN_IMAGE_CHARS // MB} MB; work from those, or say what is left."
    )


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

    def __init__(self, trigger: int) -> None:
        """`trigger` is where this graph's model is summarised, which sizes the PDFs its
        turn may hold open."""
        super().__init__()
        self._pdf_bytes = pdf_allowance(trigger)
        # What the model was last offered. `None` until a model call has passed: a call
        # replayed on a resume follows none, and is not told to call a tool it may not hold.
        self._offered: frozenset[str] | None = None
        # Handed over for calls not yet answered in the conversation, by the message asking
        # for them, since calls asked for together each see none of the others' answers.
        # Not reset at every model call: two runs of one subagent share this instance.
        self._passed: dict[str | None, _Open] = {}
        # Calls asked for together run on threads.
        self._lock = threading.Lock()

    def _seen(self, request: Any) -> Any:
        # Inside `GrantedOnlyMiddleware`, so these are the tools the model may call. Not the
        # catalogue's names: `standing_middleware` gets every tool, granted or not.
        self._offered = frozenset(
            name for tool in request.tools if isinstance(name := getattr(tool, "name", None), str)
        )
        if self._passed:
            # Answered by now, and counted from the conversation from here on.
            asked = {one.id for one in _conversation(request) if isinstance(one, AIMessage)}
            with self._lock:
                for done in asked & self._passed.keys():
                    del self._passed[done]
        return request

    def _pdf(self, where: str, size: int, beside: int) -> str | None:
        """What the model is told instead of a PDF beside `beside` already open, or `None`."""
        if size > self._pdf_bytes:
            text = (
                f"{where} is a {readable_size(size)} PDF, too large to hand over whole: it "
                "would be sent again with every model call after this one, and can end the "
                "turn."
            )
        elif beside + size > self._pdf_bytes:
            text = (
                f"{where} is a {readable_size(size)} PDF, and the PDFs already open in this "
                f"turn take up {readable_size(beside)} of the {readable_size(self._pdf_bytes)} "
                "a turn holds."
            )
        else:
            return None
        if self._offered is not None and PDF_TEXT in self._offered:
            return (
                f"{text} Call {PDF_TEXT} on it to save its text, then read that a range at a "
                "time."
            )
        return f"{text} Work from its text, or from a part of it, instead."

    def _advice(self, where: str, found: _Attachment, held: _Open) -> str | None:
        """What the model is told instead of `found`, or `None` to hand it over beside what
        the turn already `held` open."""
        if found.kind == "pdf":
            return self._pdf(where, found.open.pdf, held.pdf)
        if found.kind == "image":
            return _image(where, found.chars, held.image)
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
        blocks = answer.content_blocks
        found = [_attachment(block) for block in blocks]
        if not any(found):
            return result
        where = answer.additional_kwargs.get("read_file_path") or f"{call.get('name')}'s result"
        messages = _conversation(request)
        held = _held(messages)
        batch = _asked_by(messages, call.get("id"))
        advice: list[str | None] = []
        with self._lock:
            passed = self._passed.get(batch, _Open())
            for one in found:
                said = None if one is None else self._advice(where, one, held + passed)
                if one is not None and said is None:
                    passed += one.open
                advice.append(said)
            if passed != _Open():
                self._passed[batch] = passed
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
