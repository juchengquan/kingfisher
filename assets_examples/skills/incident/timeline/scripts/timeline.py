"""Every warning and error across a set of logs, in time order, repeats collapsed.

A skill's script, run by the shell and never imported. The skill reaches it as
`$KINGFISHER_SKILLS/incident/timeline/scripts/timeline.py` and hands it shell
paths -- `data/api.log`, not the file tools' `/data/api.log`.

Standard library only. A skill travels from one deployment to the next as files,
and nothing installs a dependency it would need.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise
from pathlib import Path

#: A line's timestamp, to the second. A line that does not open with one is
#: skipped and counted rather than placed by guesswork: an event filed at the
#: wrong time misleads a timeline more than one left out and said to be missing.
STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})")
LEVEL = re.compile(r"\b(CRITICAL|FATAL|ERROR|WARN(?:ING)?)\b")

#: What differs between two repeats of one event -- counts, ids, durations -- so
#: `retry 3 of 5` and `retry 4 of 5` collapse into one line with a count.
VARYING = re.compile(r"\b0x[0-9a-fA-F]+\b|\b[0-9a-fA-F]{8,}\b|\d+")

#: The shortest silence marked, in minutes.
GAP_MINUTES = 5

#: How much of one event's text is printed.
TEXT_WIDTH = 120

#: Lines printed before the rest are only counted.
MAX_LINES = 200


@dataclass
class Event:
    """One warning or error, and every later line that repeated it."""

    first: datetime
    last: datetime
    level: str
    where: str
    text: str
    count: int = 1


def _read(logs: list[Path]) -> tuple[list[Event], list[datetime], int]:
    """The collapsed events, every timestamp seen, and how many lines had none."""
    events: dict[tuple[str, str], Event] = {}
    seen: list[datetime] = []
    skipped = 0
    for log in logs:
        with log.open(encoding="utf-8", errors="replace") as handle:
            for number, line in enumerate(handle, 1):
                stamp = STAMP.match(line)
                if stamp is None:
                    skipped += 1
                    continue
                when = datetime.fromisoformat(f"{stamp[1]}T{stamp[2]}")
                seen.append(when)
                level = LEVEL.search(line)
                if level is None:
                    continue
                word = "WARN" if level[1].startswith("WARN") else level[1]
                text = line[level.end():].strip(" :-\t\r\n")
                key = (word, VARYING.sub("#", text))
                known = events.get(key)
                if known is None:
                    events[key] = Event(when, when, word, f"{log}:{number}", text)
                    continue
                known.count += 1
                known.last = max(known.last, when)
                # Logs are read one after another, so a later file can hold an
                # earlier repeat -- and the line cited has to be the first one.
                if when < known.first:
                    known.first, known.where, known.text = when, f"{log}:{number}", text
    return list(events.values()), seen, skipped


def _silences(stamps: list[datetime], gap: int) -> list[tuple[datetime, str]]:
    """Every stretch of at least `gap` minutes in which no log said anything."""
    marks = []
    for before, after in pairwise(stamps):
        minutes = int((after - before).total_seconds() // 60)
        if minutes >= gap:
            marks.append(
                (before, f"-- {before:%H:%M:%S} to {after:%H:%M:%S}: "
                         f"{minutes} minute(s) with no log lines")
            )
    return marks


def _row(event: Event) -> str:
    """One event as a line of the timeline."""
    text = event.text
    if len(text) > TEXT_WIDTH:
        text = text[: TEXT_WIDTH - 1] + "…"
    repeated = f"  (x{event.count}, until {event.last:%H:%M:%S})" if event.count > 1 else ""
    return f"{event.first:%Y-%m-%d %H:%M:%S}  {event.level:<8} {event.where}  {text}{repeated}"


def main(argv: list[str] | None = None) -> int:
    """Read the logs named on the command line and print their timeline."""
    parser = argparse.ArgumentParser(description="Warnings and errors across logs, in order.")
    parser.add_argument("logs", nargs="+", type=Path, help="log files, as shell paths")
    parser.add_argument(
        "--gap", type=int, default=GAP_MINUTES, help="shortest silence to mark, in minutes"
    )
    args = parser.parse_args(argv)
    try:
        events, seen, skipped = _read(args.logs)
    except OSError as exc:
        parser.error(str(exc))

    rows = sorted(
        [(event.first, _row(event)) for event in events] + _silences(sorted(seen), args.gap),
        key=lambda row: row[0],
    )
    out = [
        f"{len(seen)} timestamped line(s), {skipped} skipped for having no timestamp, "
        f"{len(events)} event(s)"
    ]
    out += [text for _, text in rows[:MAX_LINES]]
    if len(rows) > MAX_LINES:
        out.append(f"-- {len(rows) - MAX_LINES} more line(s) not shown")
    sys.stdout.write("\n".join(out) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
