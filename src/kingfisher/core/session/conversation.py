"""`/records/conversation.md`: what summaries replaced along the session's current path,
rebuilt from the turn log, so a rewind takes back what it holds."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from kingfisher.core.agent.middlewares.result_rows import arguments, stamped_id
from kingfisher.core.logged import raised, warn

from .graph_state import SUMMARY_EVENT
from .ledger import file_left
from .steps import Steps, adrive, changing, drive, reading
from .turn import ledger_line
from .turnlog import AT, CUT, PARENT, PARTS, by_turn, lines_so_far, read_log

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from kingfisher.core.result import LedgerEntry

    from .ledger import Ledger
    from .messages import Part
    from .store import HarnessFiles
    from .turnlog import Logged, Resuming, TurnLog

#: A row by its result's id, or `None` where none is kept.
Rows = Callable[[str], "LedgerEntry | None"]

_OPENING = (
    "# The conversation summaries replaced\n\n"
    "Each turn's words as they were said, oldest first, along this conversation as it now "
    "stands. A tool's result is its ledger row: the id, the call, its size and the file "
    "under the tool results folder that holds it, where it has one."
)


def summarised_away(turns: Logged, path: Sequence[str]) -> list[tuple[str, dict[str, Any]]]:
    """Every message a summary on `path` stood for, as its line, beside the turn that said
    it, oldest first; an earlier summary, which those lines already cover, left out.

    Replayed as `replayed` replays, from each turn's own lines and never a `base`, which
    stands for a conversation and not the turns that said it: so `turns` is the whole log.
    """
    said: list[tuple[str, dict[str, Any]]] = []
    away: list[tuple[str, dict[str, Any]]] = []
    for turn in path:
        for line in turns[turn][1]:
            if "role" not in line:
                at, content = line.get(AT), line.get("content")
                if isinstance(at, int) and 0 <= at < len(said) and isinstance(content, str):
                    who, was = said[at]
                    said[at] = (who, {**{k: v for k, v in was.items() if k != PARTS},
                                      "content": content})
                continue
            cut = line.get(CUT)
            if isinstance(cut, int):
                away += [one for one in said[:cut] if not one[1].get("summary")]
                said = [(turn, line), *said[cut:]]
            else:
                said.append((turn, line))
    return away


def conversation_text(turns: Logged, path: Sequence[str], rows: Rows) -> str:
    """The file's text for `path`, or `""` where no summary on it replaced anything."""
    away = summarised_away(turns, path)
    if not away:
        return ""
    out = [_OPENING]
    heading = None
    for turn, line in away:
        if turn != heading:
            out.append(f"## Turn {turn}")
            heading = turn
        if (said := _said(line, rows)) is not None:
            out.append(said)
    return "\n\n".join(out) + "\n"


def _said(line: Mapping[str, Any], rows: Rows) -> str | None:
    """One message as the file shows it, or `None` for one it leaves out."""
    role = line.get("role")
    if role == "user":
        return f"**user:** {_words(line)}"
    if role == "assistant":
        parts = [f"**assistant:** {text}"] if (text := str(line.get("content") or "")) else []
        parts += [
            f"**called** {call.get('name', '')} {arguments(call.get('args') or {})}".rstrip()
            for call in line.get("tool_calls") or ()
        ]
        return "\n".join(parts) or None
    if role == "tool":
        # A row, never the text: the result's file is the one copy, and a rewind that hides
        # an abandoned turn's files would otherwise leave their words here.
        content = line.get("content")
        number = stamped_id(content) if isinstance(content, str) else None
        row = rows(number) if number is not None else None
        if row is None or row.call != line.get("call_id"):
            return f"**result:** {line.get('name') or 'a tool'}, not recorded"
        return f"**result:** {ledger_line(row)}"
    return None


def _words(line: Mapping[str, Any]) -> str:
    """What the person and the caller wrote of a turn's message, without what kingfisher
    added to it, which the ledger rows here say again."""
    parts = line.get(PARTS)
    if not isinstance(parts, list):
        return str(line.get("content") or "")
    return "".join(str(part.get("text", "")) for part in parts if part.get("by") != "kingfisher")


def moved_since_last_turn(log: TurnLog, *, took_over: bool) -> bool:
    """Whether the file may no longer be what the current path holds: a rewind since the
    last turn, or a turn that never ended, whose words the file may hold and the log does
    not. A summary rebuilds it as it is made, so any other turn starts on the file the
    last one left, and reads the settled half of the log no more than it did."""
    opened = [
        line["turn"] for _, line in log.lines
        if PARENT in line and isinstance(line.get("turn"), str)
    ]
    return took_over or (opened[-1] if opened else None) != log.current


def keep_at_start(  # noqa: PLR0913 -- the session's backends, and what decides the file
    files: Any, harness: HarnessFiles, log: TurnLog, rows: Rows, route: str, *, took_over: bool
) -> Steps[None]:
    """Rebuild the file before a turn runs, where `moved_since_last_turn` says it may
    have moved: the agent reads it only during a turn, so this covers a rewind too."""
    if not moved_since_last_turn(log, took_over=took_over):
        return
    whole = yield from read_log(harness, whole=True)
    text = conversation_text(by_turn(list(whole.view)), whole.path, rows)
    yield from _made_to_say(files, route, text)


def _made_to_say(files: Any, route: str, text: str) -> Steps[None]:
    """Make the file at `route` say `text`, removing it for `""`; untouched where it
    already does."""
    (held,) = yield reading(files, "download_files", [route])
    had = held.content if held.error is None else None
    if not text:
        if had is not None:
            yield changing(files, "delete", route)
        return
    wanted = text.encode("utf-8")
    if had == wanted:
        return
    (answer,) = yield changing(files, "upload_files", [(route, wanted)])
    if answer.error:
        msg = f"the session's backend would not keep {route}: {answer.error}"
        raise OSError(msg)


class ConversationRecord:
    """The file kept up to date within a turn, after a summary made mid-turn, so a later
    call of the same turn can read what was just summarised.

    One per turn, made before the graph is built; `parts` and `resumed` are set once
    setup knows them, which is after the build.
    """

    def __init__(  # noqa: PLR0913 -- what the turn's own log line is built from
        self,
        files: Any,
        harness: HarnessFiles,
        ledger: Ledger,
        *,
        turn: str,
        route: str,
        tool_results_route: str,
    ) -> None:
        self._files = files
        self._harness = harness
        self._ledger = ledger
        self._turn = turn
        self.route = route
        self._tool_results_route = tool_results_route
        #: The turn's message as its log line keeps it; none for a resume.
        self.parts: tuple[Part, ...] = ()
        #: What a resume's pause left, `None` for an asked turn.
        self.resumed: Resuming | None = None

    def summarised(self, held: Sequence[Any], summary: Any, cut: int) -> None:
        """After a summary standing for `held[:cut]`, `held` every message in state."""
        try:
            drive(self._steps(held, summary, cut))
        except Exception as exc:  # noqa: BLE001 -- the model call goes on without it
            self._failed(exc)

    async def asummarised(self, held: Sequence[Any], summary: Any, cut: int) -> None:
        """`summarised`, awaiting each call's async twin."""
        try:
            await adrive(self._steps(held, summary, cut))
        except Exception as exc:  # noqa: BLE001 -- the model call goes on without it
            self._failed(exc)

    def _steps(self, held: Sequence[Any], summary: Any, cut: int) -> Steps[None]:
        # Filed first, so the rows name the files: the turn's end would file them anyway.
        yield from file_left(
            self._files, self._ledger, list(held[:cut]),
            tool_results_route=self._tool_results_route,
        )
        log = yield from read_log(self._harness, whole=True)
        state = SimpleNamespace(
            values={"messages": list(held),
                    SUMMARY_EVENT: {"summary_message": summary, "cutoff_index": cut}}
        )
        mine = lines_so_far(
            self._turn, log.current, parts=self.parts, history=len(log.conversation),
            resumed=self.resumed, snapshot=state,
        )
        turns = by_turn([*log.view, *mine])
        text = conversation_text(turns, [*log.path, self._turn], self._ledger.row)
        yield from _made_to_say(self._files, self.route, text)

    def _failed(self, exc: Exception) -> None:
        where = "raised {} at {}".format(*raised(exc))
        warn(
            "conversation_not_kept",
            f"{self.route} was not rebuilt after a summary: it {where}",
        )
