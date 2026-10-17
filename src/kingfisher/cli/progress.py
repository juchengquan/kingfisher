"""Showing a run as it happens, on one stream or two."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from typing import TextIO

    from kingfisher import RunEvent, RunResult


class Progress:
    """Print run events as they arrive, and keep the terminal readable.

    A subagent's prose is progress, not answer: on two streams it goes to `progress`, so a
    caller redirecting stdout gets the answer alone; on one, a speaker tag tells them apart.
    """

    def __init__(self, answer: TextIO, progress: TextIO | None = None) -> None:
        #: Where the asked-for agent's prose goes, and nothing else.
        self._answer = answer
        #: Everything a reader watches rather than keeps.
        self._progress = progress if progress is not None else answer
        self._split = self._progress is not self._answer
        self._owed = False
        self._speaker: str | None = None
        #: Whether the asked-for agent's own prose reached `answer`. A subagent's does not
        #: count: it is not the answer, even on one stream.
        self._answered = False

    def write(self, event: RunEvent) -> RunResult | None:
        """Show one event. Returns the `RunResult` if this was the last."""
        if event.kind == "token":
            self._token(event)
            return None
        if self._owed:
            self._progress.write("\n")
            self._owed = False
        # The answer is printed only if none of it streamed: a model that does not stream
        # sends no tokens, and one that did would otherwise read as answering twice.
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
            # Tagged on a change of speaker, or two subagents' fragments run together.
            self._say_who(event.agent)
            self._progress.write(event.text)
            self._progress.flush()
            self._owed = True
            return
        if not self._split:
            # One stream, so untagged a subagent's answer and the caller's read as one.
            # At the seam, because chunks split mid-word and there is no line to tag.
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
