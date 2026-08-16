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
    uv run main.py --seed-presets               # copy the shipped ones in

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

# Only the light end of the package at module scope. `kingfisher.infrastructure`
# reaches deepagents, which costs about a second in provider SDKs, and `--help`
# should not pay for a model it will never build.
from kingfisher import Capabilities, ConfigError, Request, ensure_layout, from_env
from kingfisher.config import Config
from kingfisher.domain.capabilities import ALL, CapabilityError, all_but
from kingfisher.domain.session import Session
from kingfisher.domain.subagent import SubagentError
from kingfisher.infrastructure import confinement, presets, skill_store, tool_store
from kingfisher.infrastructure.runlog import read_usage
from kingfisher.infrastructure.subagent_store import load_all
from kingfisher.infrastructure.subagent_store import sources as subagent_sources
from kingfisher.infrastructure.tool_store import ToolError
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
    """What a request may activate here, which is what `--list` is for.

    Read through the resolved catalogue rather than off `cfg` three times. The
    CLI only ever gets the `cfg`-derived roots, so today the two are the same
    paths -- but `--list` exists to say which names a request may use, and it
    and `build_agent` agreeing about that should be structural rather than a
    coincidence of nobody having wired anything else yet.
    """
    from kingfisher.infrastructure.agent import build_agent, registered_tools  # noqa: PLC0415
    from kingfisher.infrastructure.catalogue import resolve_catalogue  # noqa: PLC0415

    catalogue = resolve_catalogue(cfg)

    print(f"workspace : {workspace}")
    # Named rather than assumed: the catalogues may be deployed outside the
    # workspace and shared by every deployment that points at them.
    print(f"skills    : {catalogue.skills}")
    print(f"subagents : {catalogue.subagents}\n")

    # Built rather than listed: the tool set is a property of the assembled
    # agent, and a hardcoded list here would drift from the real one.
    # Rooted at a throwaway directory. An agent needs a session to root its
    # backend at, but what a workspace *offers* is a question about the
    # workspace, and answering it must not leave a session lying around --
    # `keep_runs` would eventually reap a real one to make room for the decoy.
    # Caught the way a malformed subagent is, below. `--list` is where someone
    # goes *because* something is wrong, so a loader that raises here should
    # say what to fix rather than print a traceback over the rest of the
    # inventory. Folders make the duplicate case more likely, not less: two
    # people can now add a `find_company` without ever seeing each other's.
    try:
        # Walked once and handed to the build. This listing needs two things
        # that used to be fetched apart -- where each workspace tool is defined,
        # and the built-in set, which is only knowable from an assembled graph
        # -- and a tool module is Python, so fetching them apart ran every one
        # of them twice per `--list`.
        found = tool_store.loaded(catalogue.tools)
        with tempfile.TemporaryDirectory(prefix="kingfisher-inventory-") as scratch:
            introspected = registered_tools(
                build_agent(
                    cfg,
                    session_dir=Path(scratch),
                    catalogue=catalogue,
                    workspace_tools=found,
                )
            )
    except ToolError as exc:
        print("tools")
        print(f"  cannot load: {exc}")
        return 1

    # Two headings, because they are two grants. Printed as one pile, this
    # listing advertised `read_file` beside `csv_profile` and left a reader to
    # guess which flag took which -- and guessing wrong is the "that is a
    # builtin tool" refusal. Where each workspace tool is defined goes here too,
    # so a folder is navigable rather than merely tidy.
    defined_in = {entry.name: entry.source for entry in found}
    own = tuple(sorted(defined_in))
    builtin = tuple(n for n in introspected if n not in set(defined_in))

    print("builtin tools — grant with --builtin-tools")
    for name in builtin or ("(could not introspect)",):
        print(f"  {name}")

    # The same block a refusal prints, so the two agree by construction rather
    # than by both being edited. Every entry here has a file -- the section is
    # workspace tools -- so the column is uniform, where suppressing it for
    # `http_fetch.py` and printing it for `csv_profile/` left it ragged.
    print("\nworkspace tools — grant with --tools")
    print(tool_store.offered(defined_in, own))

    print("\nskills" if cfg.skills_enabled else "\nskills (KINGFISHER_SKILLS is off)")
    for name in skill_store.names(catalogue.skills) or ("(none)",):
        print(f"  {name}")
    # Grouping skills into folders is the obvious thing to try and yields
    # nothing at all, because discovery is one level deep. Saying so is the
    # only difference between a catalogue that looks empty and one that is --
    # and it needs the reason now that tools and subagents nest freely.
    for name in skill_store.misplaced(catalogue.skills):
        print(f"  ! {name}/ holds a skill too deep to load — they live at {skill_store.LAYOUT}")
        print("    (the agent reads skills itself and only looks one level down;")
        print("     tools and subagents are read by kingfisher, so those may nest)")

    print("\nsubagents")
    try:
        specs = load_all(catalogue.subagents)
    except SubagentError as exc:
        print(f"  cannot load: {exc}")
        return 1
    where = subagent_sources(catalogue.subagents)
    for spec in specs.values() or ():
        print(f"  {spec.name}{_from(where.get(spec.name), f'{spec.name}.yaml')}"
              f" — {spec.description}")
    if not specs:
        print("  (none)  — try --seed-presets")
    return 0


def _from(source: str | None, expected: str) -> str:
    """Name the file a definition came from, when it is not the obvious one.

    Silent for anything where the name already tells you the file -- `reviewer`
    in `reviewer.yaml` is not worth a line of output. Everything else gets said,
    which is more than "is it in a folder": a package contributes tools under
    names that are not its own, so `csv_columns` comes from `csv_profile/` with
    no slash in sight and is exactly the case someone would go looking for.
    """
    return "" if source in (None, expected) else f"  ({source})"


def warn_if_unconfined(cfg: Config) -> None:
    """Say once, on every start, when nothing is keeping `execute` off the host.

    Printed rather than logged because the person who can act on it is the one
    reading this output. Silence means confined -- an unconfined shell that
    announced nothing would look exactly like a confined one, which is how this
    went unnoticed until it was measured.
    """
    confined = confinement.resolve(
        cfg.shell_sandbox,
        workspace=cfg.workspace,
        state_dir=cfg.state_dir,
        scratch_dir=cfg.scratch_dir,
        extra=cfg.shell_path_extra,
        skills=cfg.skills_dir,
    )
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
        "--seed-presets",
        action="store_true",
        help="copy the definitions kingfisher ships into the workspace",
    )
    return parser


def _offered(cfg: Config) -> dict[str, tuple[str, ...]]:
    """What this workspace offers right now, per kind.

    Built rather than listed, for the reason `--list` builds too: the tool
    surface includes whatever the workspace defined, so the only honest answer
    is an assembled agent. Rooted at a throwaway directory so answering "what
    is on offer" does not leave a session behind for `keep_runs` to reap.

    Only called when a `--without-*` flag asked, so a run that does not
    subtract pays nothing for this.

    Through the resolved catalogue, for the reason `--list` is: what a
    subtraction is taken from has to be what the run will actually offer, and
    reading `cfg` here while the agent read somewhere else would make
    `--without-skills X` refuse a name the run did not have.
    """
    from kingfisher.infrastructure.agent import (  # noqa: PLC0415
        available_skills,
        build_agent,
        defined_subagents,
        registered_tools,
    )
    from kingfisher.infrastructure.catalogue import resolve_catalogue  # noqa: PLC0415

    catalogue = resolve_catalogue(cfg)
    found = tool_store.loaded(catalogue.tools)
    workspace = tuple(sorted(entry.name for entry in found))
    with tempfile.TemporaryDirectory(prefix="kingfisher-offered-") as scratch:
        root = Path(scratch)
        registered = registered_tools(
            build_agent(cfg, session_dir=root, catalogue=catalogue, workspace_tools=found)
        )
        return {
            # The two axes, apart. They are granted apart and refused apart, and
            # a subtraction taken from the union produced a grant of built-in
            # names on the workspace axis -- which is how `--without-tools
            # execute,delete`, the example this file's own docstring gives, came
            # back as "those are builtin tools".
            "builtin_tools": tuple(n for n in registered if n not in set(workspace)),
            "tools": workspace,
            "skills": available_skills(cfg, root, catalogue=catalogue),
            "subagents": tuple(defined_subagents(cfg, root, catalogue=catalogue)),
        }


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

    if args.seed_presets:
        seeding = presets.seed(cfg)
        for name in seeding.written:
            print(f"seeded {name}")
        for name in seeding.overwritten:
            # After the list, not beside each entry: the point is that you edit
            # your copy, so losing one is the line that has to survive being
            # skimmed.
            print(f"warning: overwrote your edited {name}")

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
    print(f"model     : {cfg.model} via {cfg.api_style}")
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
        # Only when the refusal did not already list them. The tool refusals now
        # print what is on offer and where each one lives, and telling someone
        # to go and look at the thing directly above their cursor is the kind of
        # hint that teaches people to stop reading hints.
        if "this workspace offers" not in str(exc):
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
