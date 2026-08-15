"""M2 verification: do skills and memory actually work on this gateway?

Both were listed as unverified in the design record. Neither is provable from
the fake-model tests — skills load through the model's own judgement about
relevance, and memory only round-trips across two separate sessions.

Three things get checked:

  skill_read      the agent found the skill by description and read its body,
                  which is the whole progressive-disclosure mechanism
  memory_written  a durable preference stated in session one reached AGENTS.md
  memory_recalled a *new* session, with its own thread, could answer from it

    uv run python spikes/m2_capabilities.py
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from dotenv import load_dotenv

from evals.dataset import SAMPLE_NAME, seed_sample_data
from evals.seed import SKILL_NAME, seed_sample_skill
from kingfisher import ensure_layout, from_env, run

load_dotenv()

CONVENTION = "report counts as plain integers with no thousands separators"

# Chosen to match the skill's *description*, since that is all the agent sees
# before deciding whether to read the body. A `wc -l` question warrants no
# skill, and asserting otherwise tests nothing but the wording of the task.
SESSION_ONE = (
    f"Profile /data/{SAMPLE_NAME}: report the row count, the number of distinct "
    f"regions after normalising them, and any data quality problems you find.\n\n"
    f"Also, for future sessions in this project: always {CONVENTION}."
)

SESSION_TWO = (
    "Without reading any data file, state the counting convention this project "
    "uses. One short line."
)


def _log_mentions_skill(log_path: Path) -> bool:
    """Did the agent read the skill body, not merely see its description?"""
    for line in log_path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        blob = f"{record.get('input_preview', '')}{record.get('output_preview', '')}"
        if SKILL_NAME in blob and "SKILL.md" in blob:
            return True
    return False


def main() -> int:
    base = from_env()
    cfg = replace(base, skills_enabled=True, memory_enabled=True)
    workspace = ensure_layout(cfg.workspace)

    seed_sample_data(workspace)
    if seed_sample_skill(workspace):
        print(f"seeded skill /skills/{SKILL_NAME}/SKILL.md")

    memory_file = workspace / "memory" / "AGENTS.md"
    memory_file.write_text("", encoding="utf-8")  # start from a known empty state
    print(f"workspace = {workspace}")
    print("skills=on memory=on\n")

    print("--- session one: a question, plus a durable preference ---")
    first = run(SESSION_ONE, cfg=cfg)
    print(f"  {first.answer.splitlines()[0][:100] if first.answer else '(no answer)'}")

    memory = memory_file.read_text(encoding="utf-8")
    print(f"\nAGENTS.md is now {len(memory)} chars")
    if memory:
        print("  " + "\n  ".join(memory.strip().splitlines()[:6]))

    print("\n--- session two: a new thread, answering from memory alone ---")
    second = run(SESSION_TWO, cfg=cfg)
    print(f"  {second.answer[:200]}")

    checks = {
        "skill_read": _log_mentions_skill(first.log_path),
        "memory_written": "integer" in memory.lower() or "separator" in memory.lower(),
        "memory_recalled": "separator" in second.answer.lower()
        or "plain integer" in second.answer.lower(),
    }

    print()
    for name, ok in checks.items():
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    print(f"\n{sum(checks.values())}/{len(checks)} passed")
    print(f"sessions: {first.session_id} then {second.session_id}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
