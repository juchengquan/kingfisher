"""Return a file's text with anything that looks like a credential masked.

**This tool ships inside a bundle, and that is the example.** It sits in
`subagents/redactor/tools/` rather than in the catalogue's `tools/`, so it
reaches the `redactor` delegate and nothing else -- not the agent that summoned
it, not another delegate, not a request that names it.

The reason is the shape of the tool rather than a policy someone chose. It
returns file contents, which is a bulk read wearing a safety feature: handed to
the main agent it is a way to pull a whole file into the answer while *looking*
like the careful option, and the masking says nothing about whether that file
should have been read at all. It is only the right tool inside the procedure
next door in `skills/redaction/`, and the folder is what keeps the two together.

A catalogue tool could not express that. An agent that omits `tools:` holds
every tool there is, so anything in `tools/` is something the top-level agent
can call; a bundle is the only place a capability can sit that it cannot.

Written as a plain function for the reason `line_count` gives: the docstring is
the description the model reads, the annotations are the argument schema, and
this tool wants no control over either.
"""

from __future__ import annotations

import re
from pathlib import Path

#: What gets masked. Deliberately crude and deliberately named: this is an
#: example of *where a tool lives*, not a redaction library, and a pattern list
#: that looked authoritative would invite someone to trust it as one.
PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(?i)\b(?:api[_-]?key|secret|token|password)\b\s*[:=]\s*\S+", "<redacted>"),
    (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "<email>"),
    (r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "<ip>"),
)


def mask_secrets(path: str, max_lines: int = 200) -> str:
    """Read a text file with credentials, emails and IP addresses masked. Use
    when you must quote from a file that may carry secrets.

    Reports how many lines were returned and how many were masked, so the caller
    can tell "nothing sensitive here" from "the interesting part was removed" --
    two very different answers that look identical once the text is clean.
    """
    lines = Path(path).read_text(encoding="utf-8").splitlines()[:max_lines]
    masked = 0
    out = []
    for line in lines:
        cleaned = line
        for pattern, replacement in PATTERNS:
            cleaned = re.sub(pattern, replacement, cleaned)
        masked += cleaned != line
        out.append(cleaned)
    header = f"{Path(path).name}: {len(out)} line(s), {masked} masked"
    return "\n".join([header, *out])


TOOLS = [mask_secrets]
