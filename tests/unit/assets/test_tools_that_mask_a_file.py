"""The bundled tool that masks a file's credentials, driven against awkward content.

Its coverage was that it loads and that one `sk-live-123` disappears -- so two of its
three patterns could be deleted with nothing red, the line cut it applies was reached
by no test, and the work its patterns do on a line nobody wrote by hand was never
measured. This is the one shipped tool written for files whose content nobody trusts,
which makes the content the thing to test it with.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from kingfisher.core.definitions.tools.catalogue import LocalToolRepository, tool_name


@pytest.fixture
def mask(shipped):
    """The tool's own function. It takes no runtime object beyond the context."""
    found = LocalToolRepository(shipped / "subagents" / "redactor" / "tools").found
    one = {tool_name(t.tool): t.tool for t in found}["mask_secrets"]
    return getattr(one, "func", one)


def _held(mask: Any) -> dict[str, Any]:
    return mask.__globals__


class _Got:
    def __init__(self, content: bytes) -> None:
        self.content, self.error = content, None


class _Runtime:
    """A backend holding one answer, so the tool's own work is what is under test."""

    def __init__(self, content: bytes) -> None:
        backend = type("_B", (), {"download_files": lambda _s, _p: [_Got(content)]})()
        self.context = type("_C", (), {"backend": backend})()


def _said(mask: Any, body: bytes, **asked: Any) -> str:
    return mask(file_path="/inputs/given.log", runtime=_Runtime(body), **asked)


#: One line per pattern that makes it work hardest: a long run the pattern can start
#: matching anywhere in and never finish. The email one is the measured fault -- `.`,
#: `-`, `+` and `%` sit in its character class and are word characters to nobody, so
#: each one starts a fresh `\b` the class can still consume through, and on a run with
#: no `@` every start position consumed to the end and backtracked the whole way.
PATHOLOGICAL = {
    "an address that never arrives": "1.",
    "a key that is never assigned": "secret",
    "an address of too many parts": "1.2.3.",
}

#: A line a log really can hold, kept under `MAX_ANSWER_CHARS` so this rule is about
#: the time and not about the answer bound -- a longer line is dropped with a note,
#: which `test_the_answer_is_bounded_in_characters_whatever_max_lines_says` is for.
#:
#: Measured on the pattern as shipped: 7.25s at 128 KB, 121s at 512 KB, clean n² --
#: about 5.5 days at the 32 MB this tool admits, on the turn's thread, where nothing
#: can stop it. So this size cost about ten seconds then and 16 milliseconds now. A
#: bound in seconds rather than a ratio between two timings, which is the flakier
#: assertion where the gap is three orders of magnitude.
PATHOLOGICAL_BYTES = 150 * 1024
WITHIN_SECONDS = 2.0


@pytest.mark.parametrize("about", sorted(PATHOLOGICAL))
def test_a_long_line_costs_time_in_proportion_to_its_length(about, mask):
    """Every repetition in `PATTERNS` is bounded, and this is what says so. Driven on
    the whole tool rather than on the pattern, because what a deployment runs is the
    tool, and a pattern asserted on its own could be the one the tool no longer uses.
    """
    run = PATHOLOGICAL[about]
    body = (run * (PATHOLOGICAL_BYTES // len(run))).encode() + b"\n"

    started = time.monotonic()
    said = _said(mask, body)
    took = time.monotonic() - started

    assert took < WITHIN_SECONDS, f"{len(body)} bytes of {run!r} took {took:.1f}s"
    assert said.startswith("given.log: 1 of 1 line(s)")


#: Each pattern's own kind, and text it must leave alone. One case per pattern,
#: because the answer is one count: with only a credential driven, the other two could
#: be deleted and the suite stayed green.
MASKS = {
    "a credential": ("api_key = sk-live-123", "sk-live-123", "<redacted>"),
    "an address": ("mail ada@example.com today", "ada@example.com", "<email>"),
    "a host": ("from 10.1.2.3 at once", "10.1.2.3", "<ip>"),
}


@pytest.mark.parametrize("about", sorted(MASKS))
def test_each_pattern_masks_its_own_kind(about, mask):
    """A tool whose whole answer is "three things masked" needs a case per thing."""
    line, secret, replacement = MASKS[about]

    said = _said(mask, line.encode() + b"\n")

    cleaned = said.splitlines()[-1]
    assert cleaned.count(replacement) == 1
    assert "1 masked" in said
    # The secret itself is named rather than taken from the line, so this cannot pass
    # by reading some other word out of it. How much *around* it goes is the pattern's
    # business: the credential one takes the whole assignment.
    assert secret not in cleaned


def test_a_file_longer_than_one_call_reads_says_how_many_lines_it_did_not(mask):
    """`0 masked` over the first fifth of a file is not "this file held nothing
    sensitive", and the skill next door tells the subagent to read the header as
    exactly that. Measured as shipped: a thousand-line file whose only credential sat
    on the last line answered `200 line(s), 0 masked`, with nothing saying 801 lines
    had not been looked at.
    """
    held = _held(mask)
    body = b"\n".join([b"clean line"] * 1000 + [b"api_key = sk-live-123"]) + b"\n"

    said = _said(mask, body)

    assert said.startswith(f"given.log: {held['DEFAULT_LINES']} of 1001 line(s), 0 masked")
    assert "801 line(s) not read" in said
    assert "raise max_lines" in said


def test_raising_max_lines_reaches_the_credential_the_default_did_not(mask):
    """The remedy the answer names, driven -- and the other half of the rule above: a
    note telling the caller to raise a number is worth nothing if raising it does not
    reach the rest of the file. This subagent holds no `read_file`, deliberately, so
    this is the only way it can look further.
    """
    body = b"\n".join([b"clean line"] * 1000 + [b"api_key = sk-live-123"]) + b"\n"

    said = _said(mask, body, max_lines=1001)

    assert said.startswith("given.log: 1001 of 1001 line(s), 1 masked")
    assert "not read" not in said
    assert "sk-live-123" not in said


def test_a_file_read_whole_names_no_remainder(mask):
    """The control the two rules above need: a note on every answer tells the caller a
    file was cut when it was not, which is the same confusion the other way round.
    """
    said = _said(mask, b"host = db.internal\nport = 5432\n")

    assert said.startswith("given.log: 2 of 2 line(s), 0 masked")
    assert "not read" not in said


@pytest.mark.parametrize("asked", [0, -1, 2_001])
def test_a_max_lines_the_tool_cannot_honour_is_refused(asked, mask):
    """`[:0]` answers `0 line(s)`, which reads as an empty file, and `[:-1]` silently
    drops the last line -- the one a credential is as likely to be on as any other.
    Both were accepted. The ceiling is refused for the reason it exists: the answer
    this tool gives is the remedy for a file longer than it read, and a remedy with no
    bound of its own hands a 32 MB file to the model whole.
    """
    with pytest.raises(ValueError, match="ask for 1 to"):
        _said(mask, b"api_key = sk-live-123\n", max_lines=asked)


def test_the_answer_is_bounded_in_characters_whatever_max_lines_says(mask):
    """Lines are not characters: two thousand lines of a minified payload is not two
    thousand lines of a log, and a tool result costs the model's context either way.
    """
    held = _held(mask)
    wide = b"x" * 10_000
    body = b"\n".join([wide] * 100) + b"\n"

    said = _said(mask, body, max_lines=held["MAX_LINES"])

    assert len(said) <= held["MAX_ANSWER_CHARS"] + len(said.splitlines()[0]) + 1
    assert "line(s) not read" in said


def test_the_docstring_names_both_bounds_it_applies(mask):
    """The model plans its call by these numbers and reads them here and nowhere else.
    Whitespace-normalised: the sentence wraps where the column ran out, and an `in`
    against the source sees a line break the model never does.
    """
    held = _held(mask)
    reads = " ".join((mask.__doc__ or "").split())

    assert f"{held['DEFAULT_LINES']} by default" in reads
    assert f"{held['MAX_LINES']} at most" in reads
    assert f"{held['MAX_ANSWER_CHARS']} characters" in reads


def test_the_shipped_file_is_the_one_these_rules_read(shipped):
    """This tool lives in a subagent's bundle, not in `tools/`, which is the whole
    point of it as an example -- and the reason six cap rules once walked past it. If
    it moves, these rules should fail rather than find nothing.
    """
    assert (shipped / "subagents" / "redactor" / "tools" / "mask_secrets.py").is_file()
    assert not (shipped / "tools" / "mask_secrets.py").exists()
    assert "mask_secrets" in Path(
        shipped / "subagents" / "redactor" / "redactor.yaml"
    ).read_text(encoding="utf-8")
