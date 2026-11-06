"""Every turn a session ran, as first written: its messages, each tagged with the turn, and
each turn with the one it followed. The only record of what was said: each turn is built
on the conversation replayed from it.

The conversation at the end of any turn is its ancestors' and its own lines replayed in
order, where a turn goes on from all its parent left, a message is appended, a message
carrying a `cut` is a summary standing for everything before that position, and a line
carrying `at` gives the message there the text the turn left it holding.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from kingfisher.core.agent.middlewares.recorded import expired_handle, stamped_id
from kingfisher.core.layout import TURNS_FILE
from kingfisher.core.logged import warn

from . import runtime
from .steps import Steps
from .store import RESUMED, HarnessFiles, KeptLedger, read_off_path
from .transcript import Message, Part, as_record, from_record, joined

if TYPE_CHECKING:
    from kingfisher.core.request import Decision
    from kingfisher.core.result import LedgerEntry

#: On a turn's opening line, the turn it followed: `null` for the first, and for the first
#: after a rewind to the start.
PARENT = "parent"

#: On a summary, the position in the conversation so far that it stands for everything
#: before, counted with the turn's own messages appended.
CUT = "cut"

#: On a line with no role, a position in the conversation its turn ended on, beside the
#: text the message there was left holding: a result an earlier turn wrote, which clearing
#: swapped for its handle in state. Beside the line first written rather than over it, so a
#: rewind to before the swap gets the result back whole.
AT = "at"

#: On a resume's line for one answer: the decision as the pause named it, a position in the
#: pause and not a call; `call_id` beside it is the call it answered.
DECISION = "decision"

#: Beside `decision`: what the person answered, as `Decision.action` names it. Not
#: `answer`, which on a line of its own is what the caller was handed.
ACTION = "action"

#: On an asked turn's message, in place of its `content`: the pieces it was sent as, each
#: with `by` naming who wrote it, which joined in order are what the model read.
PARTS = "parts"

#: On a turn's last line, which has no `role`: the answer the caller was handed, written
#: even when empty, so a reader never takes it from the last assistant line.
ANSWER = "answer"


@dataclass(frozen=True)
class Resuming:
    """What the pause a resume answers left, read before the resume runs on it."""

    #: How many messages the paused state held: the resume's own begin after them.
    held: int
    #: The summary the paused state already carried, its text and cut, which the paused
    #: turn logged; a resume ending on it summarised nothing.
    carried: tuple[str, int] | None
    #: A line for each answer, naming the call it answered.
    answered: tuple[dict[str, Any], ...]


def resuming(graph: Any, config: Any, saver: Any, decisions: Sequence[Decision]) -> Resuming | None:
    """What the pause in `saver` holds, or `None` for a graph that keeps no state, which
    logs no messages either."""
    read = getattr(graph, "get_state", None)
    if read is None:
        return None
    paused = read(config)
    calls = runtime.calls_paused(paused, saver)
    return Resuming(
        held=len(runtime.held_in(paused)),
        carried=_identity(runtime.summary_in(paused)),
        answered=tuple(
            {DECISION: one.call_id, "call_id": calls.get(one.call_id), ACTION: one.action}
            for one in decisions
        ),
    )


def _identity(summary: tuple[Any, int] | None) -> tuple[str, int] | None:
    """What tells one summary from another: a summary message carries no id."""
    return None if summary is None else (str(getattr(summary[0], "content", "")), summary[1])


def _turn_lines(  # noqa: PLR0913 -- what the turn was asked, and what its state holds
    turn: str,
    parent: str | None,
    *,
    parts: Sequence[Part],
    history: int,
    resumed: Resuming | None,
    snapshot: Any,
    answer: str,
) -> list[dict[str, Any]]:
    """This turn's lines: its opening, a resume's answers, the messages it added, the
    summary it ended on and its answer. `history` is how many messages its transcript held
    as it began, `resumed` set for a resume.
    """
    held = runtime.held_in(snapshot)
    if resumed is None:
        opening: dict[str, Any] = {"turn": turn, PARENT: parent}
        began, carried = history, None
    else:
        opening = {"turn": turn, PARENT: parent, RESUMED: True}
        began, carried = resumed.held, resumed.carried
    added = [as_record(one) for one in runtime.as_transcript(held[began:])]
    if resumed is None:
        _with_parts(added, parts, turn)
    lines = [
        opening,
        *({"turn": turn, **answer} for answer in (resumed.answered if resumed else ())),
        *({"turn": turn, **one} for one in added),
    ]
    summary = runtime.summary_in(snapshot)
    if summary is not None and _identity(summary) != carried:
        written, cut = summary
        (said,) = runtime.as_transcript([written])
        # A position in what the log replays, which counts records rather than messages,
        # and after a summary it carried opens on that summary rather than on the cut.
        at = (
            1 + len(runtime.as_transcript(held[carried[1] : cut]))
            if carried is not None
            else len(runtime.as_transcript(held[:cut]))
        )
        lines.append({"turn": turn, **as_record(said), CUT: at})
    lines.append({"turn": turn, ANSWER: answer})
    return lines


def _with_parts(added: list[dict[str, Any]], parts: Sequence[Part], turn: str) -> None:
    """Keep the turn's message, the first user line in `added`, as the pieces it was sent
    as. Not always first: deepagents answers a superseded pause's calls ahead of it."""
    at = next((n for n, one in enumerate(added) if one["role"] == "user"), None)
    if at is None:
        return
    said = added[at].get("content", "")
    if said != joined(parts):
        # The state is what the model read: pieces it no longer matches would rebuild a
        # conversation nobody had.
        warn(
            "message_rewritten",
            f"turn {turn}'s message was changed after kingfisher wrote it, so its log "
            "line names no author",
        )
        parts = (Part(said),)
    added[at] = {
        "role": "user",
        PARTS: [
            {"by": part.by, "text": part.text} if part.by is not None else {"text": part.text}
            for part in parts
        ],
    }


def _without_expired(
    lines: Sequence[tuple[str, dict[str, Any]]],
    rows: Mapping[str, LedgerEntry],
    seen: Sequence[Message],
) -> list[tuple[str, dict[str, Any]]]:
    """`lines` with each result `rows` names, by id, swapped for its expired handle once
    `seen`, the conversation the next turn is rebuilt into, no longer holds it whole, so
    `KINGFISHER_KEEP_RESULTS_ROUNDS` bounds what this file keeps as well."""
    # Not sooner: the model is handed an expired result whole until clearing or a summary
    # takes it out of view, and swapped while in view, the rebuild hands it a handle in
    # place of text it had, the rewrite the result-expiry entry in `decisions.md` rejects.
    in_view = {(one.call_id, one.content) for one in seen if one.call_id}
    kept: list[tuple[str, dict[str, Any]]] = []
    for raw, line in lines:
        content = line.get("content")
        # A line carrying `at` names no call and is never swapped: it holds the handle
        # clearing made, which the conversation kept after its result expired too.
        row = rows.get(stamped_id(content) or "") if isinstance(content, str) else None
        if (
            row is None
            or line.get("call_id") != row.call
            or content == expired_handle(row)
            or (row.call, content) in in_view
        ):
            kept.append((raw, line))
            continue
        # Patched as parsed, so a key another version wrote survives.
        swapped = {**line, "content": expired_handle(row)}
        kept.append((json.dumps(swapped, ensure_ascii=False), swapped))
    return kept


def _read(held: bytes) -> tuple[list[tuple[str, dict[str, Any]]], bool]:
    """Each line as written beside what it holds, as far as the first this module did not
    write; and whether one ended the reading there.

    Stopped, not skipped: past a damaged line, a tool result can answer a call that was on
    it, which a provider refuses on every later turn. `fetch` hands back a save whole where
    one copy is, so a damaged line comes only from a file neither copy's seal matches: one
    written before seals were, or by something other than kingfisher.
    """
    lines = []
    for raw in held.decode("utf-8", errors="replace").splitlines():
        if not raw.strip():
            continue
        try:
            line = json.loads(raw)
            if isinstance(line, dict) and "role" in line:
                from_record(line)
        except (ValueError, KeyError, TypeError):
            return lines, True
        if not isinstance(line, dict):
            return lines, True
        lines.append((raw, line))
    return lines, False


@dataclass(frozen=True)
class TurnLog:
    """The log as a turn read it before it ran, which its end writes back with its own."""

    lines: tuple[tuple[str, dict[str, Any]], ...] = ()
    #: Whether a damaged line ended the reading before the file did.
    damaged: bool = False
    #: The turn the session is at: the last a rewind did not leave behind, or `None` where
    #: none ran or the session was rewound to the start.
    current: str | None = None
    #: The conversation `current` ended on, which the turn is built on.
    conversation: tuple[Message, ...] = ()


def read_log(harness: HarnessFiles) -> Steps[TurnLog]:
    """The log, and the conversation the session is at; empty where no turn wrote one."""
    held = yield from harness.fetch(TURNS_FILE)
    if held is None:
        return TurnLog()
    return parse_log(held, (yield from read_off_path(harness)))


def parse_log(held: bytes, off_path: frozenset[str]) -> TurnLog:
    """The log `held`, read with `off_path` as the turns a rewind left behind."""
    lines, damaged = _read(held)
    parsed = [line for _, line in lines]
    # The turns ran in the order the log holds them: after a rewind, the turn rewound to.
    current = next(
        (
            line["turn"]
            for line in reversed(parsed)
            if PARENT in line and isinstance(line.get("turn"), str)
            and line["turn"] not in off_path
        ),
        None,
    )
    turns = by_turn(parsed)
    return TurnLog(
        lines=tuple(lines),
        damaged=damaged,
        current=current,
        conversation=replayed(turns, path_to(turns, current)[0]) if current else (),
    )


def keep_turns(  # noqa: PLR0913 -- the turn, what it began from, and what it ended on
    harness: HarnessFiles,
    log: TurnLog,
    *,
    turn: str,
    parts: Sequence[Part],
    history: Sequence[Message],
    resumed: Resuming | None,
    snapshot: Any,
    ledger: KeptLedger | None,
    answer: str,
) -> Steps[None]:
    """Add this turn to `log`, as the turn read it. `ledger` is the ledger as this turn
    writes it back, or `None` where it keeps none.
    """
    seen = runtime.as_transcript(runtime.as_seen(snapshot))
    lines = list(log.lines)
    if ledger is not None:
        expired = {
            entry.id: entry
            for group in (*ledger.all_exchanges, *ledger.all_unclosed)
            for entry in group
            if entry.expired_in is not None
        }
        lines = _without_expired(lines, expired, seen)
    added = _turn_lines(
        turn,
        log.current,
        parts=parts,
        history=len(history),
        resumed=resumed,
        snapshot=snapshot,
        answer=answer,
    )
    # Before the answer, which stays the turn's last line; the rebuild skips the answer, so
    # the `at` lines still apply after the turn's own messages.
    added[-1:-1] = _rewritten(turn, [*(line for _, line in lines), *added], seen)
    written = [raw for raw, _ in lines] + [json.dumps(one, ensure_ascii=False) for one in added]
    yield from harness.store(TURNS_FILE, ("\n".join(written) + "\n").encode("utf-8"))


def _rewritten(
    turn: str, lines: Sequence[dict[str, Any]], seen: Sequence[Message]
) -> list[dict[str, Any]]:
    """A line for each message `turn` left holding other text than its lines replay to, so
    the log rebuilds `seen`, the conversation the turn ended on."""
    turns = by_turn(lines)
    rebuilt = replayed(turns, path_to(turns, turn)[0])
    if len(rebuilt) != len(seen) or any(
        replace(was, content="") != replace(now, content="")
        for was, now in zip(rebuilt, seen, strict=True)
    ):
        # Only a message's text has a line to say it changed. Said, since the next turn is
        # built from the log and would otherwise be handed something else without a word.
        warn(
            "conversation_not_logged",
            f"turn {turn} ended on {len(seen)} messages the log cannot rebuild as they "
            f"were; the next turn is built from the {len(rebuilt)} it rebuilds",
            ended=len(seen),
            rebuilt=len(rebuilt),
        )
        return []
    return [
        {"turn": turn, AT: at, "content": now.content}
        for at, (was, now) in enumerate(zip(rebuilt, seen, strict=True))
        if was.content != now.content
    ]


def read_turn_log(harness: HarnessFiles) -> Steps[list[dict[str, Any]]]:
    """Every line of the log as far as it is whole; none where no turn wrote one."""
    held = yield from harness.fetch(TURNS_FILE)
    return [] if held is None else [line for _, line in _read(held)[0]]


def logged_turns(log: TurnLog) -> frozenset[str]:
    """Every turn id the log names, on every branch."""
    return frozenset(line["turn"] for _, line in log.lines if isinstance(line.get("turn"), str))


#: Each turn's opening line and the lines after it, by its id.
Logged = Mapping[str, tuple[dict[str, Any], list[dict[str, Any]]]]


def by_turn(lines: Sequence[dict[str, Any]]) -> Logged:
    """Each turn's opening line and the lines after it, by its id."""
    turns: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    for line in lines:
        if PARENT in line and isinstance(line.get("turn"), str):
            turns[line["turn"]] = (line, [])
        elif line.get("turn") in turns:
            turns[line["turn"]][1].append(line)
    return turns


def task_of(body: Sequence[dict[str, Any]]) -> str | None:
    """What the person asked in a turn: its message's `user` pieces joined, or `None` where
    it has none -- a resume, a conversation an older version imported, a message rewritten
    after it was sent."""
    message = next((line for line in body if PARTS in line), None)
    asked = [
        part["text"] for part in (message or {}).get(PARTS, ()) if part.get("by") == "user"
    ]
    return "".join(asked) if asked else None


def path_to(turns: Logged, target: str) -> tuple[list[str], bool]:
    """The turns from the first to `target`, each the parent of the next, and whether the
    walk back came round to a turn already on it; the path then starts where it did."""
    path = [target]
    seen = {target}
    while isinstance(parent := turns[path[-1]][0].get(PARENT), str) and parent in turns:
        # Two turns under one id, which a session could log before such ids were refused:
        # the second's line takes the first's place, and the walk back comes round to it.
        if parent in seen:
            return list(reversed(path)), True
        path.append(parent)
        seen.add(parent)
    return list(reversed(path)), False


def replayed(turns: Logged, path: Sequence[str]) -> tuple[Message, ...]:
    """The conversation the last turn of `path` ended on, rebuilt from the log alone: each
    turn's messages appended to its parent's conversation, a summary standing for everything
    before its `cut`, and a message given the text an `at` names."""
    said: list[Message] = []
    for turn in path:
        _, body = turns[turn]
        for line in body:
            if "role" not in line:
                at, content = line.get(AT), line.get("content")
                if isinstance(at, int) and 0 <= at < len(said) and isinstance(content, str):
                    said[at] = replace(said[at], content=content)
                continue
            message = from_record(line)
            cut = line.get(CUT)
            said = [message, *said[cut:]] if isinstance(cut, int) else [*said, message]
    return tuple(said)
