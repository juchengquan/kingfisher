"""Draw chosen pages of a PDF as PNG images, for a model to look at.

**For a model that reads images.** `pdf_pages` hands a model that reads PDFs a smaller
PDF; this draws the pages instead, so a chart or a scan reaches any model that takes an
image. Drawing is PDFium, native code reading a file nobody vouched for, so it runs in
the session's fenced shell through `runtime.context.run`, never in kingfisher's process
-- and only for an agent that may use the shell itself. It answers in the lines
`docs/guides/tools.md` sets out under *A tool that creates a file*.
"""

from __future__ import annotations

import sys
from pathlib import PurePosixPath

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from kingfisher import ScratchRun, ToolContext

#: The largest image `read_file` hands a model whole: the guard's `MAX_IMAGE_CHARS` of
#: base64, as bytes. A test holds it to the guard's.
MAX_PAGE_BYTES = 5 * 1024 * 1024 * 3 // 4

#: Pages drawn in one call.
MAX_PAGES = 10

#: How large a page is drawn: 1 is 72 dots to the inch.
MAX_SCALE = 2.0

KB = 1024

#: What a shell says when the program it was asked to start is not there.
NOT_FOUND = 127

#: Run in the fence as `<python> -I -c`, against the copy of the PDF in its folder: `count`
#: prints the pages, and any other word draws the pages named after the scale.
DRAW = """
import sys
import pypdfium2

pdf = pypdfium2.PdfDocument("doc.pdf")
if sys.argv[1] == "count":
    print(len(pdf))
else:
    for number in map(int, sys.argv[3:]):
        pdf[number - 1].render(scale=float(sys.argv[2])).to_pil().save(f"p{number}.png")
"""


#: What one call reads. A backend hands a file back whole, into kingfisher's own
#: process, and nothing else bounds it: `docs/guides/tools.md` has the reason under
#: *A tool that reads a file*.
MAX_INPUT_BYTES = 32 * 1024 * 1024


def _fetched(runtime: ToolRuntime[ToolContext], file_path: str) -> bytes:
    """The file's bytes, through the session's backend -- wherever that keeps them."""
    (got,) = runtime.context.backend.download_files([file_path])
    if got.error or got.content is None:
        msg = f"{file_path}: {got.error or 'no content'}"
        raise FileNotFoundError(msg)
    if len(got.content) > MAX_INPUT_BYTES:
        msg = (
            f"{file_path} is {len(got.content)} bytes, past the {MAX_INPUT_BYTES} this "
            f"tool reads in one call -- ask for a smaller file, or a part of this one"
        )
        raise ValueError(msg)
    return got.content


def _asked(pages: str) -> list[tuple[int, int]]:
    """`7` or `3-5,9` as the first and last page of each range it names."""
    spans = []
    try:
        for part in pages.split(","):
            first, _, last = part.strip().partition("-")
            spans.append((int(first), int(last or first)))
    except ValueError:
        msg = f"pages {pages!r}: write page numbers and ranges, such as '7' or '3-5,9'"
        raise ValueError(msg) from None
    return spans


def _present(spans: list[tuple[int, int]], total: int) -> list[int]:
    """The pages the spans name that the document has, in order and once each.

    Clipped before expanding, because `1-99999999` is one typo away from `1-99`.
    """
    return sorted({n for a, b in spans for n in range(max(a, 1), min(b, total) + 1)})


def _size(count: int) -> str:
    """A byte count as a person reads one."""
    if count < KB:
        return f"{count} B"
    if count < KB * KB:
        return f"{count / KB:.1f} KB"
    return f"{count / KB / KB:.1f} MB"


def _ran(
    runtime: ToolRuntime[ToolContext],
    file_path: str,
    data: bytes,
    args: list[str],
    outputs: list[str] | None = None,
) -> ScratchRun:
    """One run of `DRAW` over the PDF, or the reason it failed, raised."""
    # This process's own interpreter, which has pypdfium2: `python` on the shell's `PATH`
    # need not exist, and was missing on a CI runner.
    ran = runtime.context.run(
        [sys.executable, "-I", "-c", DRAW, *args], {"doc.pdf": data}, outputs=outputs or []
    )
    if ran.error:
        raise PermissionError(ran.error)
    if ran.exit_code == NOT_FOUND:
        msg = f"the session's shell could not start Python: {ran.output.strip()[-300:]}"
        raise OSError(msg)
    if ran.exit_code != 0:
        msg = f"{file_path}: not a PDF this tool can draw ({ran.output.strip()[-300:]})"
        raise ValueError(msg)
    return ran


@tool
def pdf_render(
    file_path: str,
    pages: str,
    runtime: ToolRuntime[ToolContext],
    scale: float = 1.0,
) -> str:
    """Draw some pages of a PDF as PNG images, and say where each went. Use it to look at
    pages whose text is not enough -- a chart, a table's layout, a scanned page -- then
    open an image with `read_file`. To read or search the words, use `pdf_text` instead.

    `file_path` is the same virtual path the file tools take, such as
    `/inputs/report.pdf`. `pages` names at most 10 pages, such as `7` or `3-5,9`.
    `scale` is how large to draw them: 1 is 72 dots to the inch, 2 at most. Each page is
    saved as `/scratchpad/<name>.p<page>.png`.
    """
    if not 0 < scale <= MAX_SCALE:
        msg = f"scale {scale}: draw pages at more than 0 and at most {MAX_SCALE}"
        raise ValueError(msg)
    asked = _asked(pages)
    data = _fetched(runtime, file_path)

    counted = _ran(runtime, file_path, data, ["count"])
    total = int(counted.output.split()[0])
    present = _present(asked, total)
    if not present:
        msg = f"{file_path} has {total} page(s); pages {pages!r} names none of them"
        raise ValueError(msg)
    if len(present) > MAX_PAGES:
        msg = (
            f"{file_path}: pages {pages!r} name {len(present)}, past the {MAX_PAGES} "
            "drawn at once"
        )
        raise ValueError(msg)

    drawn = _ran(
        runtime,
        file_path,
        data,
        ["draw", str(scale), *map(str, present)],
        [f"p{n}.png" for n in present],
    )
    stem = PurePosixPath(file_path).stem
    lines = []
    for n in present:
        image = drawn.files.get(f"p{n}.png")
        if image is None:
            msg = f"{file_path}: page {n} was not drawn ({drawn.missing.get(f'p{n}.png')})"
            raise ValueError(msg)
        if len(image) > MAX_PAGE_BYTES:
            msg = (
                f"{file_path}: page {n} drawn at scale {scale} is {_size(len(image))}, past "
                f"the {_size(MAX_PAGE_BYTES)} an image can be read whole; draw it smaller"
            )
            raise ValueError(msg)
        # Not `/outputs/`, which is what the caller is handed back: this is working material.
        target = f"/scratchpad/{stem}.p{n}.png"
        (sent,) = runtime.context.backend.upload_files([(target, image)])
        if sent.error:
            msg = f"not saved to {target}: {sent.error}"
            raise OSError(msg)
        name = PurePosixPath(target).name
        note = f"page {n} of {total}; open it with read_file to see it"
        lines.append(f"Saved {name} (PNG, {_size(len(image))}) to {target} — {note}")
    return "\n".join(lines)


TOOLS = [pdf_render]
