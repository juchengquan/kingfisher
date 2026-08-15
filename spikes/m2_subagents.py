"""Does sub-agent delegation actually work on this gateway?

The `task` tool is present — the general-purpose subagent is auto-added even
without a harness profile — but presence is not function. Delegation is the
last unverified mechanism, and it is the one the research and local-code
domains would lean on, since fan-out is where they pay off.

Two things get checked, and they are deliberately separate:

  delegated   the agent actually called `task` rather than doing the work
              itself, which a capable model will happily do instead
  correct     the numbers that came back are right, so the subagent ran with
              a working backend rather than hallucinating from its briefing

    uv run python spikes/m2_subagents.py
"""

from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv

from evals.dataset import SAMPLE_NAME, seed_sample_data
from kingfisher import ensure_layout, from_env, stream

load_dotenv()

# Measured from the fixture, not asserted from the generator's inputs.
EXPECTED = {"north": 4983, "east": 5059}

TASK = (
    f"/data/{SAMPLE_NAME} has a `region` column and a `units` column.\n\n"
    "Delegate this: use the task tool to spawn one subagent per region for the "
    "regions north and east. Each subagent should compute the total units for "
    "its own region alone (region names compared case-insensitively, ignoring "
    "blank units cells) and report just that number.\n\n"
    "Then give me both totals as `region: total`, one per line. Do not compute "
    "them yourself."
)


def main() -> int:
    cfg = from_env()
    workspace = ensure_layout(cfg.workspace)
    seed_sample_data(workspace)
    print(f"workspace = {workspace}\n")

    delegations = 0
    result = None
    for event in stream(TASK, cfg=cfg):
        if event.kind == "finished":
            result = event.result
        else:
            if event.kind == "model_call" and "task" in event.tools:
                delegations += event.tools.count("task")
            print(event, flush=True)

    if result is None:
        print("no result")
        return 1

    answer = result.answer.lower()
    print(f"\nanswer:\n{result.answer}\n")

    found = {
        region: str(total) in answer.replace(",", "")
        for region, total in EXPECTED.items()
    }
    checks = {
        "delegated": delegations > 0,
        "correct": all(found.values()),
    }

    for name, ok in checks.items():
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    print(f"  task calls: {delegations}")
    print(f"  totals found: {found} (expected {EXPECTED})")

    log = Path(result.log_path).read_text(encoding="utf-8").splitlines()
    calls = [json.loads(line) for line in log if '"model_call"' in line]
    print(f"  model calls: {len(calls)}  in={sum(c['input_tokens'] for c in calls)}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
