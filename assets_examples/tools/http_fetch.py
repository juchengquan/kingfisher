"""A tool for something the built-in set cannot do at all.

The clearest reason to write one. `execute` could shell out to `curl`, but then
every request that needs a URL also needs the shell — and the shell is the one
capability that bypasses the filesystem permission layer entirely. A narrow
tool lets a request fetch a page *without* being handed arbitrary command
execution, which is a real reduction in what a task can reach.

The docstring is not decoration: it is what the model reads when deciding
whether to call this. Write it as a trigger, the way a skill's description is
written, and say what the arguments mean in the words a caller would use.
"""

from __future__ import annotations

import urllib.error
import urllib.request

from langchain_core.tools import tool

#: Bounded because a tool result goes into the model's context, and a context
#: window is the one resource an agent cannot ask for more of.
MAX_CHARS = 20_000
TIMEOUT_S = 20


@tool
def http_fetch(url: str) -> str:
    """Fetch a URL over HTTP(S) and return its body as text.

    Use for reading a public web page or API response. Returns at most
    20,000 characters. Does not run JavaScript, so a page that renders
    client-side will come back close to empty.
    """
    if not url.startswith(("http://", "https://")):
        return f"refused: {url!r} is not an http(s) URL"

    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_S) as response:  # noqa: S310
            body = response.read(MAX_CHARS * 4).decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        # Returned, not raised: a failed fetch is a result the model can react
        # to -- by trying another URL, or by saying it could not reach one --
        # whereas an exception ends the turn.
        return f"could not fetch {url}: {type(exc).__name__}: {exc}"

    return body[:MAX_CHARS]


#: Declared, never inferred. kingfisher imports this name and nothing else, so
#: a helper in this file stays a helper.
TOOLS = [http_fetch]
