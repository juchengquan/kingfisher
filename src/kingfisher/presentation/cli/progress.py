"""Showing a run as it happens, on one stream or two.

Lived in `tests/integration/driver.py`, which is not in the wheel, so shipping
`kingfisher run` would have meant a second copy of it -- and the two would have
drifted the first time one learned something about a new event kind. One
implementation, two callers: the driver passes one stream twice and behaves
exactly as it did, and the command passes two.

Two streams is what lets the command compose:

    kingfisher run "profile this" --agent analyst --data sales.csv > answer.md

The answer has to land in the file while the tool calls still scroll past, and
`2>/dev/null` has to give silence. A `--quiet` flag would ask the caller for
correct behaviour and do nothing for whoever forgets it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from typing import TextIO

    from kingfisher import RunEvent, RunResult


class Progress:
    """Print run events as they arrive, and keep the terminal readable.

    Token events are fragments, not lines: written with no newline and no tag,
    so the model owns the left margin and its own formatting survives. Progress
    stays tagged and aligned. That mix is the whole reason for `_owed` -- a
    newline is owed before the next tagged line, or it lands on the end of a
    half-finished sentence.

    A class rather than a loop because there are two loops: `stream` is
    synchronous and `astream` is not, and the formatting is the same either
    way. Writing it twice is how the two would come to disagree about when a
    newline is owed.

    **A delegate's prose is progress, not answer.** `token` events carry the
    delegate that produced them, or `None` for the agent the caller asked. Sent
    to one stream they read as one voice, which the speaker tag exists to
    prevent; sent to two, the distinction does the same job better -- an
    extractor's working notes are not part of what you asked for, and a caller
    redirecting stdout wants the answer rather than the reasoning that reached
    it.

    When both streams are the same object that distinction collapses back to
    the tag, which is what the driver has always shown.
    """

    def __init__(self, answer: TextIO, progress: TextIO | None = None) -> None:
        #: Where the asked-for agent's prose goes, and nothing else.
        self._answer = answer
        #: Everything a reader watches rather than keeps. The same stream by
        #: default, which is the driver's behaviour and was the only one.
        self._progress = progress if progress is not None else answer
        self._split = self._progress is not self._answer
        self._owed = False
        self._speaker: str | None = None

    def write(self, event: RunEvent) -> RunResult | None:
        """Show one event. Returns the `RunResult` if this was the last."""
        if event.kind == "token":
            self._token(event)
            return None
        if self._owed:
            self._progress.write("\n")
            self._owed = False
        # The terminal event carries the result and is not itself printed. Nor
        # is `result.answer` printed afterwards: it already arrived, a word at
        # a time, and saying it again would read as the model answering twice.
        if event.kind == "finished":
            return event.result
        print(event, file=self._progress, flush=True)
        return None

    def _token(self, event: RunEvent) -> None:
        """One fragment of prose, to whichever stream it belongs on."""
        if self._split and event.agent is not None:
            # A delegate, and the streams are apart: this is progress. Tagged
            # on a change of speaker, as it always was, because the fragments
            # of two delegates would otherwise run together.
            self._say_who(event.agent)
            self._progress.write(event.text)
            self._progress.flush()
            self._owed = True
            return
        if not self._split:
            # Prose from a delegate arrives on the same stream as the caller's
            # own, as the same type, with nothing between them -- so without a
            # marker the two answers read as one. It cannot go on the fragment
            # itself: chunks split mid-word, and there is no line to tag. So it
            # goes at the seam, which is the only place a boundary exists.
            self._say_who(event.agent)
            self._owed = True
        self._answer.write(event.text)
        self._answer.flush()

    def _say_who(self, agent: str | None) -> None:
        """Name the speaker, once, at the seam where it changes."""
        if agent == self._speaker:
            return
        if self._owed:
            self._progress.write("\n")
            self._owed = False
        self._speaker = agent
        print(f"[{agent or 'main'}]", file=self._progress, flush=True)

    def close(self) -> None:
        """Settle any newline still owed, so the next writer starts clean."""
        if self._owed:
            self._progress.write("\n")
            self._progress.flush()
            self._owed = False


def show(
    events: Iterable[RunEvent], out: TextIO, progress: TextIO | None = None
) -> RunResult | None:
    """Drain a synchronous stream through `Progress`.

    `show` rather than `render`, which is what the driver called it: this module
    ships beside `listing.render`, and one CLI with two `render`s that take
    different types and answer different questions is a name doing no work.
    """
    shown = Progress(out, progress)
    result: RunResult | None = None
    for event in events:
        result = shown.write(event) or result
    shown.close()
    return result
