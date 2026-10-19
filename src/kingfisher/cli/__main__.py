"""`kingfisher`, and `python -m kingfisher.cli`."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import time
import warnings
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import load_dotenv

import kingfisher
from kingfisher import (
    UNSCOPED,
    AccessError,
    ArtifactError,
    CapabilityError,
    ConfigError,
    Decision,
    DecisionError,
    Held,
    QuotaExceededError,
    Request,
    Resume,
    SessionBusyError,
    SubagentError,
    UnknownSessionError,
    UnsafeReferenceError,
    config_from_env,
    definitions_source,
    ensure_definitions,
    inventory,
    paths_from_env,
    seed,
)
from kingfisher.core.access import with_datazone

# Private names, taken where they live: the command ships in the library's own wheel.
from kingfisher.core.config import MissingCredentialsWarning
from kingfisher.core.definitions.agents.spec import AgentError

from .health import _retired, examine, worst
from .listing import as_json, failed, origins_document, render
from .progress import show

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from typing import TextIO

    from kingfisher import Config, Kingfisher, PendingDecision, RunResult
    from kingfisher.plugins import JsonlRunEvents

#: Read from the working directory only: a bare `load_dotenv()` walks up looking for
#: one, and a command should not pick up a file two directories above.
ENV_FILE = ".env"

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kingfisher",
        description=(
            "Fill a kingfisher workspace, run a task in it, see what is in it, "
            "and check it over."
        ),
    )
    # Not required, so bare `kingfisher` prints help rather than argparse's usage error.
    sub = parser.add_subparsers(dest="command")
    seeding = sub.add_parser(
        "seed",
        help="copy definitions into the workspace",
        description=(
            "Copies definitions into this deployment's catalogues, from the "
            "directory KINGFISHER_ASSETS names or the one you pass. Nothing "
            "ships with kingfisher, so one of the two has to say where. "
            "Overwrites, which is how you take an upgrade and is why it has to "
            "be asked for."
        ),
    )
    # A directory rather than a package: a path needs no wheel, metadata or publish step.
    seeding.add_argument(
        "--from",
        dest="source",
        metavar="DIR",
        help="seed from this directory instead of the one KINGFISHER_ASSETS names",
    )
    # Off by default: a definition naming middleware is refused when built, so a fresh
    # workspace would be filled with files that cannot run.
    seeding.add_argument(
        "--all",
        dest="everything",
        action="store_true",
        help=(
            "also copy definitions held back by default: the ones naming "
            "middleware, which a workspace that has not registered those names "
            "cannot build, and the ones naming source ids, which do nothing "
            "until a source_ids.yaml declares them"
        ),
    )
    doing = sub.add_parser(
        "run",
        help="run one task",
        # Wrapped by hand: the raw formatter keeps the blank lines and wraps nothing.
        description=(
            "Runs one task and prints the answer.\n"
            "\n"
            "The answer goes to stdout and everything you watch goes to stderr, so\n"
            "`kingfisher run ... > answer.md` keeps the answer alone and\n"
            "`2>/dev/null` keeps the quiet.\n"
            "\n"
            "The exit code says how the turn ended, because stdout is prose and\n"
            "there is nowhere else to put it:\n"
            "\n"
            "  0  finished\n"
            "  1  stopped at a bound -- the answer is what was reached, and what\n"
            "     it wrote is still there\n"
            "  2  never ran"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    doing.add_argument("task", help="what to do, in your own words")
    # Required, never defaulted: the agent decides which endpoint gets the prompts and
    # whose credentials pay.
    doing.add_argument(
        "--agent",
        required=True,
        help="which agent runs this (`kingfisher list` shows them, and where they are read from)",
    )
    doing.add_argument(
        "--session",
        metavar="ID",
        help="the session to run in, started if no turn has named it; without one, 'default'",
    )
    doing.add_argument(
        "--input",
        metavar="PATH",
        action="append",
        default=[],
        help="a file for the agent to read, in /inputs (read-only); repeatable",
    )
    # Off by default: files gone before somebody knew to look for them are worse than a
    # directory they can delete later.
    doing.add_argument(
        "--delete-session",
        action="store_true",
        help=(
            "delete the session once the turn finishes, so a one-off run leaves "
            "nothing behind. A turn stopped at a bound keeps its session, and "
            "says so"
        ),
    )
    doing.add_argument(
        "--log",
        metavar="FILE",
        type=Path,
        help=(
            "append what the turn did -- each model call with its tokens, each tool "
            "call -- to FILE as JSON lines. Without it, nothing is kept"
        ),
    )
    # Unlike `list --as`, absent is not the operator's view: a turn acts, so the library
    # refuses it.
    _add_caller(doing, _TURN_CALLER)
    # A verb rather than flags on `run`: answering is not asking, and `run`'s task would
    # have to become optional.
    deciding = sub.add_parser(
        "decide",
        help="answer a turn that stopped for approval",
        description=(
            "Answers the gated calls a turn stopped on, and runs the rest of it.\n"
            "\n"
            "With no decision, prints what the session is waiting on and does\n"
            "nothing -- which is the way to get the ids back if you have lost\n"
            "the output of the run that paused.\n"
            "\n"
            "Every pending call has to be answered in one go. The turn resumes\n"
            "mid-superstep, and a half-answered gate would run the approved call\n"
            "and leave the other hanging.\n"
            "\n"
            "The exit code is `run`'s, because a resumed turn ends every way an\n"
            "asked one does -- including at a second gate:\n"
            "\n"
            "  0  finished\n"
            "  1  stopped again -- at a bound, or at another gate\n"
            "  2  never ran"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    deciding.add_argument("--session", metavar="ID", required=True, help="the waiting session")
    deciding.add_argument(
        "--approve",
        metavar="CALL_ID",
        action="append",
        default=[],
        help="run this call as proposed; repeatable",
    )
    deciding.add_argument(
        "--reject",
        metavar="CALL_ID",
        action="append",
        default=[],
        help="do not run this call; repeatable",
    )
    # `ID=TEXT` rather than a second flag, which somebody could forget to pair with it.
    deciding.add_argument(
        "--respond",
        metavar="CALL_ID=TEXT",
        action="append",
        default=[],
        help="answer in the tool's place with TEXT, without running it; repeatable",
    )
    deciding.add_argument(
        "--agent",
        help=(
            "refuse unless the session paused under this agent. Optional, and "
            "checked rather than applied: a session keeps the agent it opened with"
        ),
    )
    deciding.add_argument(
        "--delete-session",
        action="store_true",
        help="delete the session once the turn finishes, as `run` does",
    )
    deciding.add_argument(
        "--log",
        metavar="FILE",
        type=Path,
        help="append what the resumed turn did to FILE as JSON lines, as `run --log` does",
    )
    _add_caller(deciding, _TURN_CALLER)
    checkup = sub.add_parser(
        "doctor",
        help="check everything that stands between this install and a run",
        # Wrapped by hand for the paragraph break: a limit buried in one block is one
        # nobody reaches.
        description=(
            "The configuration, the credentials it names, every catalogue,\n"
            "whether every definition can actually run, and what is confining the\n"
            "shell. Exits non-zero only on something that will stop a run.\n"
            "\n"
            "Nothing here calls a model. That is what makes it cheap enough to run\n"
            "before a deployment rather than after its first failure, and it is\n"
            "also the limit: a credential reported as present may still be\n"
            "rejected, and nothing shipped proves a call succeeds. The honest test\n"
            "is your own task through `kingfisher.run` -- a better one than any\n"
            "fixture of ours."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    checkup.add_argument(
        "--json",
        action="store_true",
        help="emit the same checks as JSON, for a script rather than a person",
    )
    listing = sub.add_parser(
        "list",
        help="show what this workspace offers a request",
        description=(
            "Every name a request may activate here, per grant, with where each "
            "one came from. Exits non-zero if anything here will not load, if a\n"
            "definition names something that is no longer where it says, or if one "
            "names a subagent\nnothing here defines."
        ),
    )
    listing.add_argument(
        "--json",
        action="store_true",
        help="emit the same answer as JSON, for a script rather than a person",
    )
    _add_caller(
        listing,
        "show what these source ids reach: comma-separated names, or UNSCOPED "
        "for the operator's view of everything",
    )
    fetching = sub.add_parser(
        "artifact",
        help="fetch one file a turn produced",
        description=(
            "Fetches a file a turn left under /outputs or /memory, by the name\n"
            "the run printed for it -- `outputs/report.html`, not a path on\n"
            "this machine. Read through the session's backend, so it works\n"
            "wherever that backend keeps the session.\n"
            "\n"
            "Without --out the bytes go to standard output, to pipe or redirect."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    fetching.add_argument("--session", metavar="ID", required=True, help="the session")
    fetching.add_argument("name", help="the artifact, as the run named it")
    fetching.add_argument("--out", metavar="PATH", help="write it here instead")
    _add_caller(
        fetching,
        "who is calling: comma-separated source ids, or UNSCOPED to read "
        "with no caller. Required where the workspace declares source ids",
    )
    holding = sub.add_parser(
        "sessions",
        help="show the sessions this workspace is holding",
        description=(
            "Every session in the workspace, most recently used first, with how\n"
            "long it has been idle and what it holds on disk.\n"
            "\n"
            "The idle column is in the units `reap --older-than` takes, so a\n"
            "listing reads straight into a sweep.\n"
            "\n"
            "The size is a walk per session -- about a millisecond each -- which\n"
            "a workspace holding thousands will feel.\n"
            "\n"
            "Asked of the sessions KINGFISHER_SESSIONS\n"
            "names, or of <workspace>/sessions where it names none -- so a\n"
            "deployment whose own keep sessions elsewhere sets it for this to see them."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    holding.add_argument(
        "--json",
        action="store_true",
        help="emit the same answer as JSON, for a script rather than a person",
    )
    sweeping = sub.add_parser(
        "reap",
        help="delete sessions this workspace is finished with",
        description=(
            "Deletes a session's directory, its conversation, the lock a turn\n"
            "holds, and whatever a wired store kept. All four, because removing\n"
            "three of them leaves the fourth to accumulate.\n"
            "\n"
            "With nothing else said it sweeps whatever KINGFISHER_SESSION_TTL_S\n"
            "calls expired -- seven days by default -- and spares any session\n"
            "with a turn running in it.\n"
            "\n"
            "There is no --dry-run. `kingfisher sessions` is the preview: it\n"
            "lists every session with the age this sweeps on."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Exclusive: handed an age and a name, the command would have to pick a question.
    chosen = sweeping.add_mutually_exclusive_group()
    chosen.add_argument(
        "--older-than",
        type=_older_than,
        metavar="AGE",
        help=(
            "sweep what has been idle this long instead: 30m, 12h, 7d -- or 0 "
            "for every session no turn is running in"
        ),
    )
    chosen.add_argument(
        "--session",
        metavar="ID",
        help=(
            "reap this one by name, whatever its age -- and whether or not a "
            "turn is running in it, which that turn will not survive"
        ),
    )
    return parser


_TURN_CALLER = (
    "who is calling: comma-separated source ids, or UNSCOPED to run "
    "with no caller. Required where the workspace declares source ids"
)


def _add_caller(parser: argparse.ArgumentParser, help_text: str) -> None:
    parser.add_argument(
        "--as",
        dest="held",
        type=_held,
        default=None,
        metavar="SOURCE_IDS",
        help=help_text,
    )
    parser.add_argument(
        "--datazone",
        default=None,
        metavar="NAME",
        help="the datazone the caller is in, one of the vocabulary's 'datazones:'",
    )


def _seed(source: str | None = None, *, everything: bool = False) -> int:
    """Fill the definitions folder."""
    paths = paths_from_env()
    # Before the source is asked for, which refuses when none is named: a deployment
    # with nothing to seed still gets its folders and `models.yaml.example`.
    ensure_definitions(paths.definitions)

    tree = definitions_source(paths, source)
    written = seed(paths, tree, everything=everything)
    for line in written.report():
        print(line)

    # Non-zero: `--from ./definitions/skills` is a likely mistake that reads without error.
    if (closing := written.nothing_seeded()) is not None:
        print(closing)
        return 1
    return 0


#: Where kingfisher's own code is, which is what decides who a warning is for.
_OURS = Path(kingfisher.__file__).resolve().parent


@contextmanager
def _plain_warnings() -> Iterator[None]:
    """Kingfisher's own warnings as one `warning:` line each, on stderr.

    Only its own: a dependency's file and line are the only pointer to code the reader
    did not write.
    """
    # Inside `catch_warnings` so the handler is put back: `main` runs in-process in
    # the tests, and a handler left installed would reformat every later warning.
    with warnings.catch_warnings():
        before = warnings.showwarning

        def plain(  # noqa: PLR0913, PLR0917 -- the signature `warnings.showwarning` has
            message: Warning | str,
            category: type[Warning],
            filename: str,
            lineno: int,
            file: TextIO | None = None,
            line: str | None = None,
        ) -> None:
            if Path(filename).resolve().is_relative_to(_OURS):
                print(f"warning: {message}", file=file or sys.stderr)
            else:
                before(message, category, filename, lineno, file, line)

        # The documented hook; ty types it as the stdlib's own function, which no
        # replacement is.
        warnings.showwarning = plain  # ty: ignore[invalid-assignment]
        yield


def _run(args: argparse.Namespace) -> int:
    """Run one task, and say how it ended in the only channel that is left."""
    missing = [p for p in args.input if not Path(p).expanduser().is_file()]
    if missing:
        # Before the model, or this mistake costs money to discover.
        print(f"no such file: {', '.join(missing)}", file=sys.stderr)
        return 2

    cfg = config_from_env()
    kf = _configured(cfg, log=args.log)
    request = Request(
        task=args.task,
        agent=args.agent,
        session_id=args.session,
        inputs=tuple(Path(p).expanduser() for p in args.input),
    )
    return _drive(
        kf,
        request,
        held=with_datazone(args.held, args.datazone),
        delete_session=args.delete_session,
    )


def _configured(cfg: Config, *, log: Path | None = None) -> Kingfisher:
    """The `Kingfisher` a verb builds, with the sessions and plugins its settings name.

    `--log` replaces any run events `KINGFISHER_PLUGINS` names: it is the narrower ask.
    """
    # Here, not at module scope: `Kingfisher` loads deepagents, and `seed`, `list` and
    # `doctor` never build one.
    from kingfisher import Kingfisher  # noqa: PLC0415

    from .configured import (  # noqa: PLC0415
        configured_plugins,
        configured_sessions,
    )

    wiring = configured_plugins().keywords()
    if (kept := _kept_in(log)) is not None:
        wiring["run_events"] = kept
    return Kingfisher(cfg, sessions=configured_sessions(), **wiring)


def _kept_in(log: Path | None) -> JsonlRunEvents | None:
    """Where `--log` keeps a turn's events, or `None` for the library's default."""
    if log is None:
        return None
    from kingfisher.plugins import JsonlRunEvents  # noqa: PLC0415

    return JsonlRunEvents(log.expanduser())


def _drive(
    kf: Kingfisher, asked: Request | Resume, *, held: Held | None, delete_session: bool
) -> int:
    """Stream one turn and say how it ended, whether it was asked or answered.

    Shared, because a resumed turn ends every way an asked one does, a second gate
    included. Takes what it reads rather than the `Namespace`, so it never reads a flag a
    verb lacks.
    """
    result = show(kf.stream(asked, source_ids=held), sys.stdout, sys.stderr)
    if result is None:
        # Not a shape the library produces; said rather than reported as success.
        print("the run ended without a result", file=sys.stderr)
        return 2

    print(f"\nsession {result.session_id}  turn {result.turn_id}", file=sys.stderr)
    if not result.completed:
        # Different words: running again continues a turn cut off at a bound, but
        # discards what a gate stopped to ask.
        if result.pending:
            _awaiting(result)
        else:
            print(
                f"stopped: {result.stop_reason} -- the answer above is what was "
                f"reached, and what it wrote is in /outputs and /memory",
                file=sys.stderr,
            )
        if delete_session:
            # Said, because this ending declines a flag that was asked for.
            print(
                f"session kept: continue it with --session {result.session_id}, "
                f"or remove it with kingfisher reap --session {result.session_id}",
                file=sys.stderr,
            )
        return 1
    if delete_session:
        _discard(kf, result)
    return 0


def _decide(args: argparse.Namespace) -> int:
    """Answer a waiting session, or say what it is waiting on."""
    decisions: list[Decision] = [
        *(Decision(call_id=one, action="approve") for one in args.approve),
        *(Decision(call_id=one, action="reject") for one in args.reject),
    ]
    for written in args.respond:
        call_id, sep, text = written.partition("=")
        if not sep or not text:
            # Refused here: the library's refusal would describe a form nobody typed.
            print(f"--respond wants CALL_ID=TEXT, got {written!r}", file=sys.stderr)
            return 2
        decisions.append(Decision(call_id=call_id, action="respond", message=text))

    # Refused, not ignored: with no turn to act on, a silent `--agent` reads as the check
    # its help promises having passed.
    unused = [
        flag
        for flag, given in (
            ("--agent", args.agent is not None),
            ("--delete-session", args.delete_session),
            ("--log", args.log is not None),
        )
        if given and not decisions
    ]
    for flag in unused:
        print(
            f"{flag} applies only when answering, with --approve, --reject or --respond",
            file=sys.stderr,
        )
    if unused:
        return 2

    cfg = config_from_env()
    if not decisions:
        return _show_pending(
            _configured(cfg), args.session, with_datazone(args.held, args.datazone)
        )
    return _drive(
        _configured(cfg, log=args.log),
        Resume(
            session_id=args.session,
            decisions=tuple(decisions),
            agent=args.agent,
        ),
        held=with_datazone(args.held, args.datazone),
        delete_session=args.delete_session,
    )


def _show_pending(kf: Kingfisher, session_id: str, held: Held | None) -> int:
    """What this session is waiting on, for somebody who has lost the ids.

    Asked as the caller `--as` names: it shows somebody's tool calls and their arguments,
    and must not be the one door into a session that skips the access check.
    """
    session = kf.session(session_id, source_ids=held)
    if session is None:
        print(f"no such session: {session_id}", file=sys.stderr)
        return 2
    waiting = session.pending()
    if not waiting:
        print(f"session {session_id} is not waiting on a decision", file=sys.stderr)
        return 2
    print(f"session {session_id} is waiting on {len(waiting)} call(s):", file=sys.stderr)
    _list_calls(waiting)
    return 1


def _list_calls(calls: Sequence[PendingDecision]) -> None:
    """Each waiting call, and what it will accept."""
    for call in calls:
        whose = f" (via {call.agent})" if call.agent else ""
        print(f"  {call.call_id}  {call.tool}{whose}  {call.args}", file=sys.stderr)
        print(f"      takes: {', '.join(call.decisions)}", file=sys.stderr)


def _answer(call: PendingDecision) -> str:
    """One flag answering `call`, by the first decision it takes: a flag it refuses would
    make the whole printed line one `decide` refuses."""
    takes = call.decisions or ("approve",)
    if "approve" in takes:
        return f"--approve {call.call_id}"
    if "reject" in takes:
        return f"--reject {call.call_id}"
    return f"--respond {shlex.quote(f'{call.call_id}=<your answer>')}"


def _awaiting(result: RunResult) -> None:
    """What a turn that stopped at an approval gate owes the person who ran it.

    Prints the ids, the only way to answer, and says that running again supersedes the
    gate rather than answering it.
    """
    print(
        f"stopped: {result.stop_reason} -- waiting for a decision on "
        f"{len(result.pending)} call(s):",
        file=sys.stderr,
    )
    _list_calls(result.pending)
    # Every call: `decide` refuses an answer that leaves one waiting.
    print(
        f"answer with: kingfisher decide --session {result.session_id} "
        + " ".join(_answer(call) for call in result.pending),
        file=sys.stderr,
    )
    print(
        f"running again on --session {result.session_id} discards these instead",
        file=sys.stderr,
    )


def _discard(kf: Kingfisher, result: RunResult) -> None:
    """Delete the session this run used, having named what goes with it.

    Listed first: nothing else prints them, and after this they are gone. A failed
    deletion keeps the exit code, which says how the turn ended; 1 means it was cut short.
    """
    if result.artifacts:
        many = "" if len(result.artifacts) == 1 else "s"
        print(
            f"the session goes, and {len(result.artifacts)} file{many} with it:",
            file=sys.stderr,
        )
        for name in result.artifacts:
            print(f"  {name}", file=sys.stderr)
    # `UNSCOPED`: the command deletes the session its own turn just used.
    session = kf.session(result.session_id, source_ids=UNSCOPED)
    failure = session.delete() if session is not None else None
    if failure:
        print(f"session not deleted -- {failure}", file=sys.stderr)


def _held(raw: str) -> Held:
    """`--as A,B` as the source ids it names, or the explicit absence of any."""
    if raw.strip() == "UNSCOPED":
        return UNSCOPED
    return tuple(part.strip() for part in raw.split(",") if part.strip())


#: What a span may be written in, and the whole of it.
AGES: dict[str, int] = {"m": 60, "h": 3600, "d": 24 * 3600}


def _older_than(raw: str) -> float:
    """`30m`, `12h`, `7d` as seconds. A bare number is refused.

    Not read as seconds: `--older-than 7` meant as a week would sweep every idle session.
    `0` is exempt, being the same in every unit.
    """
    text = raw.strip()
    if text == "0":
        return 0.0
    unit = AGES.get(text[-1:])
    number = text[:-1]
    if unit is None or not number.replace(".", "", 1).isdigit():
        msg = f"{raw!r} needs a unit: 30m, 12h, 7d -- or 0 for all of them"
        raise argparse.ArgumentTypeError(msg)
    return float(number) * unit


def _list(*, as_document: bool = False, held: Held | None = None) -> int:
    """Print what the workspace offers."""
    cfg = config_from_env()
    # `UNSCOPED` and no `--as` are the same view: a listing is read-only, run on the host
    # with the policy in front of its reader, so it is exempt from a turn's refusal.
    source_ids = held if isinstance(held, tuple) else None
    from .configured import configured_sessions  # noqa: PLC0415

    found = inventory(cfg, source_ids=source_ids, sessions=configured_sessions())
    if as_document:
        print(json.dumps(as_json(found), indent=2, sort_keys=True))
    else:
        for line in render(found):
            print(line)
    return 1 if failed(found) else 0


def _age(seconds: float) -> str:
    """A span in the units `--older-than` takes, so a listing reads into a flag.

    Floored: rounded, 1.6 days would print `2d` and `reap --older-than 2d` would spare it.
    """
    for suffix, size in (("d", 24 * 3600), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{int(seconds // size)}{suffix}"
    return "<1m"


def _size(count: int) -> str:
    """A number of bytes as a person reads it."""
    for suffix, size in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if count >= size:
            return f"{count / size:.1f} {suffix}"
    return f"{count} B"


def _count(sessions: int) -> str:
    """`N sessions`, and `1 session` when that is what it is."""
    return "1 session" if sessions == 1 else f"{sessions} sessions"


def _sessions(*, as_document: bool = False) -> int:
    """What this workspace is holding, and what each session costs it."""
    cfg = config_from_env()
    kf = _configured(cfg)
    # From `origins`, not the workspace: configured sessions live where their backends
    # say. A path only where kingfisher knows it, rather than a guess.
    kept = kf.origins.sessions
    root: Path | None = kf.cfg.sessions_root if kept.kind == "default" else None
    where = str(root) if root is not None else "the sessions this deployment configured"
    now = time.time()
    # A walk per session, ~0.8ms each: a workspace large enough to mind wants an index,
    # not a cheaper column.
    held = [(info, info.size()) for info in kf.sessions()]

    if as_document:
        print(
            json.dumps(
                {
                    # As in the text form: the two forms must say the same thing.
                    "root": None if root is None else str(root),
                    "sessions": [
                        {
                            "id": info.id,
                            "last_used": info.last_used,
                            "idle_seconds": now - info.last_used,
                            "bytes": size,
                        }
                        for info, size in held
                    ],
                },
                indent=2,
            )
        )
        return 0

    if not held:
        print(f"no sessions in {where}")
        return 0

    print(f"{_count(len(held))}, {_size(sum(size for _, size in held))}, in {where}")
    print()
    width = max(len(info.id) for info, _ in held)
    for info, size in held:
        idle = _age(now - info.last_used)
        print(f"  {info.id.ljust(width)}  {idle.rjust(5)} idle  {_size(size).rjust(9)}")
    return 0


def _reap(args: argparse.Namespace) -> int:
    """Delete sessions: one by name, or every one that has been idle too long."""
    cfg = config_from_env()
    kf = _configured(cfg)
    if args.session is not None:
        return _reap_one(kf, args.session)

    # Read once, so the age that decided is the age printed.
    age = kf.cfg.session_ttl_s if args.older_than is None else args.older_than
    result = kf.reap(older_than_seconds=age, now=time.time())

    for gone in result.removed:
        print(f"reaped {gone}")
    for failure in result.failures:
        print(f"not reaped -- {failure}", file=sys.stderr)

    if not result.removed and not result.failures:
        _nothing_reaped(
            result.kept, result.busy, age, from_config=args.older_than is None
        )
    return 1 if result.failures else 0


def _nothing_reaped(kept: int, busy: int, age: float, *, from_config: bool) -> None:
    """Say what decided, because a sweep that removes nothing reads as a broken one.

    Silent, it sends its next user to delete the directory by hand, leaving the
    conversation and claim behind. `busy` before the age: `kept` includes busy sessions,
    which no age sweeps.
    """
    if kept == 0:
        print("nothing to reap -- this workspace holds no sessions")
        return
    if busy == kept:
        # No age sweeps these, so naming one would be advice that cannot work.
        held = "it" if kept == 1 else "each of them"
        print(f"nothing to reap -- a turn is running in {held}; reap again when it finishes")
        return
    rest = kept - busy
    waited = f"has been idle longer than {_age(age)}"
    if busy:
        print(f"nothing to reap -- {_count(busy)} is busy, and none of the other "
              f"{_count(rest)} here {waited}")
        return
    why = f"none of the {_count(kept)} here {waited}"
    if not from_config:
        print(f"nothing to reap -- {why}")
        return
    print(f"nothing to reap -- {why}, which is KINGFISHER_SESSION_TTL_S")
    print("--older-than sweeps on a shorter age: kingfisher reap --older-than 1d")


def _artifact(args: argparse.Namespace) -> int:
    """Fetch one file a turn produced, to a file or to standard output."""
    cfg = config_from_env()
    kf = _configured(cfg)
    session = kf.session(args.session, source_ids=args.held, datazone=args.datazone)
    if session is None:
        msg = f"no session {args.session!r}"
        raise UnknownSessionError(msg)
    content = session.artifact(args.name)
    if args.out is None:
        # The buffer, not `print`: a text stream would re-encode a PDF.
        sys.stdout.buffer.write(content)
        sys.stdout.flush()
        return 0
    Path(args.out).write_bytes(content)
    print(f"wrote {args.name} to {args.out}")
    return 0


def _reap_one(kf: Kingfisher, session_id: str) -> int:
    """Reap one session by name, whatever its age and whatever is running in it."""
    # `UNSCOPED`, not `None`, which a policied deployment refuses: this is housekeeping,
    # not a call on anyone's behalf, which is why neither `reap` nor `sessions` takes `--as`.
    session = kf.session(session_id, source_ids=UNSCOPED)
    if session is None:
        # Refused here: deleting answers `None` for nothing there too, so a mistyped id
        # would report work nothing did.
        msg = f"no session {session_id!r}"
        raise UnknownSessionError(msg)
    failure = session.delete()
    if failure:
        print(f"not reaped -- {failure}", file=sys.stderr)
        return 1
    print(f"reaped {session_id}")
    return 0


def _doctor(*, as_document: bool = False, from_dotenv: frozenset[str] | None = None) -> int:
    """Say what would stop a run, and what would merely surprise."""
    try:
        # Silenced: the `credentials` check says it, inside the report.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", MissingCredentialsWarning)
            cfg = config_from_env()
    except ConfigError:
        # Before the error: `examine` needs a `Config`, and a deployment still setting
        # retired variables can fail for want of what replaced them, such as `models.yaml`.
        # On `stderr`, so `--json` emits a document or nothing.
        for check in _retired():
            print(f"{check.verdict}  {check.name}  {check.detail}", file=sys.stderr)
            print(f"      -> {check.remedy}", file=sys.stderr)
        raise
    # Built once and handed to `examine`: one it built from `cfg` would agree with `cfg`
    # by construction, and could never say the configuration is being ignored.
    from .configured import configured_sessions  # noqa: PLC0415

    sessions = configured_sessions()
    found = inventory(cfg, sessions=sessions)
    checks = examine(cfg, found, from_dotenv=from_dotenv, sessions=sessions)
    origins = found.origins
    if as_document:
        # With the origins: the text form opens with them, and the two forms must agree.
        print(
            json.dumps(
                {"origins": origins_document(origins), "checks": [vars(c) for c in checks]},
                indent=2,
            )
        )
    else:
        # Before the checks, because it is what they are about.
        for line in origins.block():
            print(line)
        print()
        width = max(len(check.name) for check in checks)
        for check in checks:
            mark = {"ok": "ok  ", "warn": "warn", "fail": "FAIL"}[check.verdict]
            print(f"{mark}  {check.name.ljust(width)}  {check.detail}")
            if check.remedy:
                print(f"      {' ' * width}  -> {check.remedy}")
    return 1 if worst(checks) == "fail" else 0


#: What a caller can put wrong, reported rather than raised: a traceback buries the one
#: line that would help. `SessionBusyError`, `AccessError` and `ConfigError` have their
#: own branches in `main`, each with more to say.
REFUSALS = (
    AgentError,
    ArtifactError,
    CapabilityError,
    DecisionError,
    QuotaExceededError,
    SubagentError,
    UnknownSessionError,
    UnsafeReferenceError,
)

#: Verb -> what runs it. Each names only the arguments its own verb defines, so none
#: can read a flag it lacks.
HANDLERS = {
    "run": _run,
    "decide": _decide,
    "seed": lambda args: _seed(args.source, everything=args.everything),
    "doctor": lambda args: _doctor(as_document=args.json, from_dotenv=args.from_dotenv),
    "list": lambda args: _list(
        as_document=args.json, held=with_datazone(args.held, args.datazone)
    ),
    "sessions": lambda args: _sessions(as_document=args.json),
    "artifact": _artifact,
    "reap": _reap,
}


def _load_dotenv() -> frozenset[str]:
    """Load `.env`, and say which names it added.

    The rest were there at start, which a macOS shell can read of this process; only
    this point can tell `doctor` which is which.
    """
    before = set(os.environ)
    load_dotenv(ENV_FILE, override=False)
    return frozenset(set(os.environ) - before)


def main(argv: list[str] | None = None) -> int:
    # Before anything reads the environment. No `.env` is ordinary once installed, and
    # `load_dotenv` returns False rather than raising.
    from_dotenv = _load_dotenv()

    parser = build_parser()
    parser.set_defaults(from_dotenv=from_dotenv)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    if args.command is None:
        parser.print_help()
        return 0

    try:
        # Every verb, not `run` alone: for `list` the warning is its only report of a
        # missing key.
        with _plain_warnings():
            return HANDLERS[args.command](args)
    except SessionBusyError as exc:
        # Not the caller's mistake: waiting fixes it. Still exit 2, so the words say "wait".
        print(f"session busy: {exc}", file=sys.stderr)
        print("nothing to change -- run it again when that turn finishes", file=sys.stderr)
        return 2
    except REFUSALS as exc:
        # Uncaught, one would exit 1, which the help gives a turn that ran and stopped.
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    except AccessError as exc:
        # A traceback for `--as Q` would bury the line naming the source ids defined here.
        print(f"access error: {exc}", file=sys.stderr)
        return 2
    except ConfigError as exc:
        # Anything not caught by now is a bug, and keeps its traceback.
        print(f"configuration error: {exc}", file=sys.stderr)
        # Which `.env` was read: one directory from theirs, a set variable reads as unset.
        where = Path(ENV_FILE).resolve()
        found = "read" if Path(ENV_FILE).is_file() else "not found"
        print(f"configuration comes from the environment and {where} ({found})", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover -- exercised as a subprocess
    raise SystemExit(main())
