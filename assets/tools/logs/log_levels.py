"""How loud a log was, minute by minute, without reading it into the answer.

**In a folder with no `__init__.py`, which makes the folder organisation.** The
loader walks into `tools/logs/` and loads this file on its own, exactly as if it
sat in `tools/`: it declares its own `TOOLS`, and its tool is `log_levels`
wherever the file sits. `access/status_codes.py` beside it is the same, one
level deeper.

Neither may import the other. A loose file has no parent package, so a relative
import in one is refused -- the day two of these want a shared parser is the day
this folder should become a package, the way `csv_profile/` is.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import PurePosixPath

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from kingfisher import ToolContext

#: A line's date and minute. A line that does not open with one is counted as
#: undated rather than placed by guesswork: an error filed under the wrong minute
#: misleads a timeline more than one left out of it and counted.
STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})")
LEVEL = re.compile(r"\b(CRITICAL|FATAL|ERROR|WARN(?:ING)?)\b")

#: Minutes listed before the rest are only counted. A long incident has hundreds,
#: and the loudest are where to start reading.
MAX_MINUTES = 30

#: Minutes counted before the rest are only tallied. `MAX_MINUTES` above bounds what
#: the answer *lists*; this bounds what the walk *holds*, which is the cost nobody had
#: measured. One `Counter` per distinct minute is about 350 bytes: measured, a 19 MB
#: file of 883,011 distinct minutes peaked at 506 MB of resident memory against 199 MB
#: for the same bytes in one minute, and the 32 MB this tool admits reaches a million
#: and a half. `docs/guides/tools.md` says to cap the bytes and then measure what the
#: tool actually spends; for this one it is the number of keys.
#:
#: Fifty thousand is thirty-five days of every-minute trouble, past which a summary of
#: the loudest thirty is not what anybody needs, and holds about 18 MB.
MAX_TRACKED_MINUTES = 50_000


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


@tool
def log_levels(file_path: str, runtime: ToolRuntime[ToolContext]) -> str:
    """Count a log's warnings and errors minute by minute, loudest minutes first.
    Use before reading a long log, to find the minutes worth reading.

    `file_path` is the same virtual path the file tools take -- `/inputs/<name>` --
    rooted at this session, read through the session's backend.

    Reads lines that begin with a timestamp such as `2026-09-01T14:03:22`, and
    counts any other line as undated.
    """
    minutes: dict[str, Counter[str]] = {}
    total = undated = untracked = 0
    for line in _fetched(runtime, file_path).decode("utf-8", errors="replace").splitlines():
        total += 1
        stamp = STAMP.match(line)
        if stamp is None:
            undated += 1
            continue
        level = LEVEL.search(line)
        if level is not None:
            word = "WARN" if level[1].startswith("WARN") else level[1]
            minute = f"{stamp[1]} {stamp[2]}"
            if minute not in minutes and len(minutes) >= MAX_TRACKED_MINUTES:
                untracked += 1
                continue
            minutes.setdefault(minute, Counter())[word] += 1

    head = f"{PurePosixPath(file_path).name}: {total} line(s), {undated} undated"
    if not minutes:
        return f"{head}, no warnings or errors"
    loudest = sorted(minutes, key=lambda minute: (-minutes[minute].total(), minute))
    lines = [
        f"{head}, warnings or errors in {len(minutes)} minute(s) "
        f"from {min(minutes)} to {max(minutes)}"
    ]
    for minute in loudest[:MAX_MINUTES]:
        counts = ", ".join(f"{word} {n}" for word, n in minutes[minute].most_common())
        lines.append(f"  {minute}  {counts}")
    if len(loudest) > MAX_MINUTES:
        lines.append(f"  -- {len(loudest) - MAX_MINUTES} quieter minute(s) not listed")
    if untracked:
        # Said rather than silently dropped, and said as a doubt about the list above
        # rather than as a count on its own: the minutes kept are the first this walk
        # met, not the loudest in the file, so a louder one may be among these.
        lines.append(
            f"  -- {untracked} line(s) in minutes past the first {MAX_TRACKED_MINUTES} "
            f"were counted and not kept, so a louder minute may be among them"
        )
    return "\n".join(lines)


TOOLS = [log_levels]
