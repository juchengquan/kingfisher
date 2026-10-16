"""The two shipped tools that summarise a log, driven against the shapes a log has.

Their coverage was that they load and that one line of each kind is counted, so of the
guards they carry only two bit: the cap on what the answer lists, the cap on what the
walk *holds*, the unparsed-line count and the busiest-codes cut were all deletable with
the suite green.

What the walk holds is the one the guide asks for by name -- cap the bytes, then measure
what the tool actually spends -- and for `log_levels` what it spends is one `Counter`
per distinct minute, which the bytes do not predict.
"""

from __future__ import annotations

import resource
from typing import Any

import pytest

from kingfisher.kinds.tools.catalogue import LocalToolRepository, tool_name

MB = 1024 * 1024


@pytest.fixture
def logs(shipped):
    """Both log tools, found the way a workspace finds them: under `logs/`."""
    found = LocalToolRepository(shipped / "tools" / "logs").tools
    return {tool_name(one): getattr(one, "func", one) for one in found}


def _held(tool: Any) -> dict[str, Any]:
    return tool.__globals__


class _Got:
    def __init__(self, content: bytes) -> None:
        self.content, self.error = content, None


class _Runtime:
    def __init__(self, content: bytes) -> None:
        backend = type("_B", (), {"download_files": lambda _s, _p: [_Got(content)]})()
        self.context = type("_C", (), {"backend": backend})()


def _said(tool: Any, body: bytes, name: str = "app.log") -> str:
    return tool(file_path=f"/inputs/{name}", runtime=_Runtime(body))


def _minute(n: int) -> str:
    """A distinct minute per `n`, walking a real calendar so every stamp parses."""
    year = 2000 + n // (1440 * 28 * 12)
    month, day = 1 + n // (1440 * 28) % 12, 1 + n // 1440 % 28
    return f"{year:04d}-{month:02d}-{day:02d}T{n // 60 % 24:02d}:{n % 60:02d}"


def test_a_minute_with_trouble_in_it_is_listed_with_its_counts(logs):
    """What the tool is for, and the control every rule below needs."""
    body = b"".join([
        b"2026-09-01T14:03:22 ERROR disk full\n",
        b"2026-09-01T14:03:59 WARNING retrying\n",
        b"2026-09-01T14:09:00 INFO fine\n",
        b"not a log line at all\n",
    ])

    said = _said(logs["log_levels"], body)

    assert said.splitlines()[0].startswith("app.log: 4 line(s), 1 undated")
    assert "2026-09-01 14:03  ERROR 1, WARN 1" in said


def test_a_line_with_no_timestamp_is_counted_and_said(logs):
    """The count a reader uses to judge whether the tool understood the file at all: a
    log in a format it cannot read answers `0 minute(s)` either way, and only this
    number tells the two apart. Deletable with the suite green.
    """
    body = b"\n".join([b"nothing here"] * 7 + [b"2026-09-01T00:00:00 ERROR x"]) + b"\n"

    said = _said(logs["log_levels"], body)

    assert said.splitlines()[0] == (
        "app.log: 8 line(s), 7 undated, warnings or errors in 1 minute(s) "
        "from 2026-09-01 00:00 to 2026-09-01 00:00"
    )


def test_the_minutes_listed_stop_at_the_cap_and_say_how_many_did_not(logs, monkeypatch):
    """`MAX_MINUTES` bounds the list, and raising it to a billion left the suite green,
    so the one bound this tool was known to have was held by its comment.

    Patched down rather than driven at its shipped value, and the fixture is a fixed
    size: written to build `cap + 5` minutes, this rule read the number it was testing,
    so mutating the cap to a billion made the *test* build a billion lines and the run
    never finished. A rule whose fixture is sized by its own subject cannot survive
    that subject being broken. `test_the_caps_are_bounds_rather_than_budgets` below is
    what holds the shipped numbers.
    """
    tool = logs["log_levels"]
    monkeypatch.setitem(_held(tool), "MAX_MINUTES", 2)
    body = "".join(f"{_minute(n)}:00 ERROR x\n" for n in range(7)).encode()

    said = _said(tool, body)

    listed = [line for line in said.splitlines() if line.startswith("  2")]
    assert len(listed) == 2
    assert "-- 5 quieter minute(s) not listed" in said


def test_what_the_walk_holds_is_bounded_and_the_answer_says_when_it_was(logs, monkeypatch):
    """`MAX_MINUTES` bounds what the answer *lists*; this bounds what the walk *holds*,
    which is the cost nobody had measured. One `Counter` per distinct minute is about
    350 bytes: measured, a 19 MB file of 883,011 distinct minutes peaked at 506 MB of
    resident memory against 199 MB for the same bytes in one minute.

    The note is asserted as a doubt about the list rather than as a count on its own,
    because the minutes kept are the first the walk met and not the loudest in the
    file -- so a louder one may be among those it dropped.
    """
    tool = logs["log_levels"]
    monkeypatch.setitem(_held(tool), "MAX_TRACKED_MINUTES", 3)
    body = "".join(f"{_minute(n)}:00 ERROR x\n" for n in range(43)).encode()

    said = _said(tool, body)

    assert "warnings or errors in 3 minute(s)" in said
    assert "40 line(s) in minutes past the first 3 were counted and not kept" in said
    assert "a louder minute may be among them" in said


def test_a_file_inside_both_bounds_carries_neither_note(logs):
    """The other half of the two rules above: a note on every answer tells a reader
    something was dropped from a summary that is whole.
    """
    body = b"2026-09-01T00:00:00 ERROR x\n2026-09-01T00:01:00 WARN y\n"

    said = _said(logs["log_levels"], body)

    assert "not listed" not in said
    assert "not kept" not in said


def test_the_busiest_codes_stop_at_the_cap(logs, monkeypatch):
    """`status_codes` lists the busiest and counts the rest, and `TOP_CODES = 1` left
    the suite green -- so the cut was held by nothing while the tool's answer is a
    ranking.
    """
    tool = logs["status_codes"]
    monkeypatch.setitem(_held(tool), "TOP_CODES", 2)
    lines = [
        f'1.2.3.4 - - [01/Sep/2026:00:00:00 +0000] "GET /x HTTP/1.1" {200 + n} 10'
        for n in range(6)
    ]

    said = _said(tool, ("\n".join(lines) + "\n").encode(), name="access.log")

    counted = [line for line in said.splitlines() if line.startswith("  ") and ":" in line]
    assert len(counted) <= 2, said


def test_a_request_line_it_cannot_read_is_counted_and_said(logs):
    """The same number as the undated count next door, for the same reason: a log in a
    format this tool cannot read answers nothing either way, and only this tells a
    reader which they got. Deletable with the suite green.
    """
    good = '1.2.3.4 - - [01/Sep/2026:00:00:00 +0000] "GET /x HTTP/1.1" 200 10'
    body = ("\n".join([good, "garbage", "also garbage"]) + "\n").encode()

    said = _said(logs["status_codes"], body, name="access.log")

    assert "2" in said.splitlines()[0], said


def test_the_accumulator_of_the_access_log_is_bounded_by_its_own_shape(logs):
    """The counterweight, and why only one of these two needed a cap: `status_codes`
    keys on a three-digit status, so its accumulator cannot hold more than a thousand
    entries however long the file. Measured rather than assumed, in a fresh reading of
    this process's own peak.
    """
    tool = logs["status_codes"]
    line = '1.2.3.4 - - [01/Sep/2026:00:00:00 +0000] "GET /x HTTP/1.1" {} 10\n'
    body = "".join(line.format(100 + n % 400) for n in range(200_000)).encode()
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / MB

    _said(tool, body, name="access.log")

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / MB
    assert peak - before < 20 * len(body) / MB, f"{peak - before:.0f} MB on {len(body)/MB:.0f} MB"


#: Each cap, and the most it can be and still be one. The rules above patch these
#: down so their fixtures stay small, which leaves the shipped numbers held by nothing:
#: `MAX_MINUTES = 10**9` and `TOP_CODES = 1` both left the suite green. A bound on the
#: bound is what catches that without a fixture the size of the cap.
AS_BOUNDS = {
    ("log_levels", "MAX_MINUTES"): (2, 100),
    ("log_levels", "MAX_TRACKED_MINUTES"): (1_000, 500_000),
    ("status_codes", "TOP_CODES"): (3, 50),
}


@pytest.mark.parametrize("which", sorted(AS_BOUNDS))
def test_the_caps_are_bounds_rather_than_budgets(which, logs):
    """Each of these says in its own comment that it is a guard and not a budget worth
    tuning, and a guard set to a billion is not one. The range is wide on purpose --
    this is not a pin on the number, it is a floor under the claim.
    """
    name, const = which
    low, high = AS_BOUNDS[which]

    held = _held(logs[name])[const]

    assert low <= held <= high, f"{const} is {held}"


def test_both_tools_are_found_where_a_workspace_finds_them(shipped):
    """They live in a folder of their own, and a walk that found neither would pass
    every rule above by having no cases.
    """
    found = {tool_name(one) for one in LocalToolRepository(shipped / "tools" / "logs").tools}

    assert found == {"log_levels", "status_codes"}
    assert (shipped / "tools" / "logs" / "access" / "status_codes.py").is_file()
