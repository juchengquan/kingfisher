"""The shipped tool that fetches a URL, driven against a server of this file's own.

Its coverage was "does it import" and "is the name in the set" -- the shape that let
`sql_query` ship a promise of CSV it did not keep. What that left here: a `timeout=`
read as a bound on the call when it bounds one socket operation, a scheme check on the
URL the model wrote and not on the URL fetched, and a body decoded as UTF-8 whatever
the server said it was.

The server is `http.server` on the loopback at a port the kernel picks, so no rule
here reaches the network -- which is also why the refusals are asserted against hosts
that do not exist: a redirect this tool refuses is refused before anything is fetched.
"""

from __future__ import annotations

import http.server
import socketserver
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest

from kingfisher.core.definitions.tools.catalogue import LocalToolRepository, tool_name

#: A body in an encoding that is not UTF-8, and what the response declares it as.
#: `utf-16` is the one that reads as corruption rather than as mojibake -- a NUL
#: between every character -- and `iso-8859-15` is the one a European page really is.
ENCODED = {
    # A no-break space, written as its codepoint: it is one of the bytes that
    # differs between this encoding and UTF-8, which is what the rule is about.
    "iso-8859-15": "café costs 5\u00a0€",
    "utf-16": "café costs five",
}


#: One run each of ASCII and Japanese, which `iso-2022-jp` writes with a shift into
#: its run and back out again: nine bytes for two characters, so the byte cap bites
#: before the character cap and the answer comes back under the cap it names.
SHIFTY = "a\u65e5" * 20_000

#: Eight bytes of PNG signature and then every byte value there is, which is what a
#: body that was never text looks like to a decoder: 61.4% of its characters came back
#: unreadable, and a PDF measured the same.
BINARY = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 80

#: Prose with accents in it, served below as Latin-1 while declaring UTF-8. Every
#: accent decodes as the replacement character and the sentences still read, which is
#: the closest text measured to the share where this tool stops answering: 10.3%.
FRENCH = (
    "Le caf\u00e9 co\u00fbte cinq euros, et la p\u00e2tisserie \u00e0 c\u00f4t\u00e9 "
    "vend des g\u00e2teaux tr\u00e8s appr\u00e9ci\u00e9s par les \u00e9tudiants. " * 20
)

#: Every route that is only "these bytes, declared that": a table, because the handler
#: below is held to twelve branches and these are not branches, they are rows.
BODIES: dict[str, tuple[bytes, str]] = {
    "/plain": (b"a plain page", "text/plain"),
    "/bogus-charset": (b"whatever", "text/plain; charset=moonspeak"),
    "/shifty": (SHIFTY.encode("iso-2022-jp"), "text/plain; charset=iso-2022-jp"),
    "/binary": (BINARY, "image/png"),
    "/text-called-binary": (b"a plain page", "application/octet-stream"),
    "/wrongly-declared": (FRENCH.encode("utf-16"), "text/plain; charset=utf-8"),
    "/dented": (FRENCH.encode("iso-8859-1"), "text/plain; charset=utf-8"),
    # A type the server made up, long enough to be worth not printing back.
    "/forged-type": (BINARY, "evil/" + "x" * 300),
    **{
        f"/{name}": (body.encode(name), f"text/plain; charset={name}")
        for name, body in ENCODED.items()
    },
}


class _Handler(http.server.BaseHTTPRequestHandler):
    """One route per thing a server can do that this tool has to survive."""

    #: Set by the fixture: how long to wait between bytes on `/dribble`.
    pause: float = 0.2

    def log_message(self, format: str, *args: Any) -> None:
        """Quiet: the server's own logging goes to stderr and says nothing useful."""

    def _sent(self, body: bytes, content_type: str = "text/plain") -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, where: str) -> None:
        self.send_response(302)
        self.send_header("Location", where)
        self.end_headers()

    def do_GET(self) -> None:
        if self.path in BODIES:
            self._sent(*BODIES[self.path])
        elif self.path.startswith("/sized/"):
            # Sized by the request, so a rule can ask for a body of exactly the cap
            # without writing that number down beside the tool's own copy of it.
            self._sent(b"x" * int(self.path.removeprefix("/sized/")))
        elif self.path == "/shifty":
            self._sent(SHIFTY.encode("iso-2022-jp"), "text/plain; charset=iso-2022-jp")
        elif self.path == "/dribble":
            # A content length it never finishes sending, a byte at a time. Every byte
            # resets the socket clock, which is how a socket timeout bounds nothing.
            self.send_response(200)
            self.send_header("Content-Length", "1000000")
            self.end_headers()
            while True:
                try:
                    self.wfile.write(b"x")
                    self.wfile.flush()
                except OSError:
                    return
                time.sleep(self.pause)
        elif self.path == "/silent":
            time.sleep(30)
        elif self.path == "/to-ftp":
            self._redirect("ftp://nowhere.invalid/welcome.msg")
        elif self.path == "/to-plain":
            self._redirect("/plain")
        else:  # pragma: no cover -- every route a rule asks for is above
            self.send_error(404)


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


@pytest.fixture
def serving():
    """A server on the loopback, and the base URL to reach it at."""
    server = _Server(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def fetch(shipped):
    """The shipped tool's own function, called directly: it takes no runtime."""
    tools = {tool_name(t): t for t in LocalToolRepository(shipped / "tools").tools}
    return tools["http_fetch"]


def _globals(tool: Any) -> dict[str, Any]:
    return tool.func.__globals__


def _within(seconds: float, call: Callable[[], str]) -> str:
    """What `call` answered, failing if it has not answered by then.

    On a thread, because what the two rules below are about is a bound on a call -- and
    a call with no bound does not come back, so measuring the elapsed time afterwards
    cannot fail, only hang. Found by mutating: reverting the deadline left the whole
    file running until something outside it gave up, which tells a reader of CI less
    than one red line does.
    """
    answered: list[str] = []
    raised: list[BaseException] = []

    def go() -> None:
        try:
            answered.append(call())
        except BaseException as exc:  # noqa: BLE001 -- re-raised below, on this thread
            raised.append(exc)

    thread = threading.Thread(target=go, daemon=True)
    started = time.monotonic()
    thread.start()
    thread.join(seconds)

    # Told apart, because they look the same from here and do not mean the same thing:
    # a call that raised has ended, and reporting it as "did not answer" sends the
    # reader to the bound when the answer is in the traceback. That cost an hour once.
    if raised:
        raise raised[0]
    assert answered, f"it had not answered after {time.monotonic() - started:.1f}s"
    return answered[0]


def test_a_url_this_tool_does_not_fetch_is_refused_before_anything_is_asked(fetch):
    """The grant this tool exists to be narrower than `execute`: a URL, over HTTP, and
    nothing else. `file:///etc/passwd` through a tool is the whole host.
    """
    assert fetch.func(url="file:///etc/hosts").startswith("refused:")
    assert fetch.func(url="ftp://nowhere.invalid/x").startswith("refused:")


def test_a_page_comes_back_as_its_text(fetch, serving):
    """The control every rule below needs: a tool that refused everything would
    satisfy most of them.
    """
    assert fetch.func(url=f"{serving}/plain") == "a plain page"


def test_a_body_past_the_cap_is_cut_to_it_and_says_so(fetch, serving):
    """A tool result goes into the model's context, which is the one resource an agent
    cannot ask for more of -- and a cut nothing mentions is read as the whole page:
    measured, 200,000 bytes and 20,000 bytes came back as the same 20,000 characters,
    with nothing in either to say which of them was complete.
    """
    cap = _globals(fetch)["MAX_CHARS"]

    said = fetch.func(url=f"{serving}/sized/{cap * 10}")

    assert said.startswith("x" * 100)
    assert said.splitlines()[-1] == (
        f"-- cut at {cap} characters, which is as much as one answer carries"
    )
    assert len(said) <= cap, "the note is part of the answer the docstring bounds"


def test_a_page_that_ends_at_the_cap_says_nothing_about_a_cut(fetch, serving):
    """The other half, at the size where it is hardest: a page of exactly the cap is
    whole, and a note on it sends the caller looking for the rest of what it has.
    """
    cap = _globals(fetch)["MAX_CHARS"]

    assert fetch.func(url=f"{serving}/sized/{cap}") == "x" * cap


def test_a_page_the_bytes_cut_before_the_characters_did_says_that_instead(fetch, serving):
    """The byte cap is `MAX_CHARS` times UTF-8's longest character, and other encodings
    spend more: `iso-2022-jp` shifts in and out of each run, nine bytes for two
    characters, so 180,000 bytes of it answered 17,779 -- under the character cap, and
    cut all the same. A note naming the characters would name a number this answer
    never reached.
    """
    held = _globals(fetch)

    said = fetch.func(url=f"{serving}/shifty")

    assert len(said) < held["MAX_CHARS"]
    assert said.splitlines()[-1] == (
        f"-- cut at {held['MAX_CHARS'] * 4} bytes, which is as much as one fetch reads"
    )


def test_the_character_the_cut_invented_is_not_in_the_answer(fetch, serving):
    """The byte cap stops mid-character and `errors="replace"` writes a character the
    page never sent, so the answer ended `\u65e5a\u65e5\ufffd` -- which a caller reads
    as the page's own corruption, and this page holds nothing of the kind.
    """
    assert "\ufffd" not in fetch.func(url=f"{serving}/shifty")


def test_a_server_that_answers_a_byte_at_a_time_is_given_up_on(fetch, serving, monkeypatch):
    """`timeout=` is a socket timeout -- one operation -- and reading it as a bound on
    the call is what left this unbounded: measured, a server sending one byte every two
    seconds was still being read after ninety, because every byte reset the clock. A
    tool runs on the turn's thread and `KINGFISHER_TURN_TIMEOUT_S` is read between
    stream chunks, so nothing outside the call was going to end it -- and the party
    choosing how slowly to answer is whoever the URL points at.

    Driven through `_within`, so a deadline that is not applied fails here rather than
    reading on until something else gives up -- which is what a bound asserted after
    the call amounts to.
    """
    monkeypatch.setitem(_globals(fetch), "MAX_SECONDS", 1.0)

    said = _within(10, lambda: fetch.func(url=f"{serving}/dribble"))

    assert "TimeoutError" in said and "stopped after 1" in said


def test_a_server_that_says_nothing_at_all_is_still_bounded_by_the_socket(
    fetch, serving, monkeypatch
):
    """The other half of the pair, and why both numbers are kept: the deadline above
    is checked between reads, so a connection that never answers at all is the socket
    timeout's to end.
    """
    monkeypatch.setitem(_globals(fetch), "TIMEOUT_S", 1)

    said = _within(10, lambda: fetch.func(url=f"{serving}/silent"))

    assert said.startswith("could not fetch")


def test_a_redirect_that_leaves_http_is_refused(fetch, serving):
    """urllib's own redirect handler permits `ftp:` beside http and https, so the check
    on the way in guarded the URL the model wrote and not the URL fetched: measured
    against the real internet, a 302 to `ftp://ftp.gnu.org/welcome.msg` came back as
    that file's contents.

    Asserted as *this* refusal rather than as the absence of a body, which no rule here
    can show: the host named does not resolve, so a tool that tried the fetch and a
    tool that refused it both come back empty-handed. The message tells them apart --
    it names the scheme and the URL it would have followed.
    """
    said = fetch.func(url=f"{serving}/to-ftp")

    assert "not an http(s) URL" in said
    assert "ftp://nowhere.invalid/welcome.msg" in said, "refused, but not over the scheme"


def test_a_redirect_that_stays_in_http_is_followed(fetch, serving):
    """The bound on the rule above: a handler refusing every redirect would pass it,
    and a redirect is how half the web answers.
    """
    assert fetch.func(url=f"{serving}/to-plain") == "a plain page"


def test_a_body_that_does_not_read_as_text_is_refused_rather_than_answered(fetch, serving):
    """20,000 characters of a PNG went into the model's context, 12,283 of them the
    replacement character and most of the rest control codes: half of what one call may
    spend, on nothing anybody can read, and nothing in the answer saying the body was
    never text. A PDF measured the same, and gzipped text 50.0%.
    """
    said = fetch.func(url=f"{serving}/binary")

    assert said.startswith("refused: image/png: ")
    assert "do not read as text" in said
    assert "\ufffd" not in said
    assert len(said) < 300, len(said)


def test_a_text_body_the_server_called_binary_is_still_answered(fetch, serving):
    """The control that keeps the rule above from becoming a check on the header, which
    is what it was going to be: a server may serve text as `application/octet-stream`,
    and that body measured 0.0% unreadable. So the type is named in the refusal and
    does not decide it.
    """
    assert fetch.func(url=f"{serving}/text-called-binary") == "a plain page"


def test_a_body_whose_declared_encoding_loses_most_of_it_is_refused(fetch, serving):
    """Not every body nobody can read is binary: UTF-16 bytes declared `utf-8` decode
    to one NUL per character -- 55.2% unreadable, with no replacement character past
    the first two bytes -- so counting only those would have answered this whole.
    """
    said = fetch.func(url=f"{serving}/wrongly-declared")

    assert said.startswith("refused: text/plain: ")
    assert "do not read as text" in said


def test_a_body_the_wrong_encoding_only_dents_is_answered(fetch, serving):
    """The other side of the share, and the reason it is not the 10% it nearly was:
    French prose written in Latin-1 and declared UTF-8 loses every accent to the
    replacement character, 10.3% of it, and reads perfectly well without them.
    """
    said = fetch.func(url=f"{serving}/dented")

    assert said.startswith("Le caf\ufffd co\ufffdte cinq euros")
    assert "refused" not in said


def test_a_content_type_the_server_made_up_is_not_printed_back(fetch, serving):
    """A header is the server's own text and a refusal is one line: `get_content_type()`
    hands back whatever it parsed, so a type of three hundred characters would be three
    hundred characters of someone else's writing inside an answer about their page.
    """
    said = fetch.func(url=f"{serving}/forged-type")

    assert said.startswith("refused: ")
    assert "evil/" not in said
    assert "xxx" not in said
    assert "do not read as text" in said


@pytest.mark.parametrize("declared", sorted(ENCODED))
def test_a_body_is_decoded_as_the_response_says_it_is(declared, fetch, serving):
    """The server says what its bytes are and this tool was not listening: measured, a
    page declaring `ISO-8859-15` came back as `caf? costs 5??`, and one declaring
    `utf-16` as its bytes with a NUL between every character.
    """
    assert fetch.func(url=f"{serving}/{declared}") == ENCODED[declared]


def test_an_encoding_this_machine_does_not_have_is_not_the_servers_to_choose(fetch, serving):
    """A server may name anything, and `decode` raises `LookupError` on a name Python
    does not know -- which would leave the server deciding whether this tool fails.
    """
    assert fetch.func(url=f"{serving}/bogus-charset") == "whatever"


def test_the_docstring_names_both_bounds_the_tool_applies(fetch):
    """The model plans its call by these two numbers and reads them here and nowhere
    else. Whitespace-normalised: the sentence wraps where the column ran out, and an
    `in` against the source sees a line break the model never does.
    """
    held = _globals(fetch)
    reads = " ".join(fetch.description.split())

    assert f"at most {held['MAX_CHARS']:,} characters" in reads
    assert f"gives up after {held['MAX_SECONDS']:g} seconds" in reads
