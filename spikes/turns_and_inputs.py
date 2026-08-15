"""Two turns in one session, with a file supplied to the first.

Exercises the turn tier against a live model:

  isolated   each turn writes into its own directory, so the second cannot
             overwrite the first one's answer — the defect that motivated this
  continuous the thread still carries context across turns, which is what makes
             them a conversation rather than two unrelated requests
  scoped     a file supplied with a request lands in that turn's input/, never
             in /data, and is gone when the turn is swept

    uv run python spikes/turns_and_inputs.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from dotenv import load_dotenv

from kingfisher import ensure_layout, from_env, run

load_dotenv()

UPLOAD_NAME = "quarterly_headcount.csv"
UPLOAD = "team,headcount\nplatform,14\ndata,9\nsupport,22\ndesign,5\n"


def main() -> int:
    cfg = from_env()
    workspace = ensure_layout(cfg.workspace)
    session = "conversation-demo"

    upload = Path(tempfile.mkdtemp()) / UPLOAD_NAME
    upload.write_text(UPLOAD, encoding="utf-8")

    print(f"workspace = {workspace}")
    print(f"session   = {session}\n")

    print("--- turn one: a file supplied with the request ---")
    first = run(
        "What is the total headcount in the file supplied with this request? "
        "Answer with the number and the filename you read.",
        cfg=cfg,
        session_id=session,
        inputs=[upload],
    )
    print(f"  turn {first.turn_id}: {first.answer.splitlines()[0][:120]}")

    print("\n--- turn two: same session, no file supplied ---")
    second = run(
        "Which team in that file had the fewest people? Answer from our earlier "
        "exchange without reading any file.",
        cfg=cfg,
        session_id=session,
    )
    print(f"  turn {second.turn_id}: {second.answer.splitlines()[0][:120]}")

    checks = {
        "isolated": first.run_dir != second.run_dir
        and (first.run_dir / "report.md").exists()
        and (second.run_dir / "report.md").exists(),
        "continuous": "design" in second.answer.lower(),
        "scoped": (first.run_dir / "input" / UPLOAD_NAME).exists()
        and not (workspace / "data" / UPLOAD_NAME).exists(),
        "totalled": "50" in first.answer,
    }

    print()
    for name, ok in checks.items():
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    print(f"\n{sum(checks.values())}/{len(checks)} passed")
    print(f"turn dirs: {first.run_dir.name}, {second.run_dir.name} under {first.run_dir.parent.name}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
