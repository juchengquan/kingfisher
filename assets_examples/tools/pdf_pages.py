"""Save chosen pages of a PDF as a smaller PDF, for a model to look at.

**For a model that reads PDFs.** `read_file` hands a PDF over as a document the model
sees page by page, charts and scans included -- but only up to a size kingfisher
allows, and only to a model whose provider takes PDFs at all. This cuts the pages
worth looking at out of a long one. `pdf_text` is the way to a PDF's words on every
model; this is for what the words leave out. It answers in the one line
`docs/guides/tools.md` sets out under *A tool that creates a file*.
"""

from __future__ import annotations

import io
from pathlib import PurePosixPath

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from kingfisher import ToolContext

#: The largest PDF kingfisher hands the model whole; a larger one is replaced with
#: advice, so a slice past this is one the model would never see. A test holds it to
#: the guard's own limit, and the docstring below names it too.
MAX_SLICE_BYTES = 256 * 1024

KB = 1024


def _fetched(runtime: ToolRuntime[ToolContext], file_path: str) -> bytes:
    """The file's bytes, through the session's backend -- wherever that keeps them."""
    (got,) = runtime.context.backend.download_files([file_path])
    if got.error or got.content is None:
        msg = f"{file_path}: {got.error or 'no content'}"
        raise FileNotFoundError(msg)
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


def _ranges(numbers: list[int]) -> list[str]:
    """Sorted page numbers as runs: `[1, 2, 3, 5]` is `['1-3', '5']`."""
    runs: list[list[int]] = []
    for n in numbers:
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    return [f"{a}-{b}" if a != b else f"{a}" for a, b in runs]


def _size(count: int) -> str:
    """A byte count as a person reads one."""
    if count < KB:
        return f"{count} B"
    if count < KB * KB:
        return f"{count / KB:.1f} KB"
    return f"{count / KB / KB:.1f} MB"


@tool
def pdf_pages(
    file_path: str,
    pages: str,
    runtime: ToolRuntime[ToolContext],
    save_as: str | None = None,
) -> str:
    """Save some pages of a PDF as a new, smaller PDF, and say where it went. Use it to
    look at pages whose text is not enough -- a chart, a table's layout, a scanned
    page `pdf_text` found no text on -- then open the saved file with `read_file`. To
    read or search the words, use `pdf_text` instead.

    `file_path` is the same virtual path the file tools take, such as
    `/inputs/report.pdf`. `pages` names the pages, such as `7` or `3-5,9`. A slice
    larger than 256 KB cannot be handed to you whole, so it is refused rather than
    saved: ask for fewer pages.

    `save_as` is where the slice is saved, a full virtual path such as
    `/scratchpad/charts.pdf`; leave it out for `/scratchpad/<name>.p<pages>.pdf`.
    """
    # Here rather than at the top: the wheel does not carry pypdf, and one tool file
    # that fails to import refuses every tool in the workspace, not only this one.
    from pypdf import PdfReader, PdfWriter  # noqa: PLC0415
    from pypdf.errors import PyPdfError  # noqa: PLC0415

    asked = _asked(pages)
    data = _fetched(runtime, file_path)
    try:
        # Copied, not drawn: pypdf is Python, and a slice needs no renderer.
        reader = PdfReader(io.BytesIO(data))
        total = len(reader.pages)
        present = _present(asked, total)
        if not present:
            msg = f"{file_path} has {total} page(s); pages {pages!r} names none of them"
            raise ValueError(msg)
        writer = PdfWriter()
        for n in present:
            # Page numbers count from one and the list from zero.
            writer.add_page(reader.pages[n - 1])
        out = io.BytesIO()
        writer.write(out)
    except PyPdfError as exc:
        msg = f"{file_path}: not a PDF this tool can read ({exc})"
        raise ValueError(msg) from exc

    sliced = out.getvalue()
    shown = ", ".join(_ranges(present))
    if len(sliced) > MAX_SLICE_BYTES:
        limit = _size(MAX_SLICE_BYTES)
        if len(present) == 1:
            msg = (
                f"{file_path}: page {shown} alone is {_size(len(sliced))}, past the {limit} "
                "a PDF can be read whole"
            )
        else:
            msg = (
                f"{file_path}: pages {shown} come to {_size(len(sliced))}, past the {limit} "
                "a PDF can be read whole; ask for fewer pages"
            )
        raise ValueError(msg)

    # Not `/outputs/`, which is what the caller is handed back: this is working
    # material. And the pages are in the name, so a second slice does not replace
    # the first.
    target = save_as or (
        f"/scratchpad/{PurePosixPath(file_path).stem}.p{'_'.join(_ranges(present))}.pdf"
    )
    (sent,) = runtime.context.backend.upload_files([(target, sliced)])
    if sent.error:
        msg = f"not saved to {target}: {sent.error}"
        raise OSError(msg)

    name = PurePosixPath(target).name
    note = f"pages {shown} of {total}; open it with read_file to see them"
    return f"Saved {name} (PDF, {_size(len(sliced))}) to {target} — {note}"


TOOLS = [pdf_pages]
