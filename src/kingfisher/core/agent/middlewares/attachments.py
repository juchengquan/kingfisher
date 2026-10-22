"""What a tool's result may hand the model besides text."""

from __future__ import annotations

import logging
import threading
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage

from kingfisher.core.interfaces import ReturnedPdf
from kingfisher.core.session.threads import off_loop, thread_pool

from .narrowing import tool_name
from .recorded import readable_size
from .results import answer_in, answered

if TYPE_CHECKING:
    from kingfisher.core.interfaces import PdfAttachments

_log = logging.getLogger(__name__)

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
    #: That base64, or `None` for a reference.
    data: str | None = None

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
        return _Attachment("pdf", mime, chars, inline, data if inline else None)
    if carrier == "image" or mime.startswith("image/"):
        return _Attachment("image", mime, chars, inline)
    return _Attachment("other", mime, chars, inline)


def _carries_a_pdf(result: Any, call_id: Any) -> bool:
    """Whether the answer to `call_id` in `result` carries a PDF."""
    answer = answer_in(result, call_id)
    return answer is not None and any(
        (found := _attachment(block)) is not None and found.kind == "pdf"
        for block in answer.content_blocks
    )


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


class AttachmentsMiddleware(AgentMiddleware):
    """Replace a file a tool's result may not hand the model with advice."""

    def __init__(self, trigger: int, pdfs: PdfAttachments | None = None) -> None:
        """`trigger` is where this graph's model is summarised, which sizes the PDFs its
        turn may hold open; `pdfs` is asked what to hand over in place of each PDF."""
        super().__init__()
        self._pdf_bytes = pdf_allowance(trigger)
        self._pdfs = pdfs
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
            name for tool in request.tools if (name := tool_name(tool)) is not None
        )
        if self._passed:
            # Answered by now, and counted from the conversation from here on.
            asked = {one.id for one in _conversation(request) if isinstance(one, AIMessage)}
            with self._lock:
                for done in asked & self._passed.keys():
                    del self._passed[done]
        return request

    def _verdict(self, size: int, beside: int) -> Literal["passes", "too_large", "turn_full"]:
        """Whether a PDF of `size` bytes may be handed over beside `beside` already open."""
        if size > self._pdf_bytes:
            return "too_large"
        if beside + size > self._pdf_bytes:
            return "turn_full"
        return "passes"

    def _pdf(self, where: str, size: int, beside: int) -> str | None:
        """What the model is told instead of a PDF beside `beside` already open, or `None`."""
        verdict = self._verdict(size, beside)
        if verdict == "passes":
            return None
        if verdict == "too_large":
            text = (
                f"{where} is a {readable_size(size)} PDF, too large to hand over whole: it "
                "would be sent again with every model call after this one, and can end the "
                "turn."
            )
        else:
            text = (
                f"{where} is a {readable_size(size)} PDF, and the PDFs already open in this "
                f"turn take up {readable_size(beside)} of the {readable_size(self._pdf_bytes)} "
                "a turn holds."
            )
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

    def _replacement(
        self, found: _Attachment, held: _Open, call: Mapping[str, Any], path: str | None
    ) -> list[Any] | None:
        """What `self._pdfs` hands over instead of the PDF `found`, as blocks, or `None`."""
        if self._pdfs is None:
            return None
        size = found.open.pdf
        pdf = ReturnedPdf(
            self._verdict(size, held.pdf),
            size=size,
            held=held.pdf,
            allowance=self._pdf_bytes,
            tool=call.get("name") or "",
            call_id=call.get("id") or "",
            path=path,
            _base64=found.data,
        )
        try:
            instead = self._pdfs.replacement(pdf)
        except Exception:  # noqa: BLE001 -- a deployment's hook failing leaves the verdict
            _log.warning(
                "the PDF attachments plugin raised on %s's result; kingfisher's verdict "
                "stands",
                pdf.tool,
                exc_info=True,
            )
            return None
        if instead is None:
            return None
        # Read as a tool's own result is, so text and blocks arrive as the blocks it makes.
        given = instead if isinstance(instead, str) else list(instead)
        blocks = ToolMessage(content=given, tool_call_id=pdf.call_id).content_blocks
        if not any(
            block.get("type") != "text" or block.get("text", "").strip() for block in blocks
        ):
            # Taken as handed over, nothing reached the model as an empty failed result
            # with no word why: to withhold a PDF, a plugin says so in text.
            _log.warning(
                "the PDF attachments plugin handed over nothing for %s's result; "
                "kingfisher's verdict stands",
                pdf.tool,
            )
            return None
        return blocks

    def _guarded(self, request: Any, result: Any) -> Any:
        call = request.tool_call
        answer = answer_in(result, call.get("id"))
        if answer is None:
            return result
        blocks = answer.content_blocks
        found = [_attachment(block) for block in blocks]
        if not any(found):
            return result
        path = answer.additional_kwargs.get("read_file_path")
        where = path or f"{call.get('name')}'s result"
        messages = _conversation(request)
        held = _held(messages)
        batch = _asked_by(messages, call.get("id"))
        content: list[Any] = []
        changed = False
        # Whether anything but kingfisher's own advice came back.
        given = False
        # Under the lock, the plugin too: the next PDF is judged beside what this one was
        # replaced with.
        with self._lock:
            passed = self._passed.get(batch, _Open())
            for block, one in zip(blocks, found, strict=True):
                instead = (
                    self._replacement(one, held + passed, call, path)
                    if one is not None and one.kind == "pdf"
                    else None
                )
                changed = changed or instead is not None
                # A replacement is judged as the result was, and never asked about again.
                for each in [block] if instead is None else instead:
                    judged = _attachment(each)
                    said = None if judged is None else self._advice(where, judged, held + passed)
                    if said is None:
                        if judged is not None:
                            passed += judged.open
                        content.append(each)
                        given = True
                    else:
                        content.append({"type": "text", "text": said})
                        changed = True
            if passed != _Open():
                self._passed[batch] = passed
        if not changed:
            return result
        # An error when nothing else came back, so the call reads as one that did not
        # give the model what it asked for.
        status = answer.status if given else "error"
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
        return self._guarded(request, handler(request))

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        result = await handler(request)
        if self._pdfs is None or not _carries_a_pdf(result, request.tool_call.get("id")):
            return self._guarded(request, result)
        # Off the loop: the plugin is a deployment's own code, and may well parse the PDF.
        return await off_loop(thread_pool(), self._guarded, request, result)
