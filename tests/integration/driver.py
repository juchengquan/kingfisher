"""Thin driver for a kingfisher run. Not shipped, and not a unit test.

Under `tests/integration/` because that is what it is: the one thing here that
reaches a real model and spends real money, where the 1,600 tests beside it are
offline and finish in eleven seconds. It is not collected -- pytest takes
`test_*.py`, and this is deliberately not that, because a module whose job is to call
a model must never be run by a bare `pytest`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from dotenv import load_dotenv

if TYPE_CHECKING:

    pass

# The repository root, so `evals` imports when this is run by path. Python puts
# the *script's* directory on `sys.path`, which was the root while this file was
# `main.py` and is `tests/integration/` now -- so `uv run
# tests/integration/driver.py` would fail on an import that has not changed.
#
# `python -m tests.integration.driver` needs none of this, and neither does
# importing it from a test. But the path is what anyone will type, and a driver
# that only works when invoked the less obvious way is a trap. The spikes carry
# the same three lines for the same reason.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from evals.artifacts import load_result, promote_report
from evals.checks import check_result
from evals.seed import seed_sample_data, seed_sample_skill
from evals.task import SMOKE_TASK

# Only the light end of the package at module scope. `kingfisher.infrastructure`
# reaches deepagents, which costs about a second in provider SDKs, and `--help`
# should not pay for a model it will never build.
from kingfisher import (
    Capabilities,
    ConfigError,
    Request,
    config_from_env,
    ensure_layout,
    paths_from_env,
)
from kingfisher.config import Config
from kingfisher.domain.capabilities import ALL, CapabilityError, all_but
from kingfisher.domain.session import Session
from kingfisher.infrastructure.harness.runlog import read_usage
from kingfisher.infrastructure.sandbox import confinement
from kingfisher.infrastructure.workspace import seeding
from kingfisher.infrastructure.workspace.layout import is_new_workspace
from kingfisher.infrastructure.workspace.sessions import LocalSessionDirs, ensure_session_layout
from kingfisher.presentation.cli.progress import show

#: The grants this driver exposes, in the order they are listed and reported.
#: `builtin_tools` and `tools` are two axes rather than one because they are
#: granted, refused and withheld apart everywhere else -- `--list` and this
#: driver were the last places treating them as one pile.
GRANTS: tuple[str, ...] = ("builtin_tools", "tools", "skills", "subagents")


def _selection(value: str | None) -> tuple[str, ...] | None:
    """A comma-separated flag into a capability selection."""
    if value is None:
        return None
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _usage_summary(log_path: Path) -> str:
    """Render what `read_usage` totalled."""
    usage = read_usage(log_path)
    if not usage.calls:
        return "no model calls logged"
    share = "n/a" if usage.cached_share is None else f"{usage.cached_share:.0%}"
    return (
        f"{usage.calls} model calls · in={usage.input_tokens} "
        f"out={usage.output_tokens} · cached={share}"
    )


def prepare_smoke(cfg: Config, workspace: Path, session_id: str) -> list[str]:
    """Put the smoke's fixtures where the agent will look for them."""
    session = Session.open(workspace, session_id, LocalSessionDirs())
    ensure_session_layout(session.directory)

    seeded = []
    if seed_sample_data(session.directory):
        seeded.append("dataset into /data")
    if cfg.skills_enabled and seed_sample_skill(workspace):
        seeded.append("skill into /skills")
    return seeded


def show_inventory(cfg: Config, workspace: Path) -> int:
    """Print what a request may activate here, which is what `--list` is for.

    Two doors, one implementation -- which is the whole reason this driver keeps its
    flags rather than losing them to the shipped command. Removing them would have
    cost 23 edits across 8 files to buy something this import already gives.
    """
    from kingfisher.application.inventory import inventory
    from kingfisher.presentation.cli.listing import failed, render

    found = inventory(cfg)
    for line in render(found):
        print(line)
    return 1 if failed(found) else 0


def warn_if_unconfined(cfg: Config) -> None:
    """Say once, on every start, when nothing is keeping `execute` off the host.

    Printed rather than logged because the person who can act on it is the one
    reading this output. Silence means confined -- an unconfined shell that announced
    nothing would look exactly like a confined one, which is how this went unnoticed
    until it was measured.
    """
    confined = confinement.shell_confinement(cfg)
    if confined.warning:
        print(f"WARNING   : {confined.warning}", file=sys.stderr)


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
    for name in GRANTS:
        spoken = name.replace("_", " ")
        parser.add_argument(
            f"--{name.replace('_', '-')}",
            dest=name,
            metavar="A,B",
            help=f"activate only these {spoken} (empty string for none)",
        )
        parser.add_argument(
            f"--without-{name.replace('_', '-')}",
            dest=f"without_{name}",
            metavar="A,B",
            help=f"activate every {spoken[:-1]} except these",
        )
    parser.add_argument(
        "--no-memory",
        action="store_true",
        help="do not read the workspace memory file on this turn",
    )
    parser.add_argument(
        "--agent",
        metavar="NAME",
        help="which agent runs this, from the workspace's agents/ (--list shows them)",
    )
    # Kept when `--seed`, `--from` and `--all` went, and not because it is
    # convenient: `kingfisher list` prints the same block through the same
    # renderer, so as a *listing* it is a duplicate. What it also is, and what
    # nothing else here provides, is the one way to reach `main` and have it
    # return without calling a model -- which is how the tests exercise
    # workspace creation and first-run seeding without spending anything.
    parser.add_argument("--list", action="store_true", help="show what the workspace offers")
    return parser


def _offered(cfg: Config) -> dict[str, tuple[str, ...]]:
    """What this workspace offers right now, per kind."""
    from kingfisher.application.inventory import inventory

    return inventory(cfg).offered


def _refuse_the_other_axis(
    excluded: dict[str, tuple[str, ...] | None], offered: dict[str, tuple[str, ...]]
) -> None:
    """Name the flag a subtraction meant, when it named the other axis."""
    for kind, other, describes in (
        ("tools", "builtin_tools", "builtin tool"),
        ("builtin_tools", "tools", "tool of this workspace"),
    ):
        leave_out = excluded[kind]
        if not leave_out:
            continue
        if misplaced := tuple(n for n in leave_out if n in set(offered[other])):
            many = len(misplaced) > 1
            msg = (
                f"--without-{kind.replace('_', '-')} names {', '.join(misplaced)}, "
                f"but {'those are' if many else 'that is a'} {describes}"
                f"{'s' if many else ''} -- subtract "
                f"{'them' if many else 'it'} with --without-{other.replace('_', '-')}"
            )
            raise CapabilityError(msg)


def _grants(cfg: Config, args: argparse.Namespace) -> dict[str, tuple[str, ...] | None]:
    """Each grant, whether it was written as a list or as a subtraction."""
    chosen: dict[str, tuple[str, ...] | None] = {}
    excluded = {kind: _selection(getattr(args, f"without_{kind}")) for kind in GRANTS}
    both = [k for k in excluded if getattr(args, k) is not None and excluded[k] is not None]
    if both:
        names = ", ".join(f"--{k.replace('_', '-')} and --without-{k.replace('_', '-')}"
                          for k in both)
        msg = f"pass one or the other, not both: {names}"
        raise ValueError(msg)

    offered = _offered(cfg) if any(v is not None for v in excluded.values()) else {}
    if offered:
        _refuse_the_other_axis(excluded, offered)
    for kind in GRANTS:
        leave_out = excluded[kind]
        named = getattr(args, kind)
        # A kind nobody mentioned is *absent* from the result, not `None` in it.
        # The two are opposite: `Capabilities` starts `tools`, `skills` and
        # `builtin_tools` at `ALL`, and `None` on those fields means *none*. So
        # passing `None` for a flag that was never typed asked for an agent with
        # no tools and no skills -- which is what `main.py "task"` had been
        # quietly requesting, against a docstring promising the opposite.
        if leave_out is None and named is None:
            continue
        chosen[kind] = (
            _selection(named)
            if leave_out is None
            else all_but(leave_out, offered=offered[kind])
        )
    return chosen


def main(argv: list[str]) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv[1:])

    # The directories first, and the catalogue after. A brand-new workspace has
    # no `models.yaml` -- it is a file *inside* the workspace -- so `config_from_env`
    # used to fail before the directory it needed had been created, and the
    # error told you to run `--seed-assets`, which failed the same way. A first
    # run could not reach seeding at all, which is precisely the run seeding is
    # for. This ordering is what makes the message it prints true.
    try:
        paths = paths_from_env()
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        print("copy .env.example to .env and fill it in", file=sys.stderr)
        return 2

    fresh = is_new_workspace(paths.workspace)
    workspace = ensure_layout(paths.workspace)
    if fresh:
        print(f"created a new workspace at {workspace}")

    # A new workspace seeds itself. It is empty by definition, so nothing can be
    # overwritten, and this is the first moment the destination exists. It says
    # what it wrote, because `is_new_workspace` also fires on a *misconfigured*
    # one -- a wrong path holding ten files reads more like success than an
    # empty one does.
    #
    # Here and never in `Kingfisher.__init__`: constructing a library object
    # must not write to somebody's disk.
    #
    # Stops rather than warns when no source is configured. Warning and carrying
    # on was the alternative, and the smoke would even have run -- it copies its
    # own sample skill. But that warning fires once per workspace, in the middle
    # of "created a new workspace at ...", and is the one line saying something
    # did *not* happen. An error nobody can walk past is the right shape for a
    # condition hit once whose fix is a line in `.env`.
    if fresh:
        try:
            source = seeding.definitions_source(paths)
        except ConfigError as exc:
            print(f"configuration error: {exc}", file=sys.stderr)
            return 2
        result = seeding.seed(paths, source)
        for name in result.written:
            print(f"seeded {name}")
        for left in result.skipped:
            # The reason `--agent researcher` would otherwise fail with nothing
            # to go on. A definition naming middleware or groups this deployment
            # has not registered is refused when it is built, so `seed` leaves it
            # behind -- and a driver that printed only what it wrote would send
            # you looking for a file it decided not to copy.
            print(
                f"skipped {left.label} — needs {', '.join(left.names)}; "
                f"register those, then `kingfisher seed --all`"
            )
        for name in result.overwritten:
            # After the list, not beside each entry: the point is that you edit
            # your copy, so losing one is the line that has to survive being
            # skimmed.
            print(f"warning: overwrote your edited {name}")

    try:
        cfg = config_from_env()
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        print("copy .env.example to .env and fill it in", file=sys.stderr)
        return 2

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

    try:
        selected = _grants(cfg, args)
    except (CapabilityError, ValueError) as exc:
        print(f"{exc}", file=sys.stderr)
        return 2

    capabilities = Capabilities(
        **selected,
        # None, not False, when the flag is absent: "no opinion" is what keeps
        # an unrestricted request unrestricted.
        memory=False if args.no_memory else None,
    )
    request = Request(
        task=task,
        agent=args.agent,
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
    print(f"model     : {cfg.models.default} via {cfg.models.resolve()[0].endpoint}")
    warn_if_unconfined(cfg)
    if not capabilities.is_unrestricted:
        for kind in GRANTS:
            selected = getattr(capabilities, kind)
            # `ALL` is not a narrowing and has no list to print. Only reachable
            # for `builtin_tools`, which defaults to it -- the other three
            # default to `None` or a tuple.
            if selected is not None and selected != ALL:
                print(f"{kind.replace('_', ' '):<14}: {', '.join(selected) or '(none)'}")
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
    from kingfisher import stream

    result = None
    try:
        result = show(stream(request, cfg=cfg), sys.stdout)
    except CapabilityError as exc:
        # A named capability the workspace does not offer. Reported here rather
        # than as a traceback because it is a usage error, not a crash.
        print(f"capability error: {exc}", file=sys.stderr)
        # Only for a refusal that fits on one line. Those name something and
        # stop, and `--list` is the next thing to try. A refusal that runs to
        # several has already listed what is on offer or said what to change,
        # and pointing at the screen someone is reading is the kind of hint that
        # teaches people to stop reading hints. It was matched on a phrase until
        # a second multi-line refusal arrived that did not contain it.
        if "\n" not in str(exc):
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
    print(
        f"\ncontinue with: uv run tests/integration/driver.py "
        f'--session {result.session_id} "..."'
    )

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
