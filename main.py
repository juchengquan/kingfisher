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
    uv run main.py --seed-assets                # copy in what a pack ships

A workspace that has never been used seeds itself on its first run and prints
what it wrote, so `--seed-assets` is for re-seeding an existing one after
installing or upgrading a pack. That overwrites, which is the point of asking.

Both have a shipped equivalent -- `kingfisher list` and `kingfisher seed` --
which is what an installed kingfisher has, since this file is not in the wheel.
They print through the same code; these flags stay because this is the driver
you already have open.

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

`--builtin-tools`, `--tools`, `--skills` and `--subagents` are per-request
capability grants. Omitting one means "everything this workspace offers";
passing it with an empty value means none. Naming something that does not exist
is an error, not a silently narrower run -- `--list` shows the valid names.

Tools come in two kinds and are granted apart. `--builtin-tools` names what the
agent ships with (`read_file`, `execute`); `--tools` names what this workspace
defines in `tools/`. Naming one under the other is refused rather than resolved,
and `--list` prints them under separate headings for exactly that reason.

    uv run main.py "Review it" --without-builtin-tools execute,delete

`--without-builtin-tools` and its three siblings say the same thing by
subtraction, which is usually what you mean: "not the shell" rather than the
other eleven names.

It resolves against what the workspace offers *now* and stores the result, so a
grant written this way is an ordinary list and behaves like one -- including
going stale if the workspace later gains a tool. That is deliberate: a grant
that kept subtracting would let a future tool through by default, and a run
says which names it left out either way.

Pass one form or the other for a given kind, not both.

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
import sys
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

# Only the light end of the package at module scope. `kingfisher.infrastructure`
# reaches deepagents, which costs about a second in provider SDKs, and `--help`
# should not pay for a model it will never build.
from kingfisher import (
    Capabilities,
    ConfigError,
    Request,
    ensure_layout,
    from_env,
    paths_from_env,
)
from kingfisher.config import Config
from kingfisher.domain.capabilities import ALL, CapabilityError, all_but
from kingfisher.domain.session import Session
from kingfisher.infrastructure import confinement, seeding
from kingfisher.infrastructure.harness.runlog import read_usage
from kingfisher.infrastructure.workspace_fs import (
    LocalSessionDirs,
    ensure_session_layout,
    is_new_workspace,
)

#: The grants this driver exposes, in the order they are listed and reported.
#: `builtin_tools` and `tools` are two axes rather than one because they are
#: granted, refused and withheld apart everywhere else -- `--list` and this
#: driver were the last places treating them as one pile.
GRANTS: tuple[str, ...] = ("builtin_tools", "tools", "skills", "subagents")


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
    """Print what a request may activate here, which is what `--list` is for.

    Neither half is here any more. Working out what is on offer is
    `infrastructure.inventory`, because this and `--without-skills` were
    computing it apart; formatting it is `cli.listing`, because `kingfisher
    list` prints the same block and a second copy of a listing is how two
    listings come to disagree.

    Two doors, one implementation -- which is the whole reason this driver keeps
    its flags rather than losing them to the shipped command. Removing them
    would have cost 23 edits across 8 files to buy something this import already
    gives.
    """
    from kingfisher.cli.listing import failed, render  # noqa: PLC0415
    from kingfisher.infrastructure.inventory import inventory  # noqa: PLC0415

    found = inventory(cfg)
    for line in render(found, workspace=workspace):
        print(line)
    return 1 if failed(found) else 0


def warn_if_unconfined(cfg: Config) -> None:
    """Say once, on every start, when nothing is keeping `execute` off the host.

    Printed rather than logged because the person who can act on it is the one
    reading this output. Silence means confined -- an unconfined shell that
    announced nothing would look exactly like a confined one, which is how this
    went unnoticed until it was measured.
    """
    confined = confinement.shell_confinement(cfg)
    if confined.warning:
        print(f"WARNING   : {confined.warning}", file=sys.stderr)


class Progress:
    """Print run events as they arrive, and keep the terminal readable.

    Token events are fragments, not lines: written with no newline and no tag,
    so the model owns the left margin and its own formatting survives. Progress
    stays tagged and aligned. That mix is the whole reason for `_owed` -- a
    newline is owed before the next tagged line, or it lands on the end of a
    half-finished sentence.

    A class rather than a loop because there are two loops: `stream` is
    synchronous and `astream` is not, and the formatting is the same either
    way. Writing it twice is how the two would come to disagree about when a
    newline is owed.
    """

    def __init__(self, out: TextIO) -> None:
        self._out = out
        self._owed = False
        self._speaker: str | None = None

    def write(self, event: RunEvent) -> RunResult | None:
        """Show one event. Returns the `RunResult` if this was the last."""
        if event.kind == "token":
            # Prose from a delegate arrives on the same stream as the caller's
            # own, as the same type, with nothing between them -- so without a
            # marker the two answers read as one. It cannot go on the fragment
            # itself: chunks split mid-word, and there is no line to tag. So it
            # goes at the seam, which is the only place a boundary exists.
            if event.agent != self._speaker:
                if self._owed:
                    self._out.write("\n")
                    self._owed = False
                self._speaker = event.agent
                print(f"[{event.agent or 'main'}]", file=self._out, flush=True)
            self._out.write(event.text)
            self._out.flush()
            self._owed = True
            return None
        if self._owed:
            self._out.write("\n")
            self._owed = False
        # The terminal event carries the result and is not itself printed. Nor
        # is `result.answer` printed afterwards: it already arrived, a word at
        # a time, and saying it again would read as the model answering twice.
        if event.kind == "finished":
            return event.result
        print(event, file=self._out, flush=True)
        return None

    def close(self) -> None:
        """Settle any newline still owed, so the next writer starts clean."""
        if self._owed:
            self._out.write("\n")
            self._out.flush()
            self._owed = False


def render(events: Iterable[RunEvent], out: TextIO) -> RunResult | None:
    """Drain a synchronous stream through `Progress`."""
    progress = Progress(out)
    result: RunResult | None = None
    for event in events:
        result = progress.write(event) or result
    progress.close()
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
    parser.add_argument("--list", action="store_true", help="show what the workspace offers")
    parser.add_argument(
        "--seed-assets",
        action="store_true",
        help="copy the definitions from installed asset packs into the workspace",
    )
    return parser


def _offered(cfg: Config) -> dict[str, tuple[str, ...]]:
    """What this workspace offers right now, per kind.

    One line, because the question is answered in one place. It used to build
    its own agent here -- a second implementation of `--list`, and the one that
    mattered more: a subtraction has to be taken from the set the run will
    actually offer, or `--without-skills X` refuses a name the run did not have.

    Only called when a `--without-*` flag asked, so a run that does not subtract
    still pays nothing for it.
    """
    from kingfisher.infrastructure.inventory import inventory  # noqa: PLC0415

    return inventory(cfg).offered


def _refuse_the_other_axis(
    excluded: dict[str, tuple[str, ...] | None], offered: dict[str, tuple[str, ...]]
) -> None:
    """Name the flag a subtraction meant, when it named the other axis.

    `--without-tools execute` is what this driver's docstring advertised until
    the two axes were split, so it is the mistake someone arrives with. Left to
    `all_but` it comes back as "cannot exclude unknown name(s): execute" beside
    a list that does not contain it -- true, unhelpful, and the reader has no
    way to know a second flag exists.

    The same sentence a request already gets for the same mistake, said one step
    earlier: `_refuse_unknown_tools` tells a caller naming `read_file` under
    `tools` that it is a builtin tool. This tells them where to subtract it.
    """
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
    """Each grant, whether it was written as a list or as a subtraction.

    `--tools` and `--without-tools` are two ways to say the same thing, so
    passing both is refused rather than resolved: whichever precedence we chose,
    the other reading is the one somebody meant.

    Four kinds, not three. `builtin_tools` is its own grant because the library
    has always had it and this driver simply never offered it -- so `--tools`
    narrowed the workspace half while every built-in stayed granted, and there
    was no way from a command line to withhold `execute` at all.
    """
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
    # no `models.yaml` -- it is a file *inside* the workspace -- so `from_env`
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

    # A new workspace seeds itself. Nothing is copied unless a pack was
    # installed, which is somebody's explicit choice; a new workspace is empty
    # by definition, so nothing can be overwritten; and this is the first moment
    # the destination exists. It says what it wrote, because `is_new_workspace`
    # also fires on a *misconfigured* one -- a wrong path holding ten files
    # reads more like success than an empty one does.
    #
    # Here and never in `Kingfisher.__init__`: constructing a library object
    # must not write to somebody's disk.
    if fresh or args.seed_assets:
        result = seeding.seed(paths)
        for name in result.written:
            print(f"seeded {name}")
        for name in result.overwritten:
            # After the list, not beside each entry: the point is that you edit
            # your copy, so losing one is the line that has to survive being
            # skimmed.
            print(f"warning: overwrote your edited {name}")

    try:
        cfg = from_env()
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
    from kingfisher import stream  # noqa: PLC0415

    result = None
    try:
        result = render(stream(request, cfg=cfg), sys.stdout)
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
