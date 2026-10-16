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

import codecs
import time
import urllib.error
import urllib.request
from typing import Any

from langchain_core.tools import tool

#: Bounded because a tool result goes into the model's context, and a context
#: window is the one resource an agent cannot ask for more of.
MAX_CHARS = 20_000

#: How long to wait for the next byte. This is what `timeout=` means -- one socket
#: operation -- and reading it as a bound on the call is the mistake below.
TIMEOUT_S = 20

#: How long the whole call may take. Measured without it: a server sending one byte
#: every two seconds was still being read after ninety seconds, because every byte
#: reset the socket clock. A tool runs on the turn's thread and
#: `KINGFISHER_TURN_TIMEOUT_S` is read between stream chunks, so nothing outside this
#: call was going to end it -- and here the party deciding how slowly to answer is
#: whoever the URL points at.
MAX_SECONDS = 30

#: Read in pieces rather than in one call, which is what gives the deadline somewhere
#: to be checked.
CHUNK_BYTES = 64 * 1024


def _is_http(url: str) -> bool:
    """Whether a URL is one this tool fetches at all."""
    return url.startswith(("http://", "https://"))


class _HttpRedirectsOnly(urllib.request.HTTPRedirectHandler):
    """Refuse a redirect that leaves http(s).

    urllib's own handler permits `ftp:` beside them, so the check in `http_fetch`
    below guarded the URL the model wrote and not the URL actually fetched: measured,
    a 302 to `ftp://ftp.gnu.org/welcome.msg` came back as that file's contents, from a
    tool whose docstring promises HTTP(S) and whose first line refuses anything else.
    `file:` was already refused by upstream, and so is a redirect loop.
    """

    def redirect_request(  # noqa: PLR0913, PLR0917 -- upstream's signature, not ours
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> Any:
        if not _is_http(newurl):
            raise urllib.error.HTTPError(
                newurl, code, f"redirect to {newurl!r} is not an http(s) URL", headers, fp
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _charset(response: Any) -> str:
    """The encoding the response declares, or UTF-8 where it declares none usable.

    A server says what its bytes are and this tool was not listening: measured, a page
    declaring `ISO-8859-15` came back as `caf? costs 5??`, and one declaring `utf-16`
    as its bytes with a NUL between each. A server may also name an encoding this
    machine has never heard of, and `decode` raises `LookupError` on one -- which
    would leave the server deciding whether this tool fails.
    """
    declared = response.headers.get_content_charset()
    try:
        codecs.lookup(declared or "utf-8")
    except LookupError:
        return "utf-8"
    return declared or "utf-8"


def _read_by(deadline: float, response: Any) -> bytes:
    """The body up to the byte cap, giving up at `deadline`.

    Four bytes a character is UTF-8's longest, so this is the most bytes `MAX_CHARS`
    can need; the answer is cut to characters afterwards.

    `read1`, not `read`. `read` blocks until it has every byte asked for, so reading
    in pieces with `read` leaves the deadline checked between pieces and never during
    one -- measured, that was still no bound at all against a server dribbling a byte
    at a time. `read1` returns what has arrived, so the check below runs as often as
    the server answers.
    """
    cap = MAX_CHARS * 4
    body = bytearray()
    while len(body) < cap:
        if time.monotonic() > deadline:
            msg = f"stopped after {MAX_SECONDS}s, which is as long as one fetch may take"
            raise TimeoutError(msg)
        piece = response.read1(min(CHUNK_BYTES, cap - len(body)))
        if not piece:
            break
        body += piece
    return bytes(body)


@tool
def http_fetch(url: str) -> str:
    """Fetch a URL over HTTP(S) and return its body as text.

    Use for reading a public web page or API response. Returns at most
    20,000 characters, and gives up after 30 seconds. Does not run JavaScript, so
    a page that renders client-side will come back close to empty.
    """
    if not _is_http(url):
        return f"refused: {url!r} is not an http(s) URL"

    # One opener, so the redirect rule applies to the request this call makes. The
    # check above is about the URL the model wrote; this is about every URL that one
    # leads to, and the two are not the same question.
    opener = urllib.request.build_opener(_HttpRedirectsOnly)
    try:
        with opener.open(url, timeout=TIMEOUT_S) as response:
            body = _read_by(time.monotonic() + MAX_SECONDS, response).decode(
                _charset(response), errors="replace"
            )
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        # Returned rather than raised, so the message names the URL -- a
        # `URLError` alone does not say which fetch failed. Raising would not
        # have ended the turn: the failure guard turns a workspace tool's
        # exception into a failed result. What returning costs is that status,
        # so a record such as `show-your-work`'s counts this call as succeeded.
        return f"could not fetch {url}: {type(exc).__name__}: {exc}"

    return body[:MAX_CHARS]


#: Declared, never inferred. kingfisher imports this name and nothing else, so
#: a helper in this file stays a helper.
TOOLS = [http_fetch]
