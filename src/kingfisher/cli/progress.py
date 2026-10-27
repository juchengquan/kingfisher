"""Showing a run as it happens, on one stream or two."""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from typing import TextIO

    from kingfisher import RunEvent, RunResult

#: The kinds that arrive while the graph streams. Any other means it has stopped, and a
#: delegation still open will say nothing more.
STREAMED = frozenset({"token", "model_call", "tool_result", "delegation_start"})

#: Who spoke last, after a block: whoever speaks next is named, the caller's agent too.
_UNSAID = object()


class _Voice:
    """Where prose and lines go, in order: the terminal, or a delegation's block."""

    def __init__(self, out: TextIO, speaker: object = None) -> None:
        self._out = out
        self._speaker = speaker
        self._owed = False

    def prose(self, agent: str | None, text: str) -> None:
        """One fragment, tagged at the seam where the speaker changes.

        At the seam, because chunks split mid-word and there is no line to tag; untagged,
        a subagent's answer and the caller's read as one.
        """
        if agent != self._speaker:
            self.settle()
            self._speaker = agent
            print(f"[{agent or 'main'}]", file=self._out, flush=True)
        self._out.write(text)
        self._out.flush()
        self._owed = True

    def line(self, text: str) -> None:
        self.settle()
        print(text, file=self._out, flush=True)

    def block(self, text: str) -> None:
        """A delegation's block, whole."""
        self.settle()
        self._out.write(text)
        self._out.flush()
        self._speaker = _UNSAID

    def settle(self) -> None:
        """Close off prose still open, so the next line starts clean."""
        if self._owed:
            self._out.write("\n")
            self._out.flush()
            self._owed = False


class _Block(_Voice):
    """What one delegation says while a sibling beside it is open, under its header."""

    def __init__(self, start: RunEvent) -> None:
        self._kept = io.StringIO()
        # The header names the speaker, so its first prose needs no tag of its own.
        super().__init__(self._kept, speaker=start.agent)
        self.line(str(start))

    def text(self) -> str:
        self.settle()
        return self._kept.getvalue()


@dataclass(frozen=True)
class _Open:
    """A delegation started and not yet answered."""

    parent: tuple[str, ...]
    start: RunEvent


class Progress:
    """Print run events as they arrive, and keep the terminal readable.

    On two streams every token is progress and `answer` gets the result's answer once, at
    the end: the agent's narration before a tool call streams as its prose too, and is not
    the answer a caller redirected stdout for. On one, a speaker tag tells voices apart.

    Delegations side by side would interleave word by word, so while two with one parent
    are open, each is held and shown whole, as its result arrives. A lone one stays live.
    """

    def __init__(self, answer: TextIO, progress: TextIO | None = None) -> None:
        #: Where the answer goes, and nothing else.
        self._answer = answer
        #: Everything a reader watches rather than keeps.
        self._progress = progress if progress is not None else answer
        self._split = self._progress is not self._answer
        self._shown = _Voice(self._progress)
        #: Whether the asked-for agent's own prose reached a one-stream reader. A
        #: subagent's does not count: it is not the answer, even on one stream.
        self._answered = False
        self._open: dict[str, _Open] = {}
        #: The block of each open delegation a sibling opened beside; the rest are live.
        self._held: dict[str, _Block] = {}

    def write(self, event: RunEvent) -> RunResult | None:
        """Show one event. Returns the `RunResult` if this was the last."""
        if event.kind == "delegation_start":
            self._started(event)
            return None
        if event.kind not in STREAMED:
            self._unfinished()
        voice = self._voice_for(event.delegations)
        if event.kind == "token":
            voice.prose(event.agent, event.text)
            if event.agent is None:
                self._answered = True
            return None
        # On one stream, printed only if none of it streamed: a model that does not stream
        # sends no tokens, and one that did would otherwise read as answering twice.
        if event.kind == "finished":
            self._shown.settle()
            owed = self._split or not self._answered
            if owed and event.result is not None and event.result.answer:
                self._answer.write(event.result.answer)
                self._answer.flush()
            return event.result
        made = event.call if event.kind == "tool_result" else None
        # The result ending a held delegation is its block's last line, not its caller's.
        ending = self._held.get(made) if made else None
        (ending if ending is not None else voice).line(str(event))
        if made and made in self._open:
            self._ended(made)
        return None

    def _started(self, event: RunEvent) -> None:
        """Note a delegation, and hold it and every sibling open beside it.

        Every sibling is announced before any of them speaks, so none has printed yet.
        """
        if not event.delegations:
            return
        parent = event.delegations[:-1]
        self._open[event.delegations[-1]] = _Open(parent, event)
        beside = [made for made, one in self._open.items() if one.parent == parent]
        if len(beside) > 1:
            for made in beside:
                if made not in self._held:
                    self._held[made] = _Block(self._open[made].start)

    def _ended(self, made: str) -> None:
        """A delegation closed: its block, if it was held, goes where its caller's output
        goes."""
        one = self._open.pop(made)
        if (held := self._held.pop(made, None)) is not None:
            self._voice_for(one.parent).block(held.text())

    def _unfinished(self) -> None:
        """Show what every open delegation holds, marked: none of them will answer now.

        Innermost first, so a helper's block lands inside its subagent's.
        """
        for made in sorted(self._open, key=lambda made: -len(self._open[made].parent)):
            if (held := self._held.get(made)) is not None:
                held.line("(unfinished)")
            self._ended(made)

    def _voice_for(self, delegations: tuple[str, ...]) -> _Voice:
        """The innermost held block an event is inside, or the terminal."""
        for made in reversed(delegations):
            if (held := self._held.get(made)) is not None:
                return held
        return self._shown

    def close(self) -> None:
        """Show anything still held, and settle any newline still owed, so the next
        writer starts clean."""
        self._unfinished()
        self._shown.settle()


def show(
    events: Iterable[RunEvent], out: TextIO, progress: TextIO | None = None
) -> RunResult | None:
    """Drain a synchronous stream through `Progress`."""
    shown = Progress(out, progress)
    result: RunResult | None = None
    try:
        for event in events:
            result = shown.write(event) or result
    finally:
        # Even on a raise: what a held delegation said is never dropped.
        shown.close()
    return result
