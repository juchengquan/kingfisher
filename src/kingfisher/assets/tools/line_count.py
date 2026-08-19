"""How long a file is, without pulling it into the answer.

The same argument `csv_profile` makes, one size down: an agent deciding whether
to `read_file` a log wants its length, and reading it to find out is the cost it
was trying to avoid. `read_file` takes a line range, so a number here turns one
huge read into a targeted one.

**Written as a plain function, and that is the other half of why it ships.** A
tool may be a `BaseTool` -- `@tool` from `langchain_core.tools`, which is what
`http_fetch` and `csv_profile` use -- or an ordinary function, and kingfisher
takes either: `tool_name` reads `.name` if there is one and falls back to
`__name__`, so everything downstream keys on the same string. deepagents wraps
what it is given.

Nothing is lost by leaving the decorator off, as long as the function is written
the way any tool should be. The docstring becomes the description the model
reads; the annotations become the argument schema. What the decorator buys is
*control* over those -- a name that is not the function's, a description that is
not the docstring -- and this tool wants neither.

What it costs is a thing worth naming rather than discovering: a plain function
is not a `BaseTool`, so a test cannot `.invoke` it or read `.args`. It calls it,
which is simpler, and is why the tests for this one look like tests for any
other function.
"""

from __future__ import annotations

from pathlib import Path

#: Read in chunks rather than whole. A line count is the one question you can
#: answer without ever holding the file, and a tool that answered it by loading
#: 2 GB into memory would be worse than the `read_file` it exists to avoid.
CHUNK = 1 << 20


def line_count(path: str) -> str:
    """Count the lines in a text file. Use before reading a file you expect to
    be long, so you can ask `read_file` for the part you want.

    `path` is a host path, not one of the agent's virtual paths.

    Reports the count and whether the last line ends in a newline, because a
    file's final line is the one a range read is most likely to get wrong.
    """
    where = Path(path)
    lines = 0
    ended = True
    with where.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            lines += chunk.count(b"\n")
            ended = chunk.endswith(b"\n")
    # A file with content and no trailing newline still has a last line; one
    # that is empty has none, and saying "1" for it would be a lie a range read
    # then trips over.
    if not ended:
        lines += 1
    return f"{where.name}: {lines} line(s){'' if ended else ', no trailing newline'}"


TOOLS = [line_count]
