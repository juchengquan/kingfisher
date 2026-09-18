"""Do turns overlap when a caller runs them on threads?

`docs/decisions.md`, under *Taken: the async turn path goes*, sends a caller who
wants turns to overlap to threads, "since a turn is almost all waiting on the
model -- reasoned, not measured". This is the measurement.

    uv run python spikes/concurrent_turns.py

`test_turns_in_separate_sessions_are_in_flight_at_once` asks the same question of
kingfisher's own code, with a barrier and no gateway. This asks it of the
gateway, which is the half a fake model cannot answer: an endpoint free to serve
one request at a time would make the threads pointless however good the library
is.

Eight turns, each in its own session, run one after another and then together.
Exits non-zero under a 2x speedup -- loose on purpose, because the claim under
test is that they overlap at all, not that they overlap perfectly.

**A warm-up turn runs first and is not timed.** The system prompt is identical
across all of these and the endpoint caches it, so without one the second batch
is measured against a prefix the first batch paid for, and threads win by an
amount that has nothing to do with threads.
"""

from __future__ import annotations

import sys
from concurrent import futures
from pathlib import Path
from time import perf_counter

from dotenv import load_dotenv

# The repository root, so the driver imports. A spike is run as a script, so `sys.path`
# starts at `spikes/`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: Enough that anything serialising the turns is unmistakable, and the number the
#: unit test pools for the same reason.
CONCURRENT = 8

#: Under this, the run fails. A turn is 1.5-1.9s of mostly waiting, so eight that
#: overlap should land near one turn and eight that queue near eight.
FLOOR = 2.0

ECHO = """\
name: echo
description: Answers in one word. Exists so a turn costs as little as a turn can.
builtin_tools: []
tools: []
system_prompt: |
  Reply with exactly the word you are given and nothing else.
"""


def main() -> int:
    load_dotenv()

    from kingfisher import Kingfisher, config_from_env, default_backend
    from kingfisher.domain.request import Request
    from kingfisher.kinds.documents import SUFFIX

    cfg = config_from_env()

    # Its own agent rather than whichever the workspace happens to hold: what is
    # being timed is the turn, so the agent has no tools to reach for and nothing
    # to think about.
    root = cfg.catalogue_roots["agents"]
    root.mkdir(parents=True, exist_ok=True)
    definition = root / f"echo{SUFFIX}"
    written = not definition.exists()
    if written:
        definition.write_text(ECHO, encoding="utf-8")

    try:
        kf = Kingfisher(cfg, backend=default_backend)

        def turn(word: str) -> bool:
            return kf.run(Request(word, agent="echo")).completed

        print("warming the prompt cache (1 turn, not timed)", flush=True)
        turn("warmup")

        print(f"one after another ({CONCURRENT} turns) ...", flush=True)
        began = perf_counter()
        one_at_a_time = [turn(f"word{n}") for n in range(CONCURRENT)]
        sequential = perf_counter() - began

        print(f"together ({CONCURRENT} turns) ...", flush=True)
        began = perf_counter()
        with futures.ThreadPoolExecutor(max_workers=CONCURRENT) as pool:
            together = list(pool.map(turn, [f"other{n}" for n in range(CONCURRENT)]))
        threaded = perf_counter() - began
    finally:
        if written:
            definition.unlink(missing_ok=True)

    if not all(one_at_a_time) or not all(together):
        print("\nsome turns did not complete; the timings mean nothing", file=sys.stderr)
        return 1

    speedup = sequential / threaded
    print(f"\nsequential : {sequential:6.2f}s  ({sequential / CONCURRENT:.2f}s per turn)")
    print(f"threaded   : {threaded:6.2f}s  ({threaded / CONCURRENT:.2f}s per turn)")
    print(f"speedup    : {speedup:6.2f}x  over {CONCURRENT} turns")

    if speedup < FLOOR:
        print(
            f"\nunder {FLOOR}x: these turns did not overlap. Either the gateway "
            f"serialises them or something in the turn does -- the unit test says "
            f"which, since it asks the same question without a gateway.",
            file=sys.stderr,
        )
        return 1
    print("\nturns overlap on threads")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
