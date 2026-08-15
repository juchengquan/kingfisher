"""Thin driver for a kingfisher run.

Still not a CLI in the sense that matters -- kingfisher is a library and this
just drives it -- but the hand-matched argument list is gone. That approach was
right for one flag and stopped being right somewhere around the fourth, because
a request now carries a session, files, and a capability set, and none of that
was reachable from here.

    uv run main.py                              # the smoke task, checked
    uv run main.py --no-checks                  # same run, no pass/fail gate
    uv run main.py "Summarise /data/x.csv"      # anything else

    uv run main.py --list                       # what this workspace offers
    uv run main.py --seed-examples              # copy examples/ into it

    uv run main.py "Review it" --skills code-review --subagents reviewer
    uv run main.py "Count the rows" --tools read_file,write_file
    uv run main.py "Just this once" --no-memory
    uv run main.py "And now?" --session 7f3a91c2b4e0
    uv run main.py "Profile this" --input ~/data.csv

Nothing is written unless the task asks for it. Wanting a report on disk is one
kind of request among many, so there is no flag for it -- say what you want in
the task, and name the files if you care what they are called.

`--tools`, `--skills` and `--subagents` are per-request capability grants.
Omitting one means "everything this workspace offers"; passing it with an empty
value means none. Naming something that does not exist is an error, not a
silently narrower run -- `--list` shows the valid names.

`--no-checks` skips the verification, not the analysis: the run costs the same.
It exists so a smoke run can be watched without a non-zero exit breaking
whatever is driving it.

Configuration comes from .env (copy .env.example). KINGFISHER_API_STYLE has no
default on purpose: the Anthropic-compatible and OpenAI-compatible endpoints of
the same gateway do not behave identically.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

from evals.artifacts import load_result, promote_report
from evals.checks import check_result
from evals.seed import seed_sample_data, seed_sample_skill
from evals.task import SMOKE_TASK

# Only the light end of the package at module scope. `kingfisher.adapters`
# reaches deepagents, which costs about a second in provider SDKs, and `--help`
# should not pay for a model it will never build.
from kingfisher import Capabilities, ConfigError, Request, ensure_layout, from_env
from kingfisher.adapters.subagent_store import load_all
from kingfisher.adapters.workspace_fs import is_new_workspace
from kingfisher.config import Config
from kingfisher.domain.subagent import DIRECTORY as SUBAGENT_DIR
from kingfisher.domain.subagent import SubagentError

EXAMPLES = Path(__file__).resolve().parent / "examples"


def _selection(value: str | None) -> tuple[str, ...] | None:
    """A comma-separated flag into a capability selection.

    `None` (flag absent) and `()` (flag present but empty) mean opposite
    things, which is the entire point of the type, so the empty string has to
    survive as an empty tuple rather than collapsing back to unrestricted.
    """
    if value is None:
        return None
    return tuple(part.strip() for part in value.split(",") if part.strip())


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


def _skill_names(workspace: Path) -> tuple[str, ...]:
    directory = workspace / "skills"
    if not directory.is_dir():
        return ()
    return tuple(sorted(p.name for p in directory.iterdir() if (p / "SKILL.md").is_file()))


def seed_examples(workspace: Path) -> list[str]:
    """Copy the repo's example skills and subagents into the workspace.

    Copied rather than read in place: they are workspace content, and the whole
    point of the examples directory is that you edit your copy.
    """
    copied = []
    for kind in ("skills", SUBAGENT_DIR):
        source = EXAMPLES / kind
        if not source.is_dir():
            continue
        for item in sorted(source.iterdir()):
            target = workspace / kind / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(item, target)
            copied.append(f"{kind}/{item.name}")
    return copied


def show_inventory(cfg: Config, workspace: Path) -> int:
    """What a request may activate here, which is what `--list` is for."""
    from kingfisher.adapters.agent import build_agent, registered_tools  # noqa: PLC0415

    print(f"workspace : {workspace}\n")

    # Built rather than listed: the tool set is a property of the assembled
    # agent, and a hardcoded list here would drift from the real one.
    print("tools")
    for name in registered_tools(build_agent(cfg)) or ("(could not introspect)",):
        print(f"  {name}")

    print("\nskills" if cfg.skills_enabled else "\nskills (KINGFISHER_SKILLS is off)")
    for name in _skill_names(workspace) or ("(none)",):
        print(f"  {name}")

    print("\nsubagents")
    try:
        specs = load_all(workspace)
    except SubagentError as exc:
        print(f"  cannot load: {exc}")
        return 1
    for spec in specs.values() or ():
        print(f"  {spec.name} — {spec.description}")
    if not specs:
        print("  (none)  — try --seed-examples")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Run one kingfisher task. With no task, runs the smoke task.",
    )
    parser.add_argument("task", nargs="*", help="the task; omit to run the smoke task")
    parser.add_argument("--no-checks", action="store_true", help="skip the smoke's pass/fail gate")
    parser.add_argument("--session", metavar="ID", help="continue an existing session")
    parser.add_argument(
        "--input",
        metavar="PATH",
        action="append",
        default=[],
        help="a file supplied with this request; repeatable",
    )
    for name in ("tools", "skills", "subagents"):
        parser.add_argument(
            f"--{name}",
            metavar="A,B",
            help=f"activate only these {name} (empty string for none)",
        )
    parser.add_argument(
        "--no-memory",
        action="store_true",
        help="do not read the workspace memory file on this turn",
    )
    parser.add_argument("--list", action="store_true", help="show what the workspace offers")
    parser.add_argument(
        "--seed-examples", action="store_true", help="copy examples/ into the workspace"
    )
    return parser


def main(argv: list[str]) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv[1:])

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

    if args.seed_examples:
        for name in seed_examples(workspace):
            print(f"seeded {name}")

    if args.list:
        return show_inventory(cfg, workspace)

    task = " ".join(args.task).strip()
    is_smoke = not task
    if is_smoke:
        task = SMOKE_TASK
        if seed_sample_data(workspace):
            print("seeded sample dataset into /data")
        if cfg.skills_enabled and seed_sample_skill(workspace):
            print("seeded sample skill into /skills")

    capabilities = Capabilities(
        tools=_selection(args.tools),
        skills=_selection(args.skills),
        subagents=_selection(args.subagents),
        # None, not False, when the flag is absent: "no opinion" is what keeps
        # an unrestricted request unrestricted.
        memory=False if args.no_memory else None,
    )
    request = Request(
        task=task,
        session_id=args.session,
        inputs=tuple(Path(p).expanduser() for p in args.input),
        capabilities=capabilities,
    )

    missing = [p for p in request.inputs if not p.is_file()]
    if missing:
        print(f"no such input file(s): {', '.join(str(p) for p in missing)}", file=sys.stderr)
        return 2

    print(f"workspace : {workspace}")
    print(f"model     : {cfg.model} via {cfg.api_style}")
    if not capabilities.is_unrestricted:
        for kind in ("tools", "skills", "subagents"):
            selected = getattr(capabilities, kind)
            if selected is not None:
                print(f"{kind:<10}: {', '.join(selected) or '(none)'}")
        if capabilities.memory is not None:
            print(f"{'memory':<10}: {'on' if capabilities.memory else 'off'}")
    if request.inputs:
        print(f"inputs    : {', '.join(p.name for p in request.inputs)}")
    print(f"task      : {task}\n")

    # Streaming rather than run(): with no UI, a multi-minute analysis would
    # otherwise print nothing at all until it finished.
    # Deferred: this is the first thing that needs deepagents, and paths
    # that never get here (--help, --list, a bad .env) should not pay for it.
    from kingfisher import stream  # noqa: PLC0415
    from kingfisher.adapters.agent import CapabilityError  # noqa: PLC0415

    result = None
    try:
        for event in stream(request, cfg=cfg):
            if event.kind == "finished":
                result = event.result
            else:
                print(event, flush=True)
    except CapabilityError as exc:
        # A named capability the workspace does not offer. Reported here rather
        # than as a traceback because it is a usage error, not a crash.
        print(f"capability error: {exc}", file=sys.stderr)
        print("run --list to see what this workspace offers", file=sys.stderr)
        return 2

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
        if path.exists():
            print(f"{name:<12}: written  {path}")
        elif is_smoke:
            print(f"{name:<12}: MISSING  {path}")

    # Continuing this session is the next thing you will want, so say how.
    print(f"\ncontinue with: uv run main.py --session {result.session_id} \"...\"")

    if not is_smoke:
        return 0

    promoted = promote_report(result.run_dir, workspace)
    if promoted:
        print(f"promoted    : {promoted}")

    if args.no_checks:
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
