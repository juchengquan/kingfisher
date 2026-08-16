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
    uv run main.py "Analyse these" --data ~/a.pdf --data ~/b.pdf

`--input` and `--data` differ only in how long the file lives, which is the
only reason there are two. `--input` puts it in this turn's `/runs/<turn>/input`
and it leaves with the turn. `--data` puts it in the session's `/data`, where
the next turn still finds it without being handed it again. `/data` is
read-only to the agent, and `--data` is the supported way to write there --
copying files in by hand fails, and working around that with `sudo` leaves
files the harness cannot manage.

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

Output is live. The model's prose is printed as it is written, untagged and
keeping its own formatting, while progress stays tagged and aligned. The
answer is not repeated at the end -- you watched it arrive.

Configuration comes from .env (copy .env.example). KINGFISHER_API_STYLE has no
default on purpose: the Anthropic-compatible and OpenAI-compatible endpoints of
the same gateway do not behave identically.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from dotenv import load_dotenv

if TYPE_CHECKING:
    from collections.abc import Iterable
    from typing import TextIO

    from kingfisher.domain.result import RunEvent, RunResult

from evals.artifacts import load_result, promote_report
from evals.checks import check_result
from evals.seed import seed_sample_data, seed_sample_skill
from evals.task import SMOKE_TASK

# Only the light end of the package at module scope. `kingfisher.adapters`
# reaches deepagents, which costs about a second in provider SDKs, and `--help`
# should not pay for a model it will never build.
from kingfisher import Capabilities, ConfigError, Request, ensure_layout, from_env
from kingfisher.adapters import skill_store
from kingfisher.adapters.runlog import read_usage
from kingfisher.adapters.subagent_store import load_all
from kingfisher.adapters.workspace_fs import (
    LocalSessionDirs,
    ensure_session_layout,
    is_new_workspace,
)
from kingfisher.config import Config
from kingfisher.domain.session import Session
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
    """Render what `read_usage` totalled. Formatting is the driver's business;
    knowing the log's shape is the log's."""
    usage = read_usage(log_path)
    if not usage.calls:
        return "no model calls logged"
    share = "n/a" if usage.cached_share is None else f"{usage.cached_share:.0%}"
    return (
        f"{usage.calls} model calls · in={usage.input_tokens} "
        f"out={usage.output_tokens} · cached={share}"
    )


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


def prepare_smoke(cfg: Config, workspace: Path, session_id: str) -> list[str]:
    """Put the smoke's fixtures where the agent will look for them.

    `/data` is a *session's* directory, not the workspace's, so the dataset
    cannot be seeded until the session it belongs to is known -- which is why
    the smoke now fixes its session id before the run rather than letting one
    be minted inside it.

    Skills are not session-scoped and stay where they were: they are shared
    definitions, and `/skills` still routes to the workspace.
    """
    session = Session.open(workspace, session_id, LocalSessionDirs())
    ensure_session_layout(session.directory)

    seeded = []
    if seed_sample_data(session.directory):
        seeded.append("dataset into /data")
    if cfg.skills_enabled and seed_sample_skill(workspace):
        seeded.append("skill into /skills")
    return seeded


def show_inventory(cfg: Config, workspace: Path) -> int:
    """What a request may activate here, which is what `--list` is for."""
    from kingfisher.adapters.agent import build_agent, registered_tools  # noqa: PLC0415

    print(f"workspace : {workspace}")
    # Named rather than assumed: the catalogues may be deployed outside the
    # workspace and shared by every deployment that points at them.
    print(f"skills    : {cfg.skills_dir}")
    print(f"subagents : {cfg.subagents_dir}\n")

    # Built rather than listed: the tool set is a property of the assembled
    # agent, and a hardcoded list here would drift from the real one.
    # Rooted at a throwaway directory. An agent needs a session to root its
    # backend at, but what a workspace *offers* is a question about the
    # workspace, and answering it must not leave a session lying around --
    # `keep_runs` would eventually reap a real one to make room for the decoy.
    print("tools")
    with tempfile.TemporaryDirectory(prefix="kingfisher-inventory-") as scratch:
        introspected = registered_tools(build_agent(cfg, session_dir=Path(scratch)))
    for name in introspected or ("(could not introspect)",):
        print(f"  {name}")

    print("\nskills" if cfg.skills_enabled else "\nskills (KINGFISHER_SKILLS is off)")
    for name in skill_store.names(cfg.skills_dir) or ("(none)",):
        print(f"  {name}")
    # Grouping skills into folders is the obvious thing to try and yields
    # nothing at all, because discovery is one level deep. Saying so is the
    # only difference between a catalogue that looks empty and one that is.
    for name in skill_store.misplaced(cfg.skills_dir):
        print(f"  ! {name}/ holds a skill too deep to load — they live at {skill_store.LAYOUT}")

    print("\nsubagents")
    try:
        specs = load_all(cfg.subagents_dir)
    except SubagentError as exc:
        print(f"  cannot load: {exc}")
        return 1
    for spec in specs.values() or ():
        print(f"  {spec.name} — {spec.description}")
    if not specs:
        print("  (none)  — try --seed-examples")
    return 0


def render(events: Iterable[RunEvent], out: TextIO) -> RunResult | None:
    """Print a run as it happens, and return its result.

    Token events are fragments, not lines: written with no newline and no tag,
    so the model owns the left margin and its own formatting survives. Progress
    stays tagged and aligned. That mix is the whole reason for `owed` -- a
    newline is owed before the next tagged line, or it lands on the end of a
    half-finished sentence.

    The terminal event carries the `RunResult` and is not itself printed. Nor
    is `result.answer` printed afterwards: it already arrived, a word at a
    time, and saying it again below would read as the model answering twice.
    """
    result: RunResult | None = None
    owed = False
    for event in events:
        if event.kind == "token":
            out.write(event.text)
            out.flush()
            owed = True
            continue
        if owed:
            out.write("\n")
            owed = False
        if event.kind == "finished":
            result = event.result
        else:
            print(event, file=out, flush=True)
    if owed:
        out.write("\n")
        out.flush()
    return result


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
        help="a file for this turn only, in /runs/<turn>/input; repeatable",
    )
    parser.add_argument(
        "--data",
        metavar="PATH",
        action="append",
        default=[],
        help="a file kept for the whole session, in /data (read-only); repeatable",
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
    session_id = args.session
    if is_smoke:
        task = SMOKE_TASK
        # Fixed here rather than minted inside the run: the smoke's fixtures
        # belong to a session's `/data`, so the session has to be named before
        # there is anywhere to put them.
        session_id = session_id or uuid4().hex[:12]
        for seeded in prepare_smoke(cfg, workspace, session_id):
            print(f"seeded sample {seeded}")

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
        session_id=session_id,
        inputs=tuple(Path(p).expanduser() for p in args.input),
        data=tuple(Path(p).expanduser() for p in args.data),
        capabilities=capabilities,
    )

    missing = [p for p in (*request.inputs, *request.data) if not p.is_file()]
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
    if request.data:
        print(f"data      : {', '.join(p.name for p in request.data)}")
    print(f"task      : {task}\n")

    # Streaming rather than run(): with no UI, a multi-minute analysis would
    # otherwise print nothing at all until it finished. The answer is not
    # printed again after the summary -- it arrived as it was written.
    # Deferred: this is the first thing that needs deepagents, and paths
    # that never get here (--help, --list, a bad .env) should not pay for it.
    from kingfisher import stream  # noqa: PLC0415
    from kingfisher.adapters.agent import CapabilityError  # noqa: PLC0415

    result = None
    try:
        result = render(stream(request, cfg=cfg), sys.stdout)
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

    # The state directory: host-side, outlives any session, and not a name
    # the agent addresses. `workspace/derived` stopped being anywhere when
    # `/derived` became a session directory.
    promoted = promote_report(result.run_dir, cfg.state_dir)
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
