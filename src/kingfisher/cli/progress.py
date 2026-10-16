"""Showing a run as it happens, on one stream or two."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from typing import TextIO

    from kingfisher import RunEvent, RunResult


class Progress:
    """Print run events as they arrive, and keep the terminal readable.

    **A subagent's prose is progress, not answer.** `token` events carry the subagent
    that produced them, or `None` for the agent the caller asked. Sent to one stream
    they read as one voice, which the speaker tag exists to prevent; sent to two, the
    distinction does the same job better -- an extractor's working notes are not part
    of what you asked for, and a caller redirecting stdout wants the answer rather
    than the reasoning that reached it.
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
        #: Whether any of the asked-for agent's own prose reached `answer`. A subagent's
        #: does not count: it is not the answer, even on one stream.
        self._answered = False

    def write(self, event: RunEvent) -> RunResult | None:
        """Show one event. Returns the `RunResult` if this was the last."""
        if event.kind == "token":
            self._token(event)
            return None
        if self._owed:
            self._progress.write("\n")
            self._owed = False
        # The terminal event carries the result and is not itself printed. Its answer
        # is, when none of it streamed: a model that does not stream sends no tokens,
        # and the answer exists only on the result. When it did stream, saying it
        # again would read as the model answering twice.
        if event.kind == "finished":
            if not self._answered and event.result is not None and event.result.answer:
                self._answer.write(event.result.answer)
                self._answer.flush()
            return event.result
        print(event, file=self._progress, flush=True)
        return None

    def _token(self, event: RunEvent) -> None:
        """One fragment of prose, to whichever stream it belongs on."""
        if self._split and event.agent is not None:
            # A subagent, and the streams are apart: this is progress. Tagged
            # on a change of speaker, as it always was, because the fragments
            # of two subagents would otherwise run together.
            self._say_who(event.agent)
            self._progress.write(event.text)
            self._progress.flush()
            self._owed = True
            return
        if not self._split:
            # Prose from a subagent arrives on the same stream as the caller's
            # own, as the same type, with nothing between them -- so without a
            # marker the two answers read as one. It cannot go on the fragment
            # itself: chunks split mid-word, and there is no line to tag. So it
            # goes at the seam, which is the only place a boundary exists.
            self._say_who(event.agent)
            self._owed = True
        self._answer.write(event.text)
        self._answer.flush()
        if event.agent is None:
            self._answered = True

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
    """Drain a synchronous stream through `Progress`."""
    shown = Progress(out, progress)
    result: RunResult | None = None
    for event in events:
        result = shown.write(event) or result
    shown.close()
    return result
