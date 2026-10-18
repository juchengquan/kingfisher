"""Return a file's text with anything that looks like a credential masked.

**This tool ships inside a bundle, and that is the example.** It sits in
`subagents/redactor/tools/` rather than in the catalogue's `tools/`, and
`redactor` lists it as `source: bundled`, so it reaches that subagent and
nothing else -- not the agent that summoned it, not another subagent, not a
request that names it.

The reason is the shape of the tool rather than a policy someone chose. It
returns file contents, which is a bulk read wearing a safety feature: handed to
the main agent it is a way to pull a whole file into the answer while *looking*
like the careful option, and the masking says nothing about whether that file
should have been read at all. It is only the right tool inside the procedure
`redactor.yaml` next door sets out, and the folder is what keeps the two together.

A catalogue tool could not express that. An agent writing `tools: ["*"]`
holds every tool there is, so anything in `tools/` is something the top-level
agent can call; a bundle is the only place a capability can sit that it cannot.

Written as a plain function for the reason `line_count` gives: the docstring is
the description the model reads, the annotations are the argument schema, and
this tool wants no control over either.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from langchain.tools import ToolRuntime

from kingfisher import ToolContext

#: What gets masked. Deliberately crude and deliberately named: this is an
#: example of *where a tool lives*, not a redaction library, and a pattern list
#: that looked authoritative would invite someone to trust it as one.
#:
#: Every repetition here is bounded, and that is not tidiness. The address pattern
#: was `[A-Za-z0-9._%+-]+@...`, and `.`, `-`, `+` and `%` are in that class while
#: being word characters to nobody -- so each one starts a fresh `\b` the class can
#: still consume through. On a run with no `@` every start position consumed to the
#: end of the run and backtracked the whole way: measured, a single line of 128 KB
#: took 7.25 seconds, 1 MB took 472, and the shape is clean n² -- about 5.5 days at
#: the 32 MB this tool admits, on the turn's thread, in kingfisher's process, where
#: nothing can stop it. The input is file content, and this is the tool written for
#: files whose content nobody trusts.
#:
#: The bounds come from the standard rather than being picked: RFC 5321 gives 64
#: octets for a local part and 255 for a domain. That makes each start position cost
#: at most its own bound instead of the rest of the line -- measured linear, and 440
#: times faster at 64 KB.
#:
#: Possessive quantifiers are the other obvious answer and are the wrong one twice
#: over: `[A-Za-z0-9.-]++\.` cannot give back the dot the pattern then needs, so it
#: matches no address at all, and it leaves the cost n² anyway, because what repeats
#: is the number of places a match is attempted from.
PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(?i)\b(?:api[_-]?key|secret|token|password)\b\s*[:=]\s*\S+", "<redacted>"),
    (r"\b[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,255}\.[A-Za-z]{2,24}\b", "<email>"),
    (r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "<ip>"),
)


#: Lines one call returns unless the caller asks for fewer, and the most it may ask
#: for. A ceiling because the only remedy this subagent has for a file longer than
#: the answer is to raise the number -- it holds no `read_file`, deliberately -- and
#: advice to raise a bound with no bound of its own is how a 32 MB file reaches the
#: model whole.
DEFAULT_LINES = 200
MAX_LINES = 2_000

#: How much the answer may carry whatever `max_lines` says. Lines are not characters:
#: two thousand lines of a minified payload is not two thousand lines of a log, and
#: what a tool result costs is counted in the model's context either way. The same
#: number `sql_query` answers with, for the same reason.
MAX_ANSWER_CHARS = 200_000


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


def mask_secrets(
    file_path: str, runtime: ToolRuntime[ToolContext], max_lines: int = DEFAULT_LINES
) -> str:
    """Read a text file with credentials, emails and IP addresses masked. Use
    when you must quote from a file that may carry secrets.

    `file_path` is the same virtual path the file tools take -- `/inputs/<name>` --
    rooted at this session, read through the session's backend.

    `max_lines` is how many lines to read, 200 by default and 2000 at most; the
    answer carries at most 200000 characters whatever you ask for.

    Reports how many lines it read *of how many the file has*, and how many it
    masked, so the caller can tell "nothing sensitive here" from "the interesting
    part was removed" -- two very different answers that look identical once the text
    is clean. A line it did not read is a line it says nothing about: raise
    `max_lines` before reporting that a long file was clean.
    """
    if not 1 <= max_lines <= MAX_LINES:
        msg = f"max_lines={max_lines}: ask for 1 to {MAX_LINES} lines"
        raise ValueError(msg)
    # Every line, not the slice: the count the header reports is the file's, and the
    # slice is what this answer covers. Read as `[:max_lines]` alone, a thousand-line
    # file answered `200 line(s), 0 masked` -- which the skill next door reads as
    # "this file held nothing sensitive", over the fifth of it that was looked at.
    every = _fetched(runtime, file_path).decode("utf-8").splitlines()
    masked = 0
    carried = 0
    out: list[str] = []
    for line in every[:max_lines]:
        cleaned = line
        for pattern, replacement in PATTERNS:
            cleaned = re.sub(pattern, replacement, cleaned)
        if carried + len(cleaned) + 1 > MAX_ANSWER_CHARS:
            break
        carried += len(cleaned) + 1
        masked += cleaned != line
        out.append(cleaned)
    name = PurePosixPath(file_path).name
    header = f"{name}: {len(out)} of {len(every)} line(s), {masked} masked"
    if left := len(every) - len(out):
        header += (
            f"; {left} line(s) not read, so this says nothing about them"
            f" -- raise max_lines, up to {MAX_LINES}"
        )
    return "\n".join([header, *out])


TOOLS = [mask_secrets]
