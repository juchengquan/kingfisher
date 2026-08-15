"""Thin driver for a kingfisher run.

Deliberately not a CLI: kingfisher is a library, and this is the script surface
that drives it. One flag, no subcommands, no argument parser.

    uv run main.py                          # the smoke task, checked, exits non-zero on failure
    uv run main.py --no-checks              # the same run, without the pass/fail gate
    uv run main.py "Summarise /data/x.csv"  # anything else

`--no-checks` skips the verification, not the analysis: the run costs the
same. It exists so a smoke run can be watched without a non-zero exit
breaking whatever is driving it.

Configuration comes from .env (copy .env.example). KINGFISHER_API_STYLE has no
default on purpose: the Anthropic-compatible and OpenAI-compatible endpoints of
the same gateway do not behave identically.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from kingfisher import ConfigError, ensure_layout, from_env, stream
from kingfisher.app.smoke import (
    SMOKE_TASK,
    check_result,
    load_result,
    promote_report,
    seed_sample_data,
    seed_sample_skill,
)
from kingfisher.domain.workspace import is_new_workspace


def _usage_summary(log_path: Path) -> str:
    calls = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if '"model_call"' in line
    ]
    if not calls:
        return "no model calls logged"
    total_in = sum(c["input_tokens"] for c in calls)
    cached = sum(c["cache_read"] for c in calls)
    share = f"{cached / total_in:.0%}" if total_in else "n/a"
    return (
        f"{len(calls)} model calls · in={total_in} "
        f"out={sum(c['output_tokens'] for c in calls)} · cached={share}"
    )


def main(argv: list[str]) -> int:
    load_dotenv()

    try:
        cfg = from_env()
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        print("copy .env.example to .env and fill it in", file=sys.stderr)
        return 2

    fresh = is_new_workspace(cfg.workspace)
    workspace = ensure_layout(cfg.workspace)
    if fresh:
        print(f"created a new workspace at {workspace}")

    # One flag, matched by hand rather than with argparse: kingfisher is a
    # library and this is a driver, so the argument surface stays something you
    # can read in a line.
    args = [a for a in argv[1:] if a != "--no-checks"]
    run_checks = "--no-checks" not in argv[1:]

    task = " ".join(args).strip()
    is_smoke = not task
    if is_smoke:
        task = SMOKE_TASK
        if seed_sample_data(workspace):
            print("seeded sample dataset into /data")
        if cfg.skills_enabled and seed_sample_skill(workspace):
            print("seeded sample skill into /skills")

    print(f"workspace : {workspace}")
    print(f"model     : {cfg.model} via {cfg.api_style}")
    print(f"task      : {task}\n")

    # Streaming rather than run(): with no UI, a multi-minute analysis would
    # otherwise print nothing at all until it finished.
    result = None
    for event in stream(task, cfg=cfg):
        if event.kind == "finished":
            result = event.result
        else:
            print(event, flush=True)

    if result is None:  # pragma: no cover
        print("run produced no result", file=sys.stderr)
        return 1

    print()
    print(f"session   : {result.session_id}")
    print(f"run_dir   : {result.run_dir}")
    print(f"usage     : {_usage_summary(result.log_path)}")
    if result.swept:
        print(f"swept     : {len(result.swept)} old session(s)")

    print(f"\n{result.answer}\n")

    for name in ("report.md", "result.json"):
        path = result.run_dir / name
        print(f"{name:<12}: {'written' if path.exists() else 'MISSING'}  {path}")

    if not is_smoke:
        return 0

    promoted = promote_report(result.run_dir, workspace)
    if promoted:
        print(f"promoted    : {promoted}")

    if not run_checks:
        return 0

    # The regression signal is the structured result, not the prose: two runs
    # on identical input rewrite the report entirely while the numbers hold.
    payload = load_result(result.run_dir)
    if payload is None:
        print("\nresult.json missing or unparseable — cannot check", file=sys.stderr)
        return 1

    checks = check_result(payload)
    print()
    for check in checks:
        print(check)

    failed = [c for c in checks if not c.ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
