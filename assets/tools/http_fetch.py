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
import http.client
import ipaddress
import re
import socket
import ssl
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import certifi
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

#: The share of a body that may be characters no reader can use before this tool
#: refuses it rather than spend the context on it. Measured: a 20 KB PNG answered
#: 20,000 characters of which 12,283 were unreadable, 61.4%, and a PDF the same;
#: gzipped text, the most text-like binary there is, 50.0%. Real pages measured 0.0%,
#: and the closest text came to this number was 10.3% -- French prose written in
#: Latin-1 and declared UTF-8, where the accents are lost and the sentences still read.
#: So the question is not text or binary but readable or not, and this sits between the
#: two things measured either side of it.
MAX_UNREADABLE_SHARE = 0.25

#: What a content type has to look like before a refusal prints it. A header is the
#: server's own text and a refusal is one line: `get_content_type()` hands back
#: whatever it parsed, so a type of five thousand characters would put five thousand
#: characters of someone else's writing in an answer about their page.
CONTENT_TYPE = re.compile(r"[\w.+-]{1,40}/[\w.+-]{1,40}")


def _unreadable(text: str) -> int:
    """How much of `text` no reader can use.

    The character `decode` writes where the bytes were not its encoding's, and the
    control codes that are not whitespace. Both, because either alone misses half of
    this: a PNG is 49.9% replacement characters, and UTF-16 bytes read as UTF-8 are
    almost all control codes -- one NUL per ASCII character -- with no replacement at
    all past the first two bytes.
    """
    return sum(
        1
        for one in text
        if one == "\ufffd" or (unicodedata.category(one) == "Cc" and one not in "\t\n\r")
    )


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


#: Private networks this tool may still reach: none, as shipped. A deployment that fetches
#: from a service of its own names that network here in its copy, as
#: `ipaddress.ip_network("10.1.0.0/16")`. Behind a proxy the address checked is the
#: proxy's, so a proxy on a private address is named here too.
ALLOWED_PRIVATE_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = ()


class _PrivateAddressError(OSError):
    """An address on this machine or a private network, refused before anything is sent.

    An `OSError`, because that is what urllib carries out of a connection as the reason
    in a `URLError`.
    """


def _public(address: str) -> bool:
    found = ipaddress.ip_address(address)
    return found.is_global or any(found in network for network in ALLOWED_PRIVATE_NETWORKS)


def _refused(host: str, address: str) -> _PrivateAddressError:
    where = address if host == address else f"{host} ({address})"
    return _PrivateAddressError(
        f"{where} is on this machine or a private network, and this tool fetches public "
        "addresses only"
    )


class _PublicConnection(http.client.HTTPConnection):
    """A connection to a public address, or to none.

    Every address the name has is checked before connecting, so a private one is never
    touched, and the address reached again before the request is sent: a name can answer
    differently the second time it is asked.
    """

    def connect(self) -> None:
        try:
            found = socket.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM)
        except OSError:
            # Left to the connection below, which says why in its own words.
            found = []
        for *_, where in found:
            if not _public(address := str(where[0])):
                raise _refused(self.host, address)
        super().connect()
        reached = self.sock.getpeername()[0]
        if not _public(reached):
            self.close()
            raise _refused(self.host, reached)


class _PublicTLSConnection(_PublicConnection, http.client.HTTPSConnection):
    """The same for HTTPS, checked again once the handshake is done."""


class _PublicHandler(urllib.request.HTTPHandler):
    def http_open(self, req: Any) -> Any:
        return self.do_open(_PublicConnection, req)


def _tls_context() -> ssl.SSLContext:
    """This Python's own certificates, and certifi's where it has none.

    python.org's macOS build ships with no store until `Install Certificates.command` is
    run, and every HTTPS fetch failed there while model calls, whose httpx carries
    certifi, worked. certifi's only then: added beside a system store, it would trust
    what a deployment had taken out of that store.
    """
    context = ssl.create_default_context()
    if context.cert_store_stats()["x509_ca"]:
        return context
    # A folder of hashed certificates is read lazily, so none count as loaded yet.
    folder = ssl.get_default_verify_paths().capath
    if folder and Path(folder).is_dir() and any(Path(folder).iterdir()):
        return context
    context.load_verify_locations(cafile=certifi.where())
    return context


class _PublicTLSHandler(urllib.request.HTTPSHandler):
    def __init__(self) -> None:
        self._tls = _tls_context()
        super().__init__(context=self._tls)

    def https_open(self, req: Any) -> Any:
        return self.do_open(_PublicTLSConnection, req, context=self._tls)


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


def _read_by(deadline: float, response: Any) -> tuple[bytes, bool]:
    """The body up to the byte cap, giving up at `deadline`, and whether more was left.

    Four bytes a character is UTF-8's longest, so this is the most bytes `MAX_CHARS`
    can need; the answer is cut to characters afterwards.

    One byte past the cap, which is what tells a page that ends there from one that
    goes on: measured, 200,000 bytes and 20,000 bytes came back as the same 20,000
    characters, and nothing in either said which of them was the whole page.

    `read1`, not `read`. `read` blocks until it has every byte asked for, so reading
    in pieces with `read` leaves the deadline checked between pieces and never during
    one -- measured, that was still no bound at all against a server dribbling a byte
    at a time. `read1` returns what has arrived, so the check below runs as often as
    the server answers.
    """
    cap = MAX_CHARS * 4
    body = bytearray()
    while len(body) <= cap:
        if time.monotonic() > deadline:
            msg = f"stopped after {MAX_SECONDS}s, which is as long as one fetch may take"
            raise TimeoutError(msg)
        piece = response.read1(min(CHUNK_BYTES, cap + 1 - len(body)))
        if not piece:
            break
        body += piece
    return bytes(body[:cap]), len(body) > cap


def _cut(text: str) -> str:
    """The start of a page, with a last line saying that is what it is.

    Named after the bound that bit, and both can bite: a megabyte of ASCII passes the
    byte cap and the character cap, and `MAX_CHARS` is the number the docstring
    promises, so it is the one a caller is told about. The bytes bound it alone only
    where the decode came back under the character cap -- `iso-2022-jp` spends nine
    bytes on two characters, and 180,000 bytes of it answered 17,779 -- and naming the
    characters there would name a number the answer never reached.

    The note is inside the cap rather than appended past it, because the docstring
    promises at most `MAX_CHARS` characters and the model plans its call on that.
    """
    if len(text) > MAX_CHARS:
        note = f"\n-- cut at {MAX_CHARS} characters, which is as much as one answer carries"
    else:
        note = f"\n-- cut at {MAX_CHARS * 4} bytes, which is as much as one fetch reads"
        # The byte cap can stop mid-character, and `errors="replace"` writes one the
        # page never sent: dropped, because a caller reads it as the page's own
        # corruption, and the note says where the answer stops.
        text = text.removesuffix("\ufffd")
    return text[: MAX_CHARS - len(note)] + note


@tool
def http_fetch(url: str) -> str:
    """Fetch a URL over HTTP(S) and return its body as text.

    Use for reading a public web page or API response; an address on this
    machine or a private network is refused. Returns at most 20,000
    characters, and gives up after 30 seconds. A page longer than that comes
    back cut, with a last line saying so. A body that does not read as
    text, such as an image or a PDF, is refused rather than answered. Does not
    run JavaScript, so a page that renders client-side will come back close to
    empty.
    """
    if not _is_http(url):
        return f"refused: {url!r} is not an http(s) URL"

    # One opener, so the redirect rule and the address rule apply to every request this
    # call makes. The check above is about the URL the model wrote; these are about every
    # URL that one leads to, and the two are not the same question.
    opener = urllib.request.build_opener(_HttpRedirectsOnly, _PublicHandler, _PublicTLSHandler)
    try:
        with opener.open(url, timeout=TIMEOUT_S) as response:
            body, more = _read_by(time.monotonic() + MAX_SECONDS, response)
            text = body.decode(_charset(response), errors="replace")
            kind = response.headers.get_content_type()
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        if isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, _PrivateAddressError):
            return f"refused: {exc.reason}"
        # Returned rather than raised, so the message names the URL -- a
        # `URLError` alone does not say which fetch failed. Raising would not
        # have ended the turn: the failure guard turns an offered tool's
        # exception into a failed result. What returning costs is that status,
        # so a record such as `show-your-work`'s counts this call as succeeded.
        return f"could not fetch {url}: {type(exc).__name__}: {exc}"

    # Judged on what arrived, not on what the server called it. Text served as
    # `application/octet-stream` measured 0.0% unreadable and is worth answering, and a
    # page declaring `text/plain; charset=utf-8` whose bytes are really UTF-16 measured
    # 55.2% and is not -- so the type is named in the refusal rather than trusted to
    # make it. Measured before the cut, over everything that arrived rather than the
    # part that would be answered: the same share, read off four times the sample.
    if (unreadable := _unreadable(text)) > len(text) * MAX_UNREADABLE_SHARE:
        named = f"{kind}: " if CONTENT_TYPE.fullmatch(kind) else ""
        return (
            f"refused: {named}{unreadable} of the {len(text)} characters at {url} do "
            f"not read as text -- ask for a URL that answers text, which is what this "
            f"tool answers with"
        )

    return text if not more and len(text) <= MAX_CHARS else _cut(text)


#: Declared, never inferred. kingfisher imports this name and nothing else, so
#: a helper in this file stays a helper.
TOOLS = [http_fetch]
