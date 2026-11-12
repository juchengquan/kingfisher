"""Every turn a session ran, as first written: its messages, each tagged with the turn, and
each turn with the one it followed. The only record of what was said: each turn is built
on the conversation replayed from it.

The conversation at the end of any turn is its ancestors' and its own lines replayed in
order, where a turn goes on from all its parent left, a message is appended, a message
carrying a `cut` is a summary standing for everything before that position, and a line
carrying `at` gives the message there the text the turn left it holding.

Kept as two files. `RECENT_TURNS_FILE` holds every turn's opening line, in the order the
turns ran, and the other lines of the turns not yet settled; `TURNS_FILE` holds the other
lines of the settled ones. A turn settles once none of its lines can change, at a split: at
a turn that wrote a summary, past `SPLIT_BYTES`, or at one that had to read both files. The
turn a split ends on carries `base`, the conversation it ended on, so the rebuild of the
turns after it reads the recent file alone.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from kingfisher.core.agent.middlewares.result_rows import expired_handle, stamped_id
from kingfisher.core.layout import RECENT_TURNS_FILE, TURNS_FILE
from kingfisher.core.logged import warn

from . import graph_state, runtime
from .messages import Message, Part, as_record, from_record, joined
from .steps import Steps
from .store import RESUMED, HarnessFiles, KeptLedger, read_off_path

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

#: Beside `answer`: why the turn stopped, as `RunResult.stop_reason` names it, and `null`
#: for a turn that raised. The answer alone reads the same for a turn that said nothing,
#: one its provider could not serve and one that failed.
STOP_REASON = "stop_reason"

#: On a turn's opening line, when it started, and on its answer line, when it ended:
#: seconds since 1970 on the wall clock, as every run event's `ts` is, so a turn lines up
#: with the events a client reads.
STARTED = "started"
ENDED = "ended"

#: On an opening line in the recent file: the turn's other lines are in the settled file.
SETTLED = "settled"

#: On an opening line in the recent file: the conversation the turn ended on, as records,
#: which the rebuild starts from rather than from the turn's own lines.
BASE = "base"

#: How large the recent file may grow before a turn splits it without a summary to split at.
SPLIT_BYTES = 5 * 1024 * 1024

#: On an assistant line, what its provider handed back as its reasoning. For the record
#: only: the rebuild never reads it, so a later turn is handed what it always was.
REASONING = "reasoning"


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
    calls = graph_state.calls_paused(paused, saver)
    return Resuming(
        held=len(graph_state.held_in(paused)),
        carried=_identity(graph_state.summary_in(paused)),
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
    stop_reason: str | None,
    started: float,
    ended: float,
) -> list[dict[str, Any]]:
    """This turn's lines: its opening, a resume's answers, the messages it added, the
    summary it ended on and its answer. `history` is how many messages its conversation held
    as it began, `resumed` set for a resume.
    """
    held = graph_state.held_in(snapshot)
    if resumed is None:
        opening: dict[str, Any] = {"turn": turn, PARENT: parent, STARTED: started}
        began, carried = history, None
    else:
        opening = {"turn": turn, PARENT: parent, RESUMED: True, STARTED: started}
        began, carried = resumed.held, resumed.carried
    # On the line and not on `Message`: earlier turns are in the state as the rebuild handed
    # them, without it, and `_rewritten` compares every field of a message but its text.
    added = [
        {**as_record(one), **({REASONING: why} if why else {})}
        for one, why in zip(
            runtime.as_messages(held[began:]), runtime.reasoning_in(held[began:]), strict=True
        )
    ]
    if resumed is None:
        _with_parts(added, parts, turn)
    lines = [
        opening,
        *({"turn": turn, **answer} for answer in (resumed.answered if resumed else ())),
        *({"turn": turn, **one} for one in added),
    ]
    summary = graph_state.summary_in(snapshot)
    if summary is not None and _identity(summary) != carried:
        written, cut = summary
        (said,) = runtime.as_messages([written])
        # A position in what the log replays, which counts records rather than messages,
        # and after a summary it carried opens on that summary rather than on the cut.
        at = (
            1 + len(runtime.as_messages(held[carried[1] : cut]))
            if carried is not None
            else len(runtime.as_messages(held[:cut]))
        )
        lines.append({"turn": turn, **as_record(said), CUT: at})
    lines.append({"turn": turn, ANSWER: answer, STOP_REASON: stop_reason, ENDED: ended})
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
        base = line.get(BASE)
        if isinstance(base, list):
            records = [_swapped(one, rows, in_view) or one for one in base]
            swapped = {**line, BASE: records} if records != base else None
        else:
            swapped = _swapped(line, rows, in_view)
        if swapped is None:
            kept.append((raw, line))
        else:
            kept.append((json.dumps(swapped, ensure_ascii=False), swapped))
    return kept


def _swapped(
    line: dict[str, Any], rows: Mapping[str, LedgerEntry], in_view: set[tuple[str, Any]]
) -> dict[str, Any] | None:
    """`line` with its result swapped for the expired handle, or `None` to keep it."""
    row = _row_of(line, rows)
    if row is None or (row.call, line["content"]) in in_view:
        return None
    # Patched as parsed, so a key another version wrote survives.
    return {**line, "content": expired_handle(row)}


def _row_of(line: Mapping[str, Any], rows: Mapping[str, LedgerEntry]) -> LedgerEntry | None:
    """The row of the result `line` holds whole, or `None` where it holds none."""
    content = line.get("content")
    # A line carrying `at` names no call and is never swapped: it holds the handle
    # clearing made, which the conversation kept after its result expired too.
    row = rows.get(stamped_id(content) or "") if isinstance(content, str) else None
    if row is None or line.get("call_id") != row.call or content == expired_handle(row):
        return None
    return row


def _read(held: bytes) -> tuple[list[tuple[str, dict[str, Any]]], bool]:
    """Each line as written beside what it holds, as far as the first this module did not
    write; and whether one ended the reading there.

    Stopped, not skipped: past a damaged line, a tool result can answer a call that was on
    it, which a provider refuses on every later turn. `fetch` hands back a save whole where
    one copy is, so a damaged line comes only from a file neither copy's seal matches: one
    written before seals were, by something other than kingfisher, or by a power cut on a
    backend that flushes nothing.
    """
    lines = []
    for raw in held.decode("utf-8", errors="replace").splitlines():
        if not raw.strip():
            continue
        try:
            line = json.loads(raw)
            if isinstance(line, dict) and "role" in line:
                from_record(line)
            if isinstance(line, dict) and BASE in line:
                for one in line[BASE]:
                    from_record(one)
        except (ValueError, KeyError, TypeError):
            return lines, True
        if not isinstance(line, dict):
            return lines, True
        lines.append((raw, line))
    return lines, False


@dataclass(frozen=True)
class TurnLog:
    """The log as a turn read it before it ran, which its end writes back with its own."""

    #: The recent file's lines as written beside what each holds; the settled file's, where
    #: no recent file has been written yet.
    lines: tuple[tuple[str, dict[str, Any]], ...] = ()
    #: The settled file's lines, where this reading needed them, else `None`.
    settled: tuple[tuple[str, dict[str, Any]], ...] | None = None
    #: Whether `lines` came from the settled file, a log from before it was split.
    unsplit: bool = False
    #: Every line read, each settled turn's after its opening: what the turns are read from.
    view: tuple[dict[str, Any], ...] = ()
    #: Whether a damaged line ended the reading before the file did.
    damaged: bool = False
    #: The turn the session is at: the last a rewind did not leave behind, or `None` where
    #: none ran or the session was rewound to the start.
    current: str | None = None
    #: The turns from the first to `current`, each the parent of the next.
    path: tuple[str, ...] = ()
    #: The conversation `current` ended on, which the turn is built on.
    conversation: tuple[Message, ...] = ()
    #: The turns a rewind left behind, read once with the log for everything a turn reads
    #: of it: the ledger leaves them out, and ranking copies follows the branches they made.
    off_path: frozenset[str] = frozenset()

    @property
    def written(self) -> bool:
        """Whether a turn has written here. Any line, not a conversation: a session rewound
        to the start has none."""
        return bool(self.lines) or self.damaged


def read_log(harness: HarnessFiles, *, whole: bool = False) -> Steps[TurnLog]:
    """The log, and the conversation the session is at; empty where no turn wrote one. The
    settled file is read only where the conversation needs it, or `whole` asks for every
    turn's lines."""
    recent = yield from harness.fetch(RECENT_TURNS_FILE)
    off_path = yield from read_off_path(harness)
    if recent is None:
        return parse_log(None, off_path, (yield from harness.fetch(TURNS_FILE)))
    log = parse_log(recent, off_path)
    if whole or not _rebuilt_from_recent(by_turn(log.view), log.path):
        log = parse_log(recent, off_path, (yield from harness.fetch(TURNS_FILE)) or b"")
    return log


def parse_log(
    recent: bytes | None, off_path: frozenset[str], settled: bytes | None = None
) -> TurnLog:
    """The log as the two files hold it, read with `off_path` as the turns a rewind left
    behind: `recent` `None` for a log from before it was split, whose lines `settled` holds;
    `settled` `None` where it was not read."""
    kept: list[tuple[str, dict[str, Any]]] | None = None
    if recent is None:
        if settled is None:
            return TurnLog(off_path=off_path)
        lines, damaged = _read(settled)
    else:
        lines, damaged = _read(recent)
        if settled is not None:
            kept, cut = _read(settled)
            damaged = damaged or cut
    parsed = [line for _, line in lines]
    view = _with_settled(parsed, [line for _, line in kept] if kept is not None else None)
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
    turns = by_turn(view)
    path = path_to(turns, current)[0] if current else []
    return TurnLog(
        lines=tuple(lines),
        settled=tuple(kept) if kept is not None else None,
        unsplit=recent is None,
        view=tuple(view),
        damaged=damaged,
        current=current,
        path=tuple(path),
        conversation=replayed(turns, path),
        off_path=off_path,
    )


def _with_settled(
    recent: Sequence[dict[str, Any]], settled: Sequence[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    """`recent` with each settled turn as the settled file holds it, where it was read."""
    if settled is None:
        return list(recent)
    kept = by_turn(settled)
    view: list[dict[str, Any]] = []
    for line in recent:
        if line.get(SETTLED) and line.get("turn") in kept:
            opening, body = kept[line["turn"]]
            view += [opening, *body]
        else:
            view.append(line)
    return view


def _rebuilt_from_recent(turns: Logged, path: Sequence[str]) -> bool:
    """Whether the conversation at the end of `path` is rebuilt without a settled turn's
    lines: every turn after the last base on it is whole in the recent file."""
    for turn in reversed(path):
        opening = turns[turn][0]
        if BASE in opening:
            return True
        if opening.get(SETTLED):
            return False
    return True


def keep_turns(  # noqa: PLR0913 -- the turn, what it began from, and what it ended on
    harness: HarnessFiles,
    log: TurnLog,
    *,
    turn: str,
    parts: Sequence[Part],
    resumed: Resuming | None,
    snapshot: Any,
    seen: Sequence[Message],
    ledger: KeptLedger | None,
    answer: str,
    stop_reason: str | None,
    started: float,
    ended: float,
    results_expire: bool,
) -> Steps[None]:
    """Add this turn to `log`, as the turn read it. `seen` is the conversation the turn
    ended on; `ledger` the ledger as this turn writes it back, or `None` where it keeps none;
    `results_expire` whether `KINGFISHER_KEEP_RESULTS_ROUNDS` ages its results.
    """
    lines = list(log.lines)
    rows = {
        entry.id: entry
        for group in ((*ledger.all_exchanges, *ledger.all_unclosed) if ledger else ())
        for entry in group
    }
    expired = {key: row for key, row in rows.items() if row.expired_in is not None}
    if expired:
        lines = _without_expired(lines, expired, seen)
    added = _turn_lines(
        turn,
        log.current,
        parts=parts,
        history=len(log.conversation),
        resumed=resumed,
        snapshot=snapshot,
        answer=answer,
        stop_reason=stop_reason,
        started=started,
        ended=ended,
    )
    settled = [line for _, line in log.settled] if log.settled is not None else None
    view = _with_settled([line for _, line in lines], settled)
    # Before the answer, which stays the turn's last line; the rebuild skips the answer, so
    # the `at` lines still apply after the turn's own messages.
    added[-1:-1] = _rewritten(turn, [*view, *added], seen)
    lines += [(json.dumps(one, ensure_ascii=False), one) for one in added]
    if not (
        log.unsplit
        or log.settled is not None
        or any(CUT in one for one in added)
        or sum(len(raw.encode("utf-8")) + 1 for raw, _ in lines) > SPLIT_BYTES
    ):
        yield from harness.store(RECENT_TURNS_FILE, _joined(lines))
        return
    turns = by_turn([*view, *added])
    base = replayed(turns, path_to(turns, turn)[0])
    # The turn writing stays whole: its own results are not in `ledger` to say they can change.
    changing = {turn} | {
        one.get("turn") for _, one in lines if results_expire and _row_of(one, rows) is not None
    }
    moving = {
        one["turn"]
        for _, one in lines
        if PARENT in one and isinstance(one.get("turn"), str) and not one.get(SETTLED)
        and one["turn"] not in changing
    }
    if moving:
        earlier = log.lines if log.unsplit else log.settled
        if earlier is None:
            earlier = _read((yield from harness.fetch(TURNS_FILE)) or b"")[0]
        # Settled first, holding each line the recent file is about to drop: a split dying
        # between the two leaves a turn in both, which the recent file's copy answers for.
        kept = [one for one in earlier if one[1].get("turn") not in moving]
        kept += [_without_base(*one) for one in lines if one[1].get("turn") in moving]
        yield from harness.store(TURNS_FILE, _joined(kept))
    yield from harness.store(RECENT_TURNS_FILE, _joined(_split(lines, moving, turn, base)))


def _split(
    lines: Sequence[tuple[str, dict[str, Any]]],
    moving: set[str],
    turn: str,
    base: Sequence[Message],
) -> list[tuple[str, dict[str, Any]]]:
    """The recent file once `moving` settle: their openings alone, and `base` on `turn`'s,
    the only one to carry it."""
    split: list[tuple[str, dict[str, Any]]] = []
    for raw, line in lines:
        name = line.get("turn")
        if PARENT not in line:
            if name not in moving:
                split.append((raw, line))
            continue
        opening = {key: value for key, value in line.items() if key != BASE}
        if name in moving:
            opening[SETTLED] = True
        if name == turn:
            opening[BASE] = [as_record(one) for one in base]
        if opening == line:
            split.append((raw, line))
        else:
            split.append((json.dumps(opening, ensure_ascii=False), opening))
    return split


def _without_base(raw: str, line: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """`line` as the settled file keeps it: a turn's lines there need no base to rebuild."""
    if BASE not in line:
        return raw, line
    kept = {key: value for key, value in line.items() if key != BASE}
    return json.dumps(kept, ensure_ascii=False), kept


def _joined(lines: Sequence[tuple[str, dict[str, Any]]]) -> bytes:
    return ("\n".join(raw for raw, _ in lines) + "\n").encode("utf-8")


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
    """Every line of the log as far as it is whole, both files'; none where no turn wrote
    one."""
    return list((yield from read_log(harness, whole=True)).view)


def turn_parents(harness: HarnessFiles) -> Steps[dict[str, str | None]]:
    """Each turn the log names, with the turn it followed: the recent file alone holds
    every opening line."""
    held = yield from harness.fetch(RECENT_TURNS_FILE)
    if held is None:
        held = yield from harness.fetch(TURNS_FILE)
    return {
        line["turn"]: line.get(PARENT)
        for _, line in _read(held or b"")[0]
        if PARENT in line and isinstance(line.get("turn"), str)
    }


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


def times_of(
    opening: Mapping[str, Any], body: Sequence[Mapping[str, Any]]
) -> tuple[float | None, float | None]:
    """When a turn started and ended, as its lines say: `None` for either one a turn logged
    before the log kept times does not hold."""
    return _seconds(opening.get(STARTED)), _seconds(_answer_line(body).get(ENDED))


def outcome_of(body: Sequence[Mapping[str, Any]]) -> tuple[str | None, str | None]:
    """What a turn handed its caller, as its answer line says: the answer, and why it
    stopped. `None` for either one the line does not hold."""
    end = _answer_line(body)
    answer, stopped = end.get(ANSWER), end.get(STOP_REASON)
    return (
        answer if isinstance(answer, str) else None,
        stopped if isinstance(stopped, str) else None,
    )


def _answer_line(body: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    return next((line for line in body if ANSWER in line), {})


def _seconds(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) else None


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
    before its `cut`, a message given the text an `at` names, and a `base` standing for all
    its turn ended on."""
    said: list[Message] = []
    for turn in path:
        opening, body = turns[turn]
        if isinstance(base := opening.get(BASE), list):
            said = [from_record(one) for one in base]
            continue
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
