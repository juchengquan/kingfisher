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
from pathlib import Path

from langchain_core.tools import tool

#: A line's date and minute. A line that does not open with one is counted as
#: undated rather than placed by guesswork: an error filed under the wrong minute
#: misleads a timeline more than one left out of it and counted.
STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})")
LEVEL = re.compile(r"\b(CRITICAL|FATAL|ERROR|WARN(?:ING)?)\b")

#: Minutes listed before the rest are only counted. A long incident has hundreds,
#: and the loudest are where to start reading.
MAX_MINUTES = 30


@tool
def log_levels(path: str) -> str:
    """Count a log's warnings and errors minute by minute, loudest minutes first.
    Use before reading a long log, to find the minutes worth reading.

    `path` is the same virtual path the file tools take -- `/data/<name>` --
    rooted at this session. Kingfisher resolves it before this runs.

    Reads lines that begin with a timestamp such as `2026-09-01T14:03:22`, and
    counts any other line as undated.
    """
    minutes: dict[str, Counter[str]] = {}
    total = undated = 0
    with Path(path).open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            total += 1
            stamp = STAMP.match(line)
            if stamp is None:
                undated += 1
                continue
            level = LEVEL.search(line)
            if level is not None:
                word = "WARN" if level[1].startswith("WARN") else level[1]
                minutes.setdefault(f"{stamp[1]} {stamp[2]}", Counter())[word] += 1

    head = f"{Path(path).name}: {total} line(s), {undated} undated"
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
    return "\n".join(lines)


TOOLS = [log_levels]
