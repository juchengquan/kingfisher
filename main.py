"""Thin driver for a kingfisher run.

Deliberately not a CLI: kingfisher is a library, and this is the script surface
that drives it. No argument parsing, no subcommands, no flags to maintain.

    uv run main.py                          # the smoke task, on sample data
    uv run main.py "Summarise /data/x.csv"  # anything else

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
from kingfisher.smoke import SMOKE_TASK, promote_report, seed_sample_data


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

    workspace = ensure_layout(cfg.workspace)

    task = " ".join(argv[1:]).strip()
    is_smoke = not task
    if is_smoke:
        task = SMOKE_TASK
        if seed_sample_data(workspace):
            print("seeded sample dataset into /data")

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

    if is_smoke:
        promoted = promote_report(result.run_dir, workspace)
        if promoted:
            print(f"\npromoted to {promoted}")
            print("compare against the previous run with:")
            print(f"  git -C {workspace} diff -- reports/smoke.md")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
