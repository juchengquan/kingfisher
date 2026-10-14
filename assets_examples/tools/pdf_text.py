"""Save a PDF's text as a file the agent reads a range at a time, rather than whole.

**`read_file` hands a PDF over in one block,** whatever `offset` and `limit` say, so a
long report is the whole report in the model's context on every call after it. Saved
as text, one marked section per page, it is a file `read_file` pages through and
`grep` searches. So this is a tool whose purpose is a file, and it answers in the one
line `docs/guides/tools.md` sets out under *A tool that creates a file*.
"""

from __future__ import annotations

import io
from pathlib import PurePosixPath

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from kingfisher import ToolContext

#: Pages extracted in one call; the rest of what was asked for is named as left out.
#: Large, because the text goes to a file the agent searches, not into its context --
#: at fifty, an agent sampled a 300-page report and missed the page it was asked about.
#: Bounded, because each page is parsed here, beside every other turn: about 50 ms a
#: page on a dense report, so under half a minute. The docstring below names it too.
#: What that covers is time, and memory was the larger cost at this number -- the loop
#: below is what bounds it, and did not need this one to move.
MAX_PAGES = 500

#: What `pages` means when the caller leaves it out. A span stood here -- `1-500`, the
#: cap written into the default -- and a span is clipped to the document before the cap
#: is applied, so on a 503-page report the last three pages were dropped and the answer
#: said nothing was left out, while the same call naming `1-503` named them. The
#: docstring recommends leaving it out, so the recipe it recommends was the one that
#: reported least. A word rather than a span, because what the caller asked for is
#: every page there is.
EVERYTHING = "all"

KB = 1024


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
    """`1-20` or `3-5,9` as the first and last page of each range it names."""
    spans = []
    try:
        for part in pages.split(","):
            first, _, last = part.strip().partition("-")
            spans.append((int(first), int(last or first)))
    except ValueError:
        msg = f"pages {pages!r}: write page numbers and ranges, such as '1-20' or '3-5,9'"
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
def pdf_text(
    file_path: str,
    runtime: ToolRuntime[ToolContext],
    pages: str = EVERYTHING,
    save_as: str | None = None,
) -> str:
    """Save a PDF's text as a text file, under a `--- page N ---` line per page, and say
    where it went. Use it to read or search a PDF instead of `read_file` on the PDF,
    which hands over the whole document at once: search the saved text with `grep` to
    find what you need, and read a range of its lines with `read_file`.

    `file_path` is the same virtual path the file tools take, such as
    `/inputs/report.pdf`. `pages` names the pages to extract, such as `1-20` or
    `3-5,9`; leave it out, or pass `all`, for the whole document, up to 500 pages a
    call. The answer says how many pages the document has and which asked-for pages
    were left out — including the pages past the cap when you asked for all of them. A
    page with no text is usually a scan or a picture, and the answer names those too.

    `save_as` is where the text is saved, a full virtual path such as
    `/scratchpad/notes.txt`; leave it out for `/scratchpad/<name>.p<pages>.txt`, a file
    of its own for each range.
    """
    # Here rather than at the top: the wheel does not carry pdfplumber, and one tool
    # file that fails to import refuses every tool in the workspace, not only this one.
    import pdfplumber  # noqa: PLC0415
    from pdfplumber.utils.exceptions import (  # noqa: PLC0415
        MalformedPDFException,
        PdfminerException,
    )

    # Parsed before the file is fetched, so a `pages` nobody can read costs nothing to
    # find out about. `all` is the one value that cannot be read yet: it means every
    # page, and how many that is comes from the document below.
    asked = None if pages.strip().lower() == EVERYTHING else _asked(pages)
    data = _fetched(runtime, file_path)
    try:
        # Opened from the bytes in memory: a temporary file would be on kingfisher's
        # disk, not the session's. And no `repair=` or `.to_image()` -- the first runs
        # Ghostscript and the second native PDFium, here, outside every sandbox; text
        # is pdfminer, which is Python.
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            total = len(pdf.pages)
            present = _present(asked if asked is not None else [(1, total)], total)
            if not present:
                msg = f"{file_path} has {total} page(s); pages {pages!r} names none of them"
                raise ValueError(msg)
            # Page numbers count from one and the list from zero. Each page is released
            # as soon as its text is out: pdfplumber holds a page's objects and its text
            # map until the document closes, so at 500 pages the whole document's worth
            # was alive at once, in kingfisher's own process. Both calls are needed, and
            # that is the half a comprehension here would lose: measured on 500 dense
            # pages, 1,655 MB of peak RSS with neither, 1,020 and 1,626 with one, 93 with
            # both -- and a quarter faster.
            texts = []
            for n in present[:MAX_PAGES]:
                page = pdf.pages[n - 1]
                texts.append((n, page.extract_text()))
                page.flush_cache()
                page.get_textmap.cache_clear()
    except (PdfminerException, MalformedPDFException) as exc:
        msg = f"{file_path}: not a PDF this tool can read ({exc})"
        raise ValueError(msg) from exc

    done = [n for n, _ in texts]
    body = "".join(f"--- page {n} ---\n{text}\n" for n, text in texts)
    encoded = body.encode("utf-8")
    # Not `/outputs/`, which is what the caller is handed back: this is working
    # material. And the pages are in the name, so a second range does not replace
    # the first.
    target = save_as or (
        f"/scratchpad/{PurePosixPath(file_path).stem}.p{'_'.join(_ranges(done))}.txt"
    )

    (sent,) = runtime.context.backend.upload_files([(target, encoded)])
    if sent.error:
        msg = f"not saved to {target}: {sent.error}"
        raise OSError(msg)

    note = f"pages {', '.join(_ranges(done))} of {total}"
    if left := present[MAX_PAGES:]:
        note += f"; at most {MAX_PAGES} a call, so {', '.join(_ranges(left))} left out"
    if empty := [n for n, text in texts if not text.strip()]:
        note += f"; no text on {', '.join(_ranges(empty))}"
    name = PurePosixPath(target).name
    return f"Saved {name} (text, {_size(len(encoded))}) to {target} — {note}"


TOOLS = [pdf_text]
