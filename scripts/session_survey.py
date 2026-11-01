#!/usr/bin/env python3
"""Survey a kingfisher sessions folder: how long sessions run, how often they reach the
clearing mark and the summary, what their conversations are made of, how long a model
call takes as they grow, what per-session memory does, and what sessions hold on disk.

Reads only. Prints counts and sizes, never anything a user or agent said, and no
session ids or file names, so its output is safe to share. Standard library only, so it
runs wherever Python 3.10+ does, with or without kingfisher installed.

    python3 scripts/session_survey.py /path/to/workspace/sessions
    python3 scripts/session_survey.py /path/to/workspace/sessions --log run.log --log run.log.1
    python3 scripts/session_survey.py /path/to/workspace/sessions --every 4 --json

SESSIONS is the folder holding one folder per session (`<workspace>/sessions` with the
default sessions plugin). A deployment keeping sessions elsewhere copies them to disk
first, keeping modified times (`cp -p`, `rsync -a`): how long a session has been idle is
read from its folder's modified time, as `kingfisher reap` reads it, and a copy that
drops them shows every session as just used.

--log takes the deployment's run log: the `kingfisher.run` logger's JSON lines, one per
event, with or without a prefix in front of the JSON. With it the survey reports what
the model was really sent (tokens, and how much of it came from the cache) and how long
each call took by how much it was sent; without it,
sizes are estimated from what the transcript holds now, after clearing and summaries.

--every is the deployment's KINGFISHER_MEMORY_EVERY (4 unless it set one), used to work
out on which turns the memory writer ran.

--layout names a session folder the deployment renamed through `Config(layout=...)`, as
`inputs=in`; repeat it for each.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Any

#: Characters per token for an estimate from text, as langchain's approximate counter.
CHARS_PER_TOKEN = 4

TURN_BUCKETS = ((1, 1), (2, 3), (4, 9), (10, 19), (20, None))

#: Input sizes, in tokens, that model-call times are grouped by.
SIZE_BANDS = ((0, 16_000), (16_000, 32_000), (32_000, 64_000), (64_000, 128_000), (128_000, None))

#: A call writing no more than this is timed mostly by what it was sent, not what it wrote.
SHORT_REPLY = 200

#: What clearing leaves in place of a result or of a file's text, as
#: `core/agent/middlewares/clearing.py` writes them. Clearing logs no event, so these are
#: its only trace; a summary since may have cut them away.
CLEARED_RESULT = re.compile(
    r"\([\d,]+ bytes, cleared(?:;| before it was shown)"
    r"| -- (?:[\d,]+ chars, )?cleared(?:; call | before it was shown)"
)
CLEARED_TEXT = re.compile(
    r"[\d,]+ chars(?: written)?, cleared(?:; .* holds the current text)?", re.S
)

DAY = 86_400

#: A session's folders as kingfisher names them; --layout gives a deployment's renames.
FOLDERS = {
    "inputs": "inputs",
    "outputs": "outputs",
    "memory": "memory",
    "scratchpad": "scratchpad",
    "tool_results": "tool_results",
}
#: deepagents' names, which move with the scratchpad but cannot be renamed.
HISTORY = "conversation_history"
HARNESS = ".harness"
TRANSCRIPT = "transcript.jsonl"
#: `.harness` files with a part of their own: the pinned agent is a whole copy per session.
HARNESS_PARTS = {TRANSCRIPT: "transcript", "ledger.jsonl": "ledger", "agent.yaml": "pinned agent"}

#: What a session's bytes are counted under, in the order they are shown.
PARTS = (
    "result files",
    "transcript",
    "ledger",
    "pinned agent",
    "summary history",
    "rest of the scratchpad",
    "outputs",
    "inputs",
    "memory",
    "rest of .harness",
    "other",
)

#: How long since a session's last turn started, as `reap` measures it.
IDLE_BANDS = ((0, DAY), (DAY, 7 * DAY), (7 * DAY, 30 * DAY), (30 * DAY, None))

#: Seconds within which every folder's modified time says a copy dropped the times.
COPIED_WITHIN = 60

#: A figure at least this large is shown whole.
WHOLE_FROM = 100

#: A share below this, but above none, is shown as "<1%".
UNDER_ONE_PERCENT = 0.005

KIB = 1024


@dataclass
class Turn:
    id: str
    resumed: bool
    summarised: bool
    ok: bool = True


@dataclass
class Session:
    turns: list[Turn] = field(default_factory=list)
    recorded_results: int = 0
    #: Transcript characters by what they are.
    chars: Counter = field(default_factory=Counter)
    messages: Counter = field(default_factory=Counter)
    agent: str | None = None
    agent_model: str | None = None
    memory_on: bool = False
    memory_entries: int = 0
    unread_turns: int | None = None
    history_bytes: int | None = None
    #: Results and file texts the transcript holds as clearing's handles.
    cleared: int = 0
    #: From the run log: the main agent's model calls, in order, each with `seconds` where
    #: an earlier event of its turn says when it could have started.
    calls: list[dict[str, Any]] = field(default_factory=list)
    #: Bytes and files by `PARTS`.
    disk: Counter = field(default_factory=Counter)
    files: Counter = field(default_factory=Counter)
    #: Bytes of result files the transcript also holds whole, not yet cleared.
    repeated: int = 0
    #: Result records whose file was expired by KINGFISHER_KEEP_RESULTS_ROUNDS.
    expired_results: int = 0
    #: The folder's modified time, which every turn touches at its start.
    modified: float = 0.0

    @property
    def user_turns(self) -> list[Turn]:
        return [t for t in self.turns if not t.resumed]

    @property
    def summarised(self) -> bool:
        return any(t.summarised for t in self.turns) or self.history_bytes is not None

    @property
    def model(self) -> str:
        logged = Counter(c["model"] for c in self.calls if c.get("model"))
        if logged:
            return logged.most_common(1)[0][0]
        return self.agent_model or "(the deployment's default)"


def _json_lines(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    found = []
    for line in text.splitlines():
        start = line.find("{")
        if start < 0:
            continue
        try:
            value = json.loads(line[start:])
        except ValueError:
            continue
        if isinstance(value, dict):
            found.append(value)
    return found


def _yaml_scalar(text: str, key: str) -> str | None:
    match = re.search(rf"^{key}:[ \t]*['\"]?([^'\"\n#]+?)['\"]?[ \t]*(?:#.*)?$", text, re.M)
    return match.group(1).strip() if match else None


def _part(relative: tuple[str, ...], names: dict[str, str]) -> str:
    top, rest = relative[0], relative[1:]
    if top == HARNESS:
        named = HARNESS_PARTS.get(rest[0]) if len(rest) == 1 else None
        return named or "rest of .harness"
    if top == names["scratchpad"] and rest:
        if rest[0] == names["tool_results"]:
            return "result files"
        return "summary history" if rest[0] == HISTORY else "rest of the scratchpad"
    for part in ("outputs", "inputs", "memory"):
        if top == names[part]:
            return part
    return "rest of the scratchpad" if top == names["scratchpad"] else "other"


def _measure(folder: Path, s: Session, names: dict[str, str]) -> None:
    """Every file's bytes under its part, counted as `session_bytes` counts them for the cap."""
    for p in folder.rglob("*"):
        try:
            if not p.is_file():
                continue
            size = p.stat().st_size
        except OSError:
            continue
        part = _part(p.relative_to(folder).parts, names)
        s.disk[part] += size
        s.files[part] += 1


def read_session(folder: Path, names: dict[str, str] = FOLDERS) -> Session:
    s = Session()
    harness = folder / HARNESS
    s.modified = folder.stat().st_mtime
    _measure(folder, s, names)
    kept = _read_ledger(harness, s)
    shown = _read_transcript(harness, s)
    _count_repeated(folder, s, kept, shown)
    _read_pin(harness, s)
    _read_memory(folder / names["memory"], harness, s)

    # The folder is made with every session; only a file in it means a summary was written.
    history = folder / names["scratchpad"] / HISTORY
    if history.is_dir():
        size = sum(p.stat().st_size for p in history.rglob("*") if p.is_file())
        s.history_bytes = size or None
    return s


def _read_ledger(harness: Path, s: Session) -> list[tuple[str, str]]:
    """The turns and recorded results; `(call id, path)` of each result still kept."""
    kept: list[tuple[str, str]] = []
    for row in _json_lines(harness / "ledger.jsonl"):
        if "id" in row:
            s.recorded_results += 1
            if row.get("expired_in") is not None:
                s.expired_results += 1
            elif row.get("path") and row.get("call"):
                kept.append((str(row["call"]), str(row["path"])))
        elif "turn" in row:
            s.turns.append(
                Turn(
                    id=str(row["turn"]),
                    resumed=bool(row.get("resumed")),
                    summarised=bool(row.get("summarised")),
                )
            )
    return kept


def _read_transcript(harness: Path, s: Session) -> dict[str, int]:
    """What the kept conversation is made of; each result it still holds whole, in bytes."""
    shown: dict[str, int] = {}
    for message in _json_lines(harness / TRANSCRIPT):
        role = message.get("role", "?")
        s.messages[role] += 1
        content = message.get("content") or ""
        size = len(content) if isinstance(content, str) else len(json.dumps(content))
        if message.get("summary"):
            s.chars["summary"] += size
        elif role == "user":
            s.chars["user"] += size
        elif role == "assistant":
            s.chars["agent text"] += size
            for call in message.get("tool_calls") or ():
                args = call.get("args") or {}
                s.chars["tool-call arguments"] += len(json.dumps(args))
                s.cleared += sum(
                    1 for v in args.values() if isinstance(v, str) and CLEARED_TEXT.fullmatch(v)
                )
        elif role == "tool":
            s.chars["tool results"] += size
            if isinstance(content, str) and CLEARED_RESULT.search(content):
                s.cleared += 1
            elif isinstance(content, str) and message.get("call_id"):
                shown[str(message["call_id"])] = len(content.encode("utf-8"))
        else:
            s.chars[role] += size
    return shown


def _count_repeated(
    folder: Path, s: Session, kept: list[tuple[str, str]], shown: dict[str, int]
) -> None:
    # The smaller of the two, since the message carries the result's id as well. A call
    # no longer in the transcript was cut by a summary, and its file is the only copy.
    for call, path in kept:
        relative = Path(path.lstrip("/"))
        if call not in shown or ".." in relative.parts:
            continue
        try:
            s.repeated += min(shown[call], (folder / relative).stat().st_size)
        except OSError:
            continue


def _read_pin(harness: Path, s: Session) -> None:
    pinned = harness / "agent.yaml"
    if pinned.is_file():
        text = pinned.read_text(encoding="utf-8", errors="replace")
        s.agent = _yaml_scalar(text, "name")
        s.agent_model = _yaml_scalar(text, "model")


def _read_memory(memory: Path, harness: Path, s: Session) -> None:
    writer = harness / "memory_writer.json"
    memory_file = memory / "AGENTS.md"
    # The writer's file, not the memory file: every session is made with one of those.
    s.memory_on = writer.is_file()
    if memory_file.is_file():
        lines = memory_file.read_text(encoding="utf-8", errors="replace").splitlines()
        s.memory_entries = sum(1 for line in lines if line.startswith("- "))
    if writer.is_file():
        try:
            s.unread_turns = int(json.loads(writer.read_text(encoding="utf-8")).get("turns", 0))
        except (ValueError, TypeError, AttributeError, OSError):
            s.unread_turns = None


def read_logs(paths: list[Path], sessions: dict[str, Session]) -> int:
    """Attach the main agent's model calls and each turn's outcome; the number of events read."""
    read = 0
    ended: dict[str, bool] = {}
    by_turn: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for path in paths:
        for event in _json_lines(path):
            name, sid = event.get("event"), event.get("session_id")
            if not name or sid not in sessions:
                continue
            read += 1
            by_turn[(sid, str(event.get("turn_id")))].append(event)
            if name == "run_end":
                ended[str(event.get("turn_id"))] = bool(event.get("ok", True))
            elif name == "model_call" and not event.get("agent") and not event.get("delegations"):
                sessions[sid].calls.append(event)
    # Only a call's end is logged, so its time is from the event before it in its turn,
    # which counts kingfisher's own work ahead of the call, clearing's among it.
    main = {id(c) for s in sessions.values() for c in s.calls}
    for events in by_turn.values():
        events.sort(key=lambda e: e.get("ts", 0))
        for before, event in pairwise(events):
            if id(event) in main and "ts" in before and "ts" in event:
                event["seconds"] = event["ts"] - before["ts"]
    for s in sessions.values():
        s.calls.sort(key=lambda c: c.get("ts", 0))
        for t in s.turns:
            t.ok = ended.get(t.id, True)
    return read


def writer_turns(s: Session, every: int) -> list[int]:
    """Indexes into `s.turns` of the turns the writer ran on, as `kept_memory` decides."""
    ran, unread = [], 0
    for i, t in enumerate(s.turns):
        unread += 1
        if t.ok and (t.summarised or unread >= every):
            ran.append(i)
            unread = 0
    return ran


def _spread(values: Sequence[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)

    def rank(q: float) -> float:
        return ordered[min(len(ordered) - 1, max(0, round(q * len(ordered) + 0.5) - 1))]

    return {
        "n": len(ordered),
        "median": statistics.median(ordered),
        "p90": rank(0.9),
        "max": ordered[-1],
    }


def _band(tokens: int) -> str:
    for low, high in SIZE_BANDS:
        if tokens >= low and (high is None or tokens < high):
            return f"{low // 1000}k+" if high is None else f"{low // 1000}-{high // 1000}k"
    return "?"


def _call_times(group: list[Session]) -> dict[str, dict[str, Any]]:
    """Each input-size band's calls: how many, their seconds, the seconds of those writing
    little, and how much of what they were sent came from the cache.

    A summarised turn's calls are left out: one of them is the summariser's own, and it
    reads as the agent's.
    """
    found: dict[str, dict[str, list[float]]] = {
        _band(low): {"all": [], "short": [], "cached": []} for low, _ in SIZE_BANDS
    }
    for s in group:
        summarised = {t.id for t in s.turns if t.summarised}
        for c in s.calls:
            sent, seconds = c.get("input_tokens"), c.get("seconds")
            if not sent or seconds is None or str(c.get("turn_id")) in summarised:
                continue
            band = found[_band(sent)]
            band["all"].append(seconds)
            if (c.get("output_tokens") or 0) <= SHORT_REPLY:
                band["short"].append(seconds)
            band["cached"].append((c.get("cache_read") or 0) / sent)
    return {
        name: {
            "seconds": _spread(v["all"]),
            "seconds_writing_little": _spread(v["short"]),
            "cached_share": statistics.median(v["cached"]) if v["cached"] else None,
        }
        for name, v in found.items()
    }


def _idle_band(seconds: float) -> str:
    for low, high in IDLE_BANDS:
        if seconds >= low and (high is None or seconds < high):
            days = low // DAY
            return f"{days}+ days" if high is None else (
                "under 1 day" if low == 0 else f"{days}-{high // DAY} days"
            )
    return "in the future"


def _disk(sessions: list[Session], *, now: float) -> dict[str, Any]:
    """What every session folder holds, those with no turn too: a sweep removes them all."""
    total = sum(sum(s.disk.values()) for s in sessions)
    by_part = {}
    for part in PARTS:
        held = sum(s.disk[part] for s in sessions)
        by_part[part] = {
            "bytes": held,
            "share": held / total if total else 0.0,
            "bytes_per_session": _spread([s.disk[part] for s in sessions]),
            "files_per_session": _spread([s.files[part] for s in sessions]),
        }
    idle = {_idle_band(low): {"sessions": 0, "bytes": 0} for low, _ in IDLE_BANDS}
    for s in sessions:
        band = idle.setdefault(_idle_band(now - s.modified), {"sessions": 0, "bytes": 0})
        band["sessions"] += 1
        band["bytes"] += sum(s.disk.values())
    times = [s.modified for s in sessions]
    used = [s for s in sessions if s.user_turns]
    return {
        "bytes": total,
        "bytes_per_session": _spread([sum(s.disk.values()) for s in sessions]),
        "bytes_per_turn": _spread([sum(s.disk.values()) / len(s.user_turns) for s in used]),
        "by_part": by_part,
        "repeated_bytes": sum(s.repeated for s in sessions),
        "sessions_repeating": sum(1 for s in sessions if s.repeated),
        "expired_results": sum(s.expired_results for s in sessions),
        "sessions_with_expired_results": sum(1 for s in sessions if s.expired_results),
        "idle": idle,
        # What a copy that dropped the times leaves; idle ages are then meaningless.
        "times_look_copied": len(times) > 1 and max(times) - min(times) < COPIED_WITHIN,
    }


def _bucket(n: int) -> str:
    for low, high in TURN_BUCKETS:
        if n >= low and (high is None or n <= high):
            return f"{low}" if low == high else (f"{low}+" if high is None else f"{low}-{high}")
    return "0"


def survey(
    sessions: dict[str, Session], *, every: int, logged: bool, now: float
) -> dict[str, Any]:
    used = {k: s for k, s in sessions.items() if s.turns}
    report: dict[str, Any] = {
        "sessions": len(sessions),
        "sessions_with_no_turn": len(sessions) - len(used),
        "logged": logged,
        "memory_every": every,
    }
    ordered_buckets = [_bucket(low) for low, _ in TURN_BUCKETS]

    by_model: dict[str, list[Session]] = defaultdict(list)
    for s in used.values():
        by_model[s.model].append(s)

    models: dict[str, dict[str, Any]] = {}
    for model, group in sorted(by_model.items(), key=lambda kv: -len(kv[1])):
        lengths = [len(s.user_turns) for s in group]
        buckets = Counter(_bucket(n) for n in lengths)
        summarised = [s for s in group if s.summarised]
        first_summary = [
            next(i for i, t in enumerate(s.user_turns, 1) if t.summarised)
            for s in summarised
            if any(t.summarised for t in s.user_turns)
        ]
        if logged and any(s.calls for s in group):
            peak = [max(c.get("input_tokens", 0) for c in s.calls) for s in group if s.calls]
            peak_source = "largest single model call, from the run log"
        else:
            peak = [sum(s.chars.values()) / CHARS_PER_TOKEN for s in group]
            peak_source = "estimated from the transcript as left, after clearing and summaries"
        models[model] = {
            "sessions": len(group),
            "turns_per_session": _spread(lengths),
            "turns_buckets": {b: buckets.get(b, 0) for b in ordered_buckets},
            "sessions_with_resumes": sum(1 for s in group if len(s.turns) > len(s.user_turns)),
            "cleared_sessions": sum(1 for s in group if s.cleared),
            "summarised_sessions": len(summarised),
            "first_summarised_turn": _spread(first_summary),
            "conversation_tokens": _spread(peak),
            "conversation_tokens_from": peak_source,
        }
        if logged:
            models[model]["call_seconds_by_size"] = _call_times(group)
    report["by_model"] = models

    chars = Counter()
    for s in used.values():
        chars.update(s.chars)
    total = sum(chars.values())
    report["transcript_share"] = {
        kind: round(n / total, 4) for kind, n in chars.most_common()
    } if total else {}
    report["recorded_results_per_session"] = _spread([s.recorded_results for s in used.values()])

    with_memory = [s for s in used.values() if s.memory_on]
    ran = {id(s): writer_turns(s, every) for s in with_memory}
    memory: dict[str, Any] = {
        "sessions_with_memory_on": len(with_memory),
        "sessions_with_entries": sum(1 for s in with_memory if s.memory_entries),
        "entries_per_session": _spread([s.memory_entries for s in with_memory]),
        "writer_runs_estimated": sum(len(r) for r in ran.values()),
        "sessions_the_writer_never_ran_in": sum(1 for s in with_memory if not ran[id(s)]),
        "sessions_ending_with_unread_turns": sum(1 for s in with_memory if s.unread_turns),
        "unread_turns_at_end": _spread([s.unread_turns for s in with_memory if s.unread_turns]),
    }

    if logged:
        after, other = [], []
        for s in with_memory:
            if not s.calls:
                continue
            first = {}
            for c in s.calls:
                first.setdefault(str(c.get("turn_id")), c)
            writer_at = set(ran[id(s)])
            for i, t in enumerate(s.turns):
                c = first.get(t.id)
                # A summarised turn's first logged call may be the summariser's own.
                if i == 0 or t.summarised or c is None or not c.get("input_tokens"):
                    continue
                share = c.get("cache_read", 0) / c["input_tokens"]
                (after if i - 1 in writer_at else other).append(share)
        memory["cached_share_of_first_call"] = {
            "turns_right_after_a_writer_run": _spread(after),
            "other_turns_but_the_first": _spread(other),
        }
    report["memory"] = memory
    report["disk"] = _disk(list(sessions.values()), now=now)
    return report


def _fmt_spread(spread: dict[str, float] | None, unit: str = "") -> str:
    if not spread:
        return "none"

    def one(v: float) -> str:
        if unit == "%":
            return f"{v:.0%}"
        if unit == "bytes":
            return _size(v)
        return f"{v:,.0f}{unit}" if v == int(v) or v >= WHOLE_FROM else f"{v:,.1f}{unit}"

    return (
        f"median {one(spread['median'])}, 90th percentile {one(spread['p90'])}, "
        f"max {one(spread['max'])} (of {spread['n']})"
    )


def _share(v: float) -> str:
    return "<1%" if 0 < v < UNDER_ONE_PERCENT else f"{v:.0%}"


def _size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < KIB:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= KIB
    return f"{n:.1f} TB"


def _render_disk(d: dict[str, Any]) -> list[str]:
    out = [
        "== disk (every session folder, those with no turn too)",
        f"all sessions: {_size(d['bytes'])}",
        f"per session: {_fmt_spread(d['bytes_per_session'], 'bytes')}"
        "  (as KINGFISHER_SESSION_MAX_BYTES counts it)",
        f"per turn, sessions with one: {_fmt_spread(d['bytes_per_turn'], 'bytes')}",
        "by part: total and share, then per session bytes and files:",
    ]
    for part, p in d["by_part"].items():
        if not p["files_per_session"]["max"]:
            continue
        files = p["files_per_session"]
        out.append(
            f"  {part}: {_size(p['bytes'])} ({_share(p['share'])}); "
            f"{_fmt_spread(p['bytes_per_session'], 'bytes')}; "
            f"files median {files['median']:,.0f}, 90th percentile {files['p90']:,.0f}, "
            f"max {files['max']:,.0f}"
        )
    transcript = d["by_part"]["transcript"]["bytes"]
    out += [
        f"result files the transcript also holds whole: {_size(d['repeated_bytes'])}"
        + (f" ({_share(d['repeated_bytes'] / transcript)} of the transcript)" if transcript else "")
        + f", in {d['sessions_repeating']} sessions",
        f"result records whose file expired: {d['expired_results']}, "
        f"in {d['sessions_with_expired_results']} sessions"
        "  (none: KINGFISHER_KEEP_RESULTS_ROUNDS is off, or no session ran long enough)",
        "idle, since a turn last started (each session folder's modified time):",
    ]
    out += [
        f"  {band}: {b['sessions']} sessions, {_size(b['bytes'])}" for band, b in d["idle"].items()
    ]
    if d["times_look_copied"]:
        out.append(
            "  warning: every session folder was modified within a minute of the others; "
            "a copy that dropped modified times makes these idle figures meaningless"
        )
    return out


def render(report: dict[str, Any]) -> str:
    out = [
        f"sessions: {report['sessions']} "
        f"({report['sessions_with_no_turn']} opened with no turn, left out below)",
        f"run log read: {'yes' if report['logged'] else 'no'}",
        "",
    ]
    for model, m in report["by_model"].items():
        n = m["sessions"]
        buckets = "   ".join(
            f"{b} → {c} ({c / n:.0%})" for b, c in m["turns_buckets"].items()
        )
        out += [
            f"== {model}: {n} sessions",
            f"turns per session: {_fmt_spread(m['turns_per_session'])}",
            f"  {buckets}",
            f"sessions with a resumed (approved) turn: {m['sessions_with_resumes']}",
            f"reached the clearing mark: {m['cleared_sessions']} ({m['cleared_sessions'] / n:.0%})"
            "  (clearing's handles found in the transcript; a summary may have cut them away)",
            f"summarised at least once: {m['summarised_sessions']} "
            f"({m['summarised_sessions'] / n:.0%})"
            + (
                f"; first on turn {_fmt_spread(m['first_summarised_turn'])}"
                if m["first_summarised_turn"]
                else ""
            ),
            f"conversation size, tokens: {_fmt_spread(m['conversation_tokens'])}",
            f"  ({m['conversation_tokens_from']})",
        ]
        if "call_seconds_by_size" in m:
            out.append(
                "seconds per model call, by tokens sent (from the event before each call "
                f"to its end; 'brief' = writing at most {SHORT_REPLY} tokens):"
            )
            for band, t in m["call_seconds_by_size"].items():
                if not t["seconds"]:
                    out.append(f"  {band:>9}: no calls")
                    continue
                brief = t["seconds_writing_little"]
                out.append(
                    f"  {band:>9}: {t['seconds']['n']} calls, "
                    f"median {t['seconds']['median']:.1f}s, "
                    f"90th percentile {t['seconds']['p90']:.1f}s; brief ones median "
                    + (f"{brief['median']:.1f}s (of {brief['n']})" if brief else "none")
                    + f"; {t['cached_share']:.0%} of input from cache"
                )
        out.append("")
    share = report["transcript_share"]
    out += [
        "what the kept conversations are made of, by characters:",
        "  " + ("   ".join(f"{k} {_share(v)}" for k, v in share.items()) or "nothing"),
        f"recorded tool results per session: {_fmt_spread(report['recorded_results_per_session'])}",
        "",
    ]
    mem = report["memory"]
    out += [
        f"== memory (writer every {report['memory_every']} turns)",
        f"sessions with memory on: {mem['sessions_with_memory_on']}, "
        f"with any entry at the end: {mem['sessions_with_entries']}",
        f"entries per session: {_fmt_spread(mem['entries_per_session'])}",
        f"writer runs (worked out from the turns): {mem['writer_runs_estimated']}; "
        f"sessions it never ran in: {mem['sessions_the_writer_never_ran_in']}",
        f"sessions ending with turns the writer never read: "
        f"{mem['sessions_ending_with_unread_turns']}"
        + (
            f" ({_fmt_spread(mem['unread_turns_at_end'])} turns)"
            if mem["unread_turns_at_end"]
            else ""
        ),
    ]
    if "cached_share_of_first_call" in mem:
        cached = mem["cached_share_of_first_call"]
        after = _fmt_spread(cached["turns_right_after_a_writer_run"], "%")
        other = _fmt_spread(cached["other_turns_but_the_first"], "%")
        out += [
            "share of a turn's first model call served from cache:",
            f"  right after a writer run: {after}",
            f"  other turns:              {other}",
        ]
    out += ["", *_render_disk(report["disk"])]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("sessions", type=Path, help="the folder holding one folder per session")
    parser.add_argument("--log", type=Path, action="append", default=[], help="a run log file")
    parser.add_argument("--every", type=int, default=4, help="KINGFISHER_MEMORY_EVERY (default 4)")
    parser.add_argument(
        "--layout", action="append", default=[], metavar="FOLDER=NAME",
        help=f"a session folder the deployment renamed, one of {', '.join(FOLDERS)}",
    )
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)

    if not args.sessions.is_dir():
        parser.error(f"{args.sessions} is not a folder")
    names = dict(FOLDERS)
    for renamed in args.layout:
        folder, _, name = renamed.partition("=")
        if folder not in FOLDERS or not name:
            parser.error(f"--layout takes FOLDER=NAME, FOLDER one of {', '.join(FOLDERS)}")
        names[folder] = name
    now = time.time()
    sessions = {
        p.name: read_session(p, names)
        for p in sorted(args.sessions.iterdir())
        if p.is_dir() and not p.name.startswith(".")
    }
    if not sessions:
        parser.error(f"{args.sessions} holds no session folders")
    events = read_logs(args.log, sessions) if args.log else 0
    if args.log and not events:
        print("warning: no run-log events matched these sessions", file=sys.stderr)
    report = survey(sessions, every=args.every, logged=bool(events), now=now)
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
