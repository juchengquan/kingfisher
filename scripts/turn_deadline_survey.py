#!/usr/bin/env python3
"""Survey a deployment's run log for work that goes on past a turn's time limit: how long
`eval` programs and subagents run, how close to the limit they start, and what still runs
after it -- a program's tool calls, built-in or offered, and a subagent's model and tool
calls -- and whether any of it outlasted the session's claim.

Reads only. Prints counts and durations, never anything a user or agent said, no session
or turn id and no offered tool's name, so its output is safe to share. Standard library
only, so it runs wherever Python 3.10+ does, with or without kingfisher installed.

    python3 scripts/turn_deadline_survey.py run.log run.log.1
    python3 scripts/turn_deadline_survey.py run.log --turn-limit 1800 --grace 300 --json

Each LOG is the deployment's run log: the `kingfisher.run` logger's JSON lines, or a
`JsonlRunEvents` file, one event a line, with or without a prefix in front of the JSON.
Rotated files may be given in any order.

--turn-limit is the deployment's KINGFISHER_TURN_TIMEOUT_S, 3600 unless it set one.
--grace is how long past the limit a session stays claimed: the longest of
KINGFISHER_EXECUTION_TIMEOUT_S and every model's `timeout_s` in `models.yaml`, 120
unless one was raised. Past both, another request may take the session over while the
turn still runs in it.

A turn's clock starts at its `run_start`, a fraction of a second after kingfisher starts
it. The log records a model call only as it ends, so one is counted as begun after the
limit when the event before it in the same subagent was. A program's tool calls carry no
call id, so they are told apart by falling inside its `eval` call.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

EVAL = "eval"
TASK = "task"

#: The tools kingfisher builds rather than a deployment offers: deepagents' own, and
#: `eval`. Named only by kind in the report, so an offered tool's name never leaves it.
BUILT_IN = frozenset(
    {
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "glob",
        "grep",
        "execute",
        "write_todos",
        TASK,
        EVAL,
    }
)

#: The `stop_reason` a turn its limit stopped ends with.
OUT_OF_TIME = "max_duration"

#: A program counts as started near the limit with less than this share of the turn left.
NEAR = 0.1

#: A figure at least this large is shown whole.
WHOLE_FROM = 10

ENDS = frozenset({"tool_end", "tool_error"})


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
        if isinstance(value, dict) and "event" in value and "ts" in value:
            found.append(value)
    return found


def read_turns(paths: list[Path]) -> list[list[dict[str, Any]]]:
    """Every turn's events, in the order they happened, each turn on its own."""
    turns: dict[tuple[Any, Any], list[dict[str, Any]]] = defaultdict(list)
    for path in paths:
        for event in _json_lines(path):
            turns[event.get("session_id"), event.get("turn_id")].append(event)
    for events in turns.values():
        events.sort(key=lambda e: float(e["ts"]))
    return [events for events in turns.values() if events[0]["event"] == "run_start"]


@dataclass
class Span:
    """One `eval` or `task` call, from its start to its end."""

    tool: str
    call: str
    agent: Any
    delegations: tuple[str, ...]
    start: float
    end: float | None = None
    #: A `task` started from a program's code.
    from_program: bool = False
    calls: int = 0
    late_built_in: int = 0
    late_offered: int = 0
    late_model_calls: int = 0
    late_tool_calls: int = 0

    def holds(self, ts: float) -> bool:
        return self.start <= ts and (self.end is None or ts <= self.end)


@dataclass
class TurnFacts:
    took: float
    out_of_time: bool
    #: What was still running once the session could be taken over; empty if nothing was.
    at_lapse: str = ""
    programs: list[dict[str, Any]] = field(default_factory=list)
    subagents: list[dict[str, Any]] = field(default_factory=list)


def _where(event: dict[str, Any]) -> tuple[Any, tuple[str, ...]]:
    return event.get("agent"), tuple(event.get("delegations") or ())


class _Reading:
    """One turn's events, read in the order they happened."""

    def __init__(self, ended: float, at_limit: float) -> None:
        self.ended, self.at_limit = ended, at_limit
        self.spans: dict[str, Span] = {}
        #: Direct calls by their id, for what was running once the claim lapsed.
        self.calls: dict[str, list[float]] = {}
        #: The last event in each subagent, read as the start of the model call after it.
        self.last_in: dict[str, float] = {}

    def read(self, event: dict[str, Any]) -> None:
        name, ts = event["event"], float(event["ts"])
        tool, call = event.get("tool"), event.get("call")
        if tool in (EVAL, TASK) and call:
            self._program_or_subagent(name, tool, call, event, ts)
        elif name == "tool_start" and call is None:
            self._from_code(tool, event, ts)
        elif name == "tool_start" and call:
            self.calls[call] = [ts, self.ended]
        elif name in ENDS and call in self.calls:
            self.calls[call][1] = ts
        self._inside_subagents(name, event, ts)

    def _program_or_subagent(
        self, name: str, tool: str, call: str, event: dict[str, Any], ts: float
    ) -> None:
        if name in ENDS and call in self.spans:
            self.spans[call].end = ts
        elif name == "tool_start":
            agent, delegations = _where(event)
            span = Span(tool, call, agent, delegations, ts)
            span.from_program = tool == TASK and bool(self._programs_at(agent, delegations, ts))
            self.spans[call] = span
            self.last_in[call] = ts

    def _programs_at(self, agent: Any, delegations: tuple[str, ...], ts: float) -> list[Span]:
        return [
            s
            for s in self.spans.values()
            if s.tool == EVAL and s.agent == agent and s.delegations == delegations and s.holds(ts)
        ]

    def _from_code(self, tool: Any, event: dict[str, Any], ts: float) -> None:
        """A call from a program's code, filed under the latest program it falls inside."""
        inside = self._programs_at(*_where(event), ts)
        if not inside:
            return
        program = max(inside, key=lambda s: s.start)
        program.calls += 1
        if ts > self.at_limit and tool in BUILT_IN:
            program.late_built_in += 1
        elif ts > self.at_limit:
            program.late_offered += 1

    def _inside_subagents(self, name: str, event: dict[str, Any], ts: float) -> None:
        """Counted against every subagent the event ran inside, not the innermost alone."""
        for one in _where(event)[1]:
            span = self.spans.get(one)
            if span is None or span.tool != TASK:
                continue
            if name == "model_call" and self.last_in.get(one, span.start) > self.at_limit:
                span.late_model_calls += 1
            elif name == "tool_start" and ts > self.at_limit:
                span.late_tool_calls += 1
            self.last_in[one] = ts


def _facts_of(span: Span, *, ended: float, at_limit: float) -> dict[str, Any]:
    end = ended if span.end is None else span.end
    common = {
        "seconds": end - span.start,
        "started_with_left": at_limit - span.start,
        "running_at_limit": span.start <= at_limit < end,
    }
    if span.tool == EVAL:
        return {
            **common,
            "calls": span.calls,
            "late_built_in": span.late_built_in,
            "late_offered": span.late_offered,
        }
    return {
        **common,
        "from_program": span.from_program,
        "late_model_calls": span.late_model_calls,
        "late_tool_calls": span.late_tool_calls,
    }


def survey_turn(events: list[dict[str, Any]], *, limit: float, grace: float) -> TurnFacts:
    """What one turn's events say about its programs, its subagents and its limit."""
    began = float(events[0]["ts"])
    stopped = next((e for e in reversed(events) if e["event"] == "run_end"), None)
    ended = float((stopped or events[-1])["ts"])
    at_limit, at_lapse = began + limit, began + limit + grace

    reading = _Reading(ended, at_limit)
    for event in events:
        reading.read(event)

    facts = TurnFacts(
        took=ended - began,
        out_of_time=stopped is not None and stopped.get("stop_reason") == OUT_OF_TIME,
    )
    for span in reading.spans.values():
        kept = facts.programs if span.tool == EVAL else facts.subagents
        kept.append(_facts_of(span, ended=ended, at_limit=at_limit))
    if ended > at_lapse:
        facts.at_lapse = _running(reading.spans, reading.calls, at_lapse, ended)
    return facts


def _running(spans: dict[str, Span], calls: dict[str, list[float]], ts: float, ended: float) -> str:
    """The outermost thing still running at `ts`, as the report names it."""
    for kind, tool in (("a program", EVAL), ("a subagent", TASK)):
        if any(
            s.tool == tool and s.start <= ts <= (ended if s.end is None else s.end)
            for s in spans.values()
        ):
            return kind
    if any(start <= ts <= end for start, end in calls.values()):
        return "a tool call"
    return "a model call, or the turn's ending"


def _spread(values: list[float]) -> dict[str, float] | None:
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


def survey(turns: list[list[dict[str, Any]]], *, limit: float, grace: float) -> dict[str, Any]:
    facts = [survey_turn(events, limit=limit, grace=grace) for events in turns]
    over = [f for f in facts if f.took > limit]
    lapsed = [f for f in facts if f.at_lapse]
    programs = [p for f in facts for p in f.programs]
    subagents = [s for f in facts for s in f.subagents]
    late = [p for p in programs if p["late_built_in"] or p["late_offered"]]
    return {
        "turn_limit_s": limit,
        "grace_s": grace,
        "turns": {
            "n": len(facts),
            "stopped_by_their_limit": sum(f.out_of_time for f in facts),
            "ran_past_the_limit": len(over),
            "seconds_past_the_limit": _spread([f.took - limit for f in over]),
            "ran_past_the_claim": len(lapsed),
            "running_when_the_claim_lapsed": dict(Counter(f.at_lapse for f in lapsed)),
        },
        "programs": {
            "n": len(programs),
            "turns_with_one": sum(bool(f.programs) for f in facts),
            "seconds": _spread([p["seconds"] for p in programs]),
            "tool_calls_in_one": _spread([float(p["calls"]) for p in programs]),
            "started_near_the_limit": sum(
                0 <= p["started_with_left"] < NEAR * limit for p in programs
            ),
            "running_at_the_limit": sum(p["running_at_limit"] for p in programs),
            "with_calls_begun_after_the_limit": len(late),
            "built_in_calls_begun_after_the_limit": sum(p["late_built_in"] for p in programs),
            "offered_calls_begun_after_the_limit": sum(p["late_offered"] for p in programs),
        },
        "subagents": {
            "n": len(subagents),
            "started_from_a_program": sum(s["from_program"] for s in subagents),
            "seconds": _spread([s["seconds"] for s in subagents]),
            "running_at_the_limit": sum(s["running_at_limit"] for s in subagents),
            "model_calls_begun_after_the_limit": sum(s["late_model_calls"] for s in subagents),
            "tool_calls_begun_after_the_limit": sum(s["late_tool_calls"] for s in subagents),
        },
    }


def _one(v: float, unit: str = "s") -> str:
    return f"{v:,.0f}{unit}" if v >= WHOLE_FROM or v == int(v) else f"{v:,.1f}{unit}"


def _fmt(spread: dict[str, float] | None, unit: str = "s") -> str:
    if not spread:
        return "none"
    return (
        f"median {_one(spread['median'], unit)}, 90th percentile {_one(spread['p90'], unit)}, "
        f"max {_one(spread['max'], unit)} (of {spread['n']:,.0f})"
    )


def render(report: dict[str, Any]) -> str:
    t, p, s = report["turns"], report["programs"], report["subagents"]
    limit, grace = report["turn_limit_s"], report["grace_s"]
    lapsed = ", ".join(f"{kind} {n}" for kind, n in t["running_when_the_claim_lapsed"].items())
    return "\n".join(
        [
            f"== turns (limit {_one(limit)}, claimed {_one(grace)} past it)",
            f"turns: {t['n']:,}; stopped by their limit: {t['stopped_by_their_limit']:,}",
            f"ran past the limit: {t['ran_past_the_limit']:,}; "
            f"by {_fmt(t['seconds_past_the_limit'])}",
            f"ran past the claim, so another request could take the session: "
            f"{t['ran_past_the_claim']:,}" + (f" (still running: {lapsed})" if lapsed else ""),
            "",
            "== programs (eval)",
            f"programs: {p['n']:,}; turns with one: {p['turns_with_one']:,}",
            f"ran for: {_fmt(p['seconds'])}",
            f"tool calls in one: {_fmt(p['tool_calls_in_one'], '')}",
            f"started with under {NEAR:.0%} of the turn left: {p['started_near_the_limit']:,}; "
            f"still running at the limit: {p['running_at_the_limit']:,}",
            f"programs with tool calls begun after the limit: "
            f"{p['with_calls_begun_after_the_limit']:,}; those calls: "
            f"{p['offered_calls_begun_after_the_limit']:,} offered, "
            f"{p['built_in_calls_begun_after_the_limit']:,} built-in",
            "",
            "== subagents (task)",
            f"subagents: {s['n']:,}; started from a program: {s['started_from_a_program']:,}",
            f"ran for: {_fmt(s['seconds'])}",
            f"still running at the limit: {s['running_at_the_limit']:,}",
            f"model calls begun after the limit: {s['model_calls_begun_after_the_limit']:,}; "
            f"tool calls: {s['tool_calls_begun_after_the_limit']:,}",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("logs", type=Path, nargs="+", help="a run log file", metavar="LOG")
    parser.add_argument(
        "--turn-limit", type=float, default=3600, help="KINGFISHER_TURN_TIMEOUT_S (default 3600)"
    )
    parser.add_argument(
        "--grace", type=float, default=120,
        help="how long past the limit a session stays claimed (default 120)",
    )
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)

    if missing := [str(path) for path in args.logs if not path.is_file()]:
        parser.error(f"no such log: {', '.join(missing)}")
    turns = read_turns(args.logs)
    if not turns:
        parser.error("no turn found: no run_start event in these logs")
    report = survey(turns, limit=args.turn_limit, grace=args.grace)
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
