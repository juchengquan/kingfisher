"""`kingfisher`, and `python -m kingfisher.cli`."""

from __future__ import annotations

import argparse
import json
import logging
import os
import shlex
import sys
import time
import warnings
from collections.abc import Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dotenv import load_dotenv

import kingfisher
from kingfisher import (
    START,
    UNSCOPED,
    ArtifactError,
    CallerError,
    CapabilityError,
    ConfigError,
    Decision,
    DecisionError,
    Held,
    InputsError,
    MissingCredentialsWarning,
    QuotaExceededError,
    Request,
    ResultError,
    Resume,
    RewindError,
    SessionBusyError,
    SubagentError,
    UnknownSessionError,
    config_from_env,
    definitions_source,
    ensure_definitions,
    inventory,
    paths_from_env,
    seed,
)
from kingfisher.core.access import Datazone, ExtraDatazone, with_datazone

# Private names, taken where they live: the command ships in the library's own wheel.
from kingfisher.core.definitions.agents.spec import AgentError
from kingfisher.core.logged import RUN_LOGGER, logged
from kingfisher.core.session.model import held_by_a_turn

from .health import examine, retired, worst
from .listing import as_json, failed, origins_document, render
from .progress import show

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from typing import TextIO

    from kingfisher import Config, Kingfisher, PendingDecision, RunResult, Session
    from kingfisher.plugins import JsonlRunEvents

#: Read from the working directory only: a bare `load_dotenv()` walks up looking for
#: one, and a command should not pick up a file two directories above.
ENV_FILE = ".env"

def _add_fetching(
    sub: Any, verb: str, summary: str, description: str, what: tuple[str, str]
) -> None:
    """A verb that reads one thing a session holds, by the name a run gave it."""
    fetching = sub.add_parser(
        verb,
        help=summary,
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    fetching.add_argument("--session", metavar="ID", required=True, help="the session")
    fetching.add_argument(what[0], help=what[1])
    fetching.add_argument("--out", metavar="PATH", help="write it here instead")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kingfisher",
        description=(
            "Seed a definitions folder, run a task against it, see what it "
            "offers, and check it over."
        ),
    )
    # Not required, so bare `kingfisher` prints help rather than argparse's usage error.
    sub = parser.add_subparsers(dest="command")
    seeding = sub.add_parser(
        "seed",
        help="copy definitions into the definitions folder",
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
    # definitions folder would be filled with files that cannot run.
    seeding.add_argument(
        "--all",
        dest="everything",
        action="store_true",
        help=(
            "also copy definitions held back by default: the ones naming "
            "middleware, which a deployment that has not registered those names "
            "cannot build, and the ones carrying a lock, which switches on access "
            "control for this deployment"
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
            "  1  stopped at a bound, the answer being what was reached and what\n"
            "     it wrote still there; or at an approval gate, waiting on\n"
            "     `kingfisher decide`\n"
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
            "nothing behind. A turn stopped at a bound or an approval gate keeps "
            "its session, and says so"
        ),
    )
    _add_logging(
        doing,
        "append what the turn did -- each model call with its tokens, each tool call, each "
        "warning -- to FILE as JSON lines, every one whatever --log-level shows. Without it, "
        "nothing is kept",
    )
    doing.add_argument(
        "--tell",
        metavar="TEXT",
        help=(
            "tell every agent and subagent of this turn TEXT -- today's date, who the "
            "work is for. Sent after the cached part of each prompt, and stored nowhere"
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
            "the agent the session paused under. Needed to answer, as every turn names "
            "its agent; any other is refused, with the one it paused under"
        ),
    )
    deciding.add_argument(
        "--delete-session",
        action="store_true",
        help="delete the session once the turn finishes, as `run` does",
    )
    _add_logging(
        deciding, "append what the resumed turn did to FILE as JSON lines, as `run --log` does"
    )
    deciding.add_argument(
        "--tell",
        metavar="TEXT",
        help=(
            "tell every agent and subagent of the resumed turn TEXT, as `run --tell` "
            "does. A pause does not keep it: give it again"
        ),
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
            "shell. Exits non-zero only on something that will break a run, or\n"
            "make it not do what its definitions say.\n"
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
        help="show what the definitions offer a request",
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
    showing = sub.add_parser(
        "prompt",
        help="show what an agent's model is sent",
        description=(
            "Prints what an agent's first model call would be sent: the system\n"
            "message block by block, each named for what wrote it, then every\n"
            "tool with its description.\n"
            "\n"
            "Built as `run` builds it, in the session --session names, and caught\n"
            "before the call leaves: no model is called and the session is not\n"
            "written to. A session not there yet is made to look in, and removed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    showing.add_argument(
        "--agent",
        required=True,
        help="the agent to show (`kingfisher list` shows them)",
    )
    showing.add_argument(
        "--session",
        metavar="ID",
        help="the session to look from, as `run --session` names one; without one, 'default'",
    )
    showing.add_argument(
        "--tell",
        metavar="TEXT",
        help="show where `run --tell TEXT` lands",
    )
    showing.add_argument(
        "--json",
        action="store_true",
        help="emit the same as JSON, every tool's parameters included",
    )
    _add_caller(showing, _TURN_CALLER)
    _add_fetching(
        sub,
        "artifact",
        "fetch one file a turn produced",
        (
            "Fetches a file a turn left under /outputs or /memory, by the name\n"
            "the run printed for it -- `outputs/report.html`, not a path on\n"
            "this machine. Read through the session's backend, so it works\n"
            "wherever that backend keeps the session.\n"
            "\n"
            "Without --out the bytes go to standard output, to pipe or redirect."
        ),
        ("name", "the artifact, as the run named it"),
    )
    _add_fetching(
        sub,
        "result",
        "fetch one tool result's text, by its id",
        (
            "Fetches what one tool call returned, by the id the run's ledger gave\n"
            "it -- `A-0002`. From its file where it has one, else from the\n"
            "session's conversation, which holds a result until it is filed.\n"
            "\n"
            "Without --out the text goes to standard output, to pipe or redirect."
        ),
        ("id", "the result, as the ledger names it"),
    )
    holding = sub.add_parser(
        "sessions",
        help="show the sessions this workspace is holding",
        description=(
            "Every session in the workspace, most recently used first, with how\n"
            "long it has been idle and what it holds on disk.\n"
            "\n"
            "The size is a walk per session -- about a millisecond each -- which\n"
            "a workspace holding thousands will feel.\n"
            "\n"
            "Asked of the sessions KINGFISHER_SESSIONS names, or of\n"
            "<workspace>/sessions where it names none -- so a deployment that\n"
            "keeps its sessions elsewhere sets it for this to see them."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    holding.add_argument(
        "--json",
        action="store_true",
        help="emit the same answer as JSON, for a script rather than a person",
    )
    listing_turns = sub.add_parser(
        "turns",
        help="show the turns a session ran, and which it is at now",
        description=(
            "Every turn the session kept a log of, oldest first, including those a\n"
            "rewind left behind. A * marks the path to where the session is now; a\n"
            "turn's task is what sending it again would regenerate it from."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    listing_turns.add_argument("--session", metavar="ID", required=True, help="the session")
    rewinding = sub.add_parser(
        "rewind",
        help="put a session back where an earlier turn left it",
        description=(
            "The session's conversation, which tool results its next turn is told\n"
            "of, what it heard of its inputs folder, and the files in /outputs and\n"
            "/memory go back to where the turn ended. The turns after it are kept, so\n"
            "rewinding to one of them returns. A file with no copy of that version is\n"
            "left as it is, and the next turn is told. A pause waiting on a decision\n"
            "is discarded."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    rewinding.add_argument("--session", metavar="ID", required=True, help="the session")
    where = rewinding.add_mutually_exclusive_group(required=True)
    where.add_argument("--to", metavar="TURN", help="the turn to go back to the end of")
    # A flag of its own, not a turn named `start`: a caller's own turn ids may be anything.
    where.add_argument(
        "--to-start", action="store_true", help="go back to before the first turn"
    )
    sweeping = sub.add_parser(
        "reap",
        help="delete sessions this workspace is finished with",
        description=(
            "Deletes a session and everything in it -- its files, its\n"
            "conversation, the lock a turn holds -- through the sessions that\n"
            "keep it: those KINGFISHER_SESSIONS names, or <workspace>/sessions.\n"
            "\n"
            "--session removes one by name; --all removes every session no\n"
            "turn is running in. One of the two is required, so the command\n"
            "alone removes nothing.\n"
            "\n"
            "There is no --dry-run. `kingfisher sessions` is the preview: it\n"
            "lists every session and how long it has been idle."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Required: bare, the least-typed invocation would have to guess, and every guess
    # but "nothing" deletes.
    chosen = sweeping.add_mutually_exclusive_group(required=True)
    chosen.add_argument(
        "--session",
        metavar="ID",
        help=(
            "reap this one by name -- whether or not a turn is running in it, "
            "which that turn will not survive"
        ),
    )
    chosen.add_argument(
        "--all",
        action="store_true",
        help="reap every session no turn is running in",
    )
    return parser


_TURN_CALLER = (
    "who is calling: comma-separated source ids, or UNSCOPED to run "
    "with no caller. Required where a definition carries a lock"
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
        help="the datazone the caller is in, as a subagent's or tool's datazone: names it",
    )
    parser.add_argument(
        "--extra-datazones",
        type=_names,
        default=(),
        metavar="NAMES",
        help="further datazones the caller is in, beside --datazone: what any of them "
        "requires is offered, and a session is reopened only from all of them",
    )


#: `--log-level`'s choices, as the level the `kingfisher` logger holds while the command runs.
LOG_LEVELS = {"warning": logging.WARNING, "info": logging.INFO, "off": logging.CRITICAL + 1}
SHOWN_BY_DEFAULT = "warning"


def _add_logging(parser: argparse.ArgumentParser, log_help: str) -> None:
    parser.add_argument("--log", metavar="FILE", type=Path, help=log_help)
    parser.add_argument(
        "--log-level",
        choices=LOG_LEVELS,
        default=SHOWN_BY_DEFAULT,
        help=(
            "which of kingfisher's log lines stderr shows, each led by its session, turn "
            "and subagent: warning (the default) its warnings, info what the turn did as "
            "well, off none. --log keeps everything whatever this says, and a `warning:` "
            "line is printed whatever it says"
        ),
    )


def _seed(source: str | None = None, *, everything: bool = False) -> int:
    """Fill the definitions folder."""
    paths = paths_from_env()
    # Before the source is asked for, which refuses when none is named: a deployment
    # with nothing to seed still gets its folders and `models.yaml.example`.
    ensure_definitions(paths.definitions)

    tree = definitions_source(paths.assets, source)
    written = seed(paths.definitions, tree, everything=everything)
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


#: What leads a line: not `turn_agent`, which `--agent` named.
PREFIXED = ("session_id", "turn_id", "agent")
#: Not repeated among a run event's fields: its name, its prefix or `--agent` says each, and
#: a line printed as it happens needs no `ts`.
UNLISTED = frozenset({"ts", "event", "level", "message", "turn_agent", *PREFIXED})


def _field(value: object) -> str:
    # Bare only where nothing could be misread; JSON's escapes keep the rest to one line.
    if isinstance(value, str) and value and not any(c.isspace() or c in '="' for c in value):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


class _Prefixed(logging.Formatter):
    """A line led by the ids its event carries, `[session turn subagent]`, and a run event
    as its name and its fields, `key=value`, rather than the JSON the default sink writes."""

    def format(self, record: logging.LogRecord) -> str:
        event = getattr(record, "kingfisher", None) or {}
        if record.name == RUN_LOGGER and event:
            said = " ".join([
                str(event.get("event")),
                *(f"{name}={_field(value)}" for name, value in event.items()
                  if name not in UNLISTED),
            ])
        else:
            said = record.getMessage()
        ids = " ".join(str(event[name]) for name in PREFIXED if event.get(name) is not None)
        return f"[{ids}] {said}" if ids else said


@contextmanager
def _prefixed_logs(level: str) -> Iterator[None]:
    """Kingfisher's log lines on stderr, prefixed, at `--log-level`; its loggers alone."""
    logger = logging.getLogger("kingfisher")
    was = logger.level
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_Prefixed())
    # On the logger, not the handler: `logged` asks the logger before it builds a line,
    # and Python's default drops INFO.
    logger.setLevel(LOG_LEVELS[level])
    logger.addHandler(handler)
    try:
        yield
    finally:
        # Put back: `main` runs in-process in the tests, and a handler left on `kingfisher`
        # would say every later line twice.
        logger.removeHandler(handler)
        logger.setLevel(was)


EMPTY_TELL = "--tell is empty -- say what to tell the agents, or leave it out"


def _run(args: argparse.Namespace) -> int:
    """Run one task, and say how it ended in the only channel that is left."""
    if not args.task.strip():
        # Refused here, as `--input` is: the library's refusal is a `ValueError` traceback.
        print("the task is empty -- say what to do", file=sys.stderr)
        return 2
    missing = [p for p in args.input if not Path(p).expanduser().is_file()]
    if missing:
        # Every one in a line: the library refuses as well, but names only the first.
        print(f"no such file: {', '.join(missing)}", file=sys.stderr)
        return 2

    if args.tell is not None and not args.tell.strip():
        # Sent, it would add a blank block to every call; ignored, a flag that did nothing.
        print(EMPTY_TELL, file=sys.stderr)
        return 2

    cfg = config_from_env()
    kf = _configured(cfg, log=args.log, tell=args.tell)
    request = Request(
        task=args.task,
        agent=args.agent,
        session_id=args.session,
        inputs=tuple(Path(p).expanduser() for p in args.input),
    )
    return _drive(
        kf,
        request,
        held=with_datazone(args.held, args.datazone, args.extra_datazones),
        delete_session=args.delete_session,
    )


#: The task `prompt` builds with. It reaches only the user message, which is not printed.
LOOKING = "(kingfisher prompt: nothing is run)"


def _prompt(args: argparse.Namespace) -> int:
    """Print what an agent's first model call would be sent, without making it."""
    if args.tell is not None and not args.tell.strip():
        print(EMPTY_TELL, file=sys.stderr)
        return 2
    from .sent import as_document, render  # noqa: PLC0415

    kf = _configured(config_from_env(), tell=args.tell)
    sent = kf.sent(
        Request(task=LOOKING, agent=args.agent, session_id=args.session),
        source_ids=with_datazone(args.held, args.datazone, args.extra_datazones),
    )
    if args.json:
        print(json.dumps(as_document(sent), indent=2))
    else:
        for line in render(sent):
            print(line)
    return 0


def _configured(cfg: Config, *, log: Path | None = None, tell: str | None = None) -> Kingfisher:
    """The `Kingfisher` a verb builds, with the sessions its settings name, `--tell`'s text as
    its turn text, and `--log`'s run events where it was given."""
    # Here, not at module scope: `Kingfisher` loads deepagents, and `seed`, `list` and
    # `doctor` never build one.
    from kingfisher import Kingfisher  # noqa: PLC0415

    from .configured import (  # noqa: PLC0415
        CommandTurnInstructions,
        configured_sessions,
    )

    return Kingfisher(
        cfg,
        sessions=configured_sessions(),
        turn_instructions=CommandTurnInstructions(tell or ""),
        run_events=_kept_in(log),
    )


def _kept_in(log: Path | None) -> JsonlRunEvents | None:
    """Where `--log` keeps a turn's events, or `None` for the library's default."""
    if log is None:
        return None
    from kingfisher.plugins import JsonlRunEvents  # noqa: PLC0415

    class KeptAndSaid(JsonlRunEvents):
        """The file, and each event on stderr as well where `--log-level` shows it: a
        command's user reads the terminal, and a plugin they wired that broke must still
        say so there."""

        def record(self, event: Mapping[str, object]) -> None:
            # Unfiltered: a deployment may total its tokens from the file.
            super().record(event)
            logged(event)

    return KeptAndSaid(log.expanduser())


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
    # Not when the session goes: `_discard` names them as what goes with it.
    if result.artifacts and not (result.completed and delete_session):
        print("files, by the names `kingfisher artifact` takes:", file=sys.stderr)
        for name in result.artifacts:
            print(f"  {name}", file=sys.stderr)
    if not result.completed:
        # Different words: running again continues a turn cut off at a bound, but
        # discards what a gate stopped to ask.
        if result.pending:
            _awaiting(result, held, asked.agent)
        else:
            # Empty where the model never spoke, and stdout then holds nothing to point at.
            reached = (
                "the answer above is what was reached"
                if result.answer
                else "it stopped before the model answered"
            )
            print(
                f"stopped: {result.stop_reason} -- {reached}, and what it wrote is in "
                f"/outputs and /memory",
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
    # Refused here, as an empty task is: the library's refusal is a `ValueError` traceback.
    if not args.session.strip():
        print("--session is empty -- name the waiting session", file=sys.stderr)
        return 2
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
    # `Resume` refuses this as well, with a traceback.
    called = [decision.call_id for decision in decisions]
    if twice := sorted({call for call in called if called.count(call) > 1}):
        print(f"{', '.join(twice)} answered more than once -- give each call one answer",
              file=sys.stderr)
        return 2

    # Refused, not ignored: with no turn to act on, a silent `--agent` or `--as` reads as
    # the check its help promises having passed.
    unused = [
        flag
        for flag, given in (
            ("--agent", args.agent is not None),
            ("--as", args.held is not None),
            ("--datazone", args.datazone is not None),
            ("--extra-datazones", bool(args.extra_datazones)),
            ("--delete-session", args.delete_session),
            ("--log", args.log is not None),
            ("--tell", args.tell is not None),
        )
        if given and not decisions
    ]
    refused = [
        f"{flag} applies only when answering, with --approve, --reject or --respond"
        for flag in unused
    ]
    if args.tell is not None and not args.tell.strip():
        refused.append(EMPTY_TELL)
    for complaint in refused:
        print(complaint, file=sys.stderr)
    if refused:
        return 2

    cfg = config_from_env()
    if not decisions:
        return _show_pending(_configured(cfg), args.session)
    return _drive(
        _configured(cfg, log=args.log, tell=args.tell),
        Resume(
            session_id=args.session,
            decisions=tuple(decisions),
            agent=args.agent,
        ),
        held=with_datazone(args.held, args.datazone, args.extra_datazones),
        delete_session=args.delete_session,
    )


def _show_pending(kf: Kingfisher, session_id: str) -> int:
    """What this session is waiting on, for somebody who has lost the ids."""
    session = kf.session(session_id)
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
        return f"--approve {shlex.quote(call.call_id)}"
    if "reject" in takes:
        return f"--reject {shlex.quote(call.call_id)}"
    return f"--respond {shlex.quote(f'{call.call_id}=<your answer>')}"


def _caller(held: Held | None) -> str:
    """`held` as the `--as`, `--datazone` and `--extra-datazones` that name it again, with a
    leading space."""
    if held is None:
        return ""
    if not isinstance(held, Sequence):
        return " --as UNSCOPED"
    ids = [one for one in held if not isinstance(one, Datazone)]
    extras = [one for one in held if isinstance(one, ExtraDatazone)]
    said = f" --as {shlex.quote(','.join(ids))}" if ids else ""
    said += "".join(
        f" --datazone {shlex.quote(one)}"
        for one in held
        if isinstance(one, Datazone) and not isinstance(one, ExtraDatazone)
    )
    return said + (f" --extra-datazones {shlex.quote(','.join(extras))}" if extras else "")


def _awaiting(result: RunResult, held: Held | None, agent: str | None) -> None:
    """What a turn that stopped at an approval gate owes the person who ran it.

    Prints the ids, the only way to answer, and says that running again supersedes the
    gate rather than answering it. The line names the caller, or under a policy `decide`
    refuses it as asked by nobody.
    """
    print(
        f"stopped: {result.stop_reason} -- waiting for a decision on "
        f"{len(result.pending)} call(s):",
        file=sys.stderr,
    )
    _list_calls(result.pending)
    # Every call: `decide` refuses an answer that leaves one waiting.
    # The agent too: a resume naming none is refused.
    named = f" --agent {shlex.quote(agent)}" if agent else ""
    print(
        f"answer with: kingfisher decide --session {result.session_id}{_caller(held)}"
        f"{named} " + " ".join(_answer(call) for call in result.pending),
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
    session = kf.session(result.session_id)
    failure = session.delete() if session is not None else None
    if failure:
        print(f"session not deleted -- {failure}", file=sys.stderr)


def _held(raw: str) -> Held:
    """`--as A,B` as the source ids it names, or the explicit absence of any."""
    if raw.strip() == "UNSCOPED":
        return UNSCOPED
    return _names(raw)


def _names(raw: str) -> tuple[str, ...]:
    """`A,B` as the names it lists."""
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _list(*, as_document: bool = False, held: Held | None = None) -> int:
    """Print what the definitions offer."""
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
    """A span in its largest whole unit, floored, so no session reads idler than it is."""
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
    root = kf.origins.sessions.path
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
    """Delete sessions: one by name, or every one no turn is running in."""
    cfg = config_from_env()
    kf = _configured(cfg)
    if args.session is not None:
        return _reap_one(kf, args.session)

    result = kf.reap(now=time.time())
    for gone in result.removed:
        print(f"reaped {gone}")
    for failure in result.failures:
        print(f"not reaped -- {failure}", file=sys.stderr)
    # Said, because a sweep that removes nothing reads as a broken one, and its next user
    # deletes the directory by hand, leaving the conversation and claim behind.
    if not result.removed and not result.failures:
        if result.kept == 0:
            print("nothing to reap -- this workspace holds no sessions")
        else:
            held = "it" if result.kept == 1 else "each of them"
            print(
                f"nothing to reap -- {held} is {held_by_a_turn(cfg.claim_stale_after)}; "
                "reap again then, or reap one by name with --session"
            )
    return 1 if result.failures else 0


def _artifact(args: argparse.Namespace) -> int:
    """Fetch one file a turn produced, to a file or to standard output."""
    content = _opened(_configured(config_from_env()), args.session).artifact(args.name)
    if args.out is None:
        # The buffer, not `print`: a text stream would re-encode a PDF.
        sys.stdout.buffer.write(content)
        sys.stdout.flush()
        return 0
    out = Path(args.out).expanduser()
    # Made, as `--log` makes its own: the bytes are already fetched, and a missing
    # directory would lose them to a traceback.
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(content)
    print(f"wrote {args.name} to {args.out}")
    return 0


def _result(args: argparse.Namespace) -> int:
    """Fetch one tool result's text, to a file or to standard output."""
    text = _opened(_configured(config_from_env()), args.session).result(args.id)
    if args.out is None:
        sys.stdout.write(text)
        sys.stdout.flush()
        return 0
    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"wrote {args.id} to {args.out}")
    return 0


def _opened(kf: Kingfisher, session_id: str) -> Session:
    session = kf.session(session_id)
    if session is None:
        msg = f"no session {session_id!r}"
        raise UnknownSessionError(msg)
    return session


def _turns(args: argparse.Namespace) -> int:
    """List a session's turns, marking the path to where it is now."""
    turns = _opened(_configured(config_from_env()), args.session).turns()
    if not turns:
        print(f"session {args.session} has kept no log of its turns")
        return 0
    for turn in turns:
        kind = "resumed" if turn.resumed else "imported" if turn.imported else ""
        said = turn.task.strip().splitlines()[0][:60] if turn.task and turn.task.strip() else ""
        mark = "*" if turn.current else " "
        print(f"{mark} {turn.id}  after {turn.parent or '-'}  {kind:<8} {said}")
        if turn.files is None:
            print("    files not recorded")
            continue
        for name in turn.files.changed:
            print(f"    changed {name}")
        for name in turn.files.removed:
            print(f"    removed {name}")
        for name, why in turn.files.without_copy.items():
            print(f"    no copy of {name}: {why}")
    return 0


def _rewind(args: argparse.Namespace) -> int:
    """Put a session back where a turn left it, and say what that discarded."""
    session = _opened(_configured(config_from_env()), args.session)
    done = session.rewind(START if args.to_start else args.to)
    where = f"where turn {done.to} ended" if done.to is not None else "at its start"
    print(f"session {args.session} is now {where}")
    for name in done.restored:
        print(f"  put back {name}")
    for name, why in done.not_restored.items():
        print(f"  left {name} as it is: {why}")
    for pending in done.discarded:
        print(f"  discarded the decision {pending.call_id} on {pending.tool}")
    return 0


def _reap_one(kf: Kingfisher, session_id: str) -> int:
    """Reap one session by name, whatever is running in it."""
    # Looked up first: deleting answers `None` for nothing there too, so a mistyped id
    # would report work nothing did.
    failure = _opened(kf, session_id).delete()
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
        for check in retired():
            print(f"{check.verdict}  {check.name}  {check.detail}", file=sys.stderr)
            print(f"      -> {check.remedy}", file=sys.stderr)
        raise
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
#: line that would help. `SessionBusyError` and `ConfigError` have their own branches
#: in `main`, each with more to say.
REFUSALS = (
    AgentError,
    ArtifactError,
    CallerError,
    CapabilityError,
    DecisionError,
    InputsError,
    QuotaExceededError,
    ResultError,
    RewindError,
    SubagentError,
    UnknownSessionError,
)

#: Verb -> what runs it. Each names only the arguments its own verb defines, so none
#: can read a flag it lacks.
HANDLERS = {
    "run": _run,
    "decide": _decide,
    "seed": lambda args: _seed(args.source, everything=args.everything),
    "doctor": lambda args: _doctor(as_document=args.json, from_dotenv=args.from_dotenv),
    "list": lambda args: _list(
        as_document=args.json, held=with_datazone(args.held, args.datazone, args.extra_datazones)
    ),
    "prompt": _prompt,
    "sessions": lambda args: _sessions(as_document=args.json),
    "artifact": _artifact,
    "result": _result,
    "reap": _reap,
    "turns": _turns,
    "rewind": _rewind,
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
    # A level for every verb: each one's lines go through the command's handler, though only
    # `run` and `decide` take `--log-level`.
    parser.set_defaults(from_dotenv=from_dotenv, log_level=SHOWN_BY_DEFAULT)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    if args.command is None:
        parser.print_help()
        return 0

    try:
        # Every verb, not `run` alone: for `list` the warning is its only report of a
        # missing key.
        with _plain_warnings(), _prefixed_logs(args.log_level):
            return HANDLERS[args.command](args)
    except SessionBusyError as exc:
        # Not the caller's mistake: waiting fixes it. Still exit 2, so the words say "wait".
        print(f"session busy: {exc}", file=sys.stderr)
        print("nothing to change -- run it again once the session is free", file=sys.stderr)
        return 2
    except REFUSALS as exc:
        # Uncaught, one would exit 1, which the help gives a turn that ran and stopped.
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
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
