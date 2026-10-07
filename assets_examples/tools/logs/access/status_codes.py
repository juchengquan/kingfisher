"""What an access log answered, by status code, without reading it.

Two folders down, and still loaded on its own: neither `tools/logs/` nor
`tools/logs/access/` holds an `__init__.py`, so the loader walks both and reads
this file as it would one directly under `tools/`. Depth reaches no name either
-- a grant writes `status_codes`, and moving the file changes nothing a caller
types.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import PurePosixPath

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from kingfisher import ToolContext

#: The status after the quoted request in the common and combined log formats,
#: as in `"GET /orders HTTP/1.1" 503 1187`. Any other line is counted as
#: unparsed, and a large count means the log is in another format -- not that
#: the service answered nothing.
STATUS = re.compile(r'"\s+(\d{3})\s')

#: Codes listed under each class; the class total counts the rest.
TOP_CODES = 5


def _fetched(runtime: ToolRuntime[ToolContext], file_path: str) -> bytes:
    """The file's bytes, through the session's backend -- wherever that keeps them."""
    (got,) = runtime.context.backend.download_files([file_path])
    if got.error or got.content is None:
        msg = f"{file_path}: {got.error or 'no content'}"
        raise FileNotFoundError(msg)
    return got.content


@tool
def status_codes(file_path: str, runtime: ToolRuntime[ToolContext]) -> str:
    """Count the HTTP status codes in an access log, grouped by class. Use to size
    an outage -- how many requests failed, and with which codes -- without reading
    the log.

    `file_path` is the same virtual path the file tools take -- `/inputs/<name>` --
    rooted at this session, read through the session's backend.
    """
    codes: Counter[str] = Counter()
    unparsed = 0
    for line in _fetched(runtime, file_path).decode("utf-8", errors="replace").splitlines():
        found = STATUS.search(line + "\n")
        if found is None:
            unparsed += 1
        else:
            codes[found[1]] += 1

    name = PurePosixPath(file_path).name
    total = codes.total()
    if not total:
        return (
            f"{name}: no status codes in {unparsed} line(s) -- "
            f"not a common-format access log"
        )
    lines = [f"{name}: {total} request(s), {unparsed} unparsed line(s)"]
    for family in sorted({code[0] for code in codes}):
        mine = Counter({code: n for code, n in codes.items() if code[0] == family})
        lines.append(f"{family}xx: {mine.total()} ({mine.total() / total:.1%})")
        lines += [f"  {code}: {n}" for code, n in mine.most_common(TOP_CODES)]
    return "\n".join(lines)


TOOLS = [status_codes]
