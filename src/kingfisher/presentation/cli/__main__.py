"""`kingfisher`, and `python -m kingfisher.presentation.cli`."""

from __future__ import annotations

import argparse
import json
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
    CapabilityError,
    ConfigError,
    Held,
    QuotaExceededError,
    Request,
    SessionBusyError,
    SkillError,
    SubagentError,
    UnknownSessionError,
    UnsafeReferenceError,
    config_from_env,
    definitions_source,
    ensure_layout,
    inventory,
    paths_from_env,
    seed,
)

# The names here the door does not carry. The command ships in the same
# distribution as the library, so it may take one at the module defining it --
# see *The front door* in `docs/decisions.md` -- and each of these is something
# no caller outside the wheel has asked for: the four kinds a catalogue holds,
# used to say what a directory has none of; where a workspace keeps its
# sessions; what one of them costs; and the warning `doctor` says as a check
# instead. Everything above is public and comes through the front door because
# it is.
from kingfisher.config import MissingCredentialsWarning
from kingfisher.domain.session import sessions_root
from kingfisher.infrastructure.catalogue import DEFINITION_KINDS
from kingfisher.infrastructure.workspace.sessions import session_bytes
from kingfisher.presentation.cli.health import _retired, examine, worst
from kingfisher.presentation.cli.listing import as_json, failed, origins_document, render
from kingfisher.presentation.cli.progress import show

if TYPE_CHECKING:
    from collections.abc import Iterator
    from typing import TextIO

    from kingfisher import Kingfisher, RunResult, Seeded

#: Read from the working directory and nowhere else. A bare `load_dotenv()`
#: walks up looking for one, which is the behaviour this deliberately does not
#: have -- a command should not pick up a file two directories above the one you
#: are standing in.
ENV_FILE = ".env"

#: What `seed` did *not* look at before leaving a definition behind, per kind.
UNCONSULTED = {
    "middlewares": "what this deployment registered",
    "source_ids": "your source_ids.yaml",
}

#: And what to do about it. The half a reader acts on, and the half that would
#: be wrong if one sentence served both: middleware is registered in code, a
#: source id is declared in a file.
REMEDY = {
    "middlewares": "Register the names",
    # No file named here any more. It used to say `groups.yaml.example is
    # beside it`, and that example could not be the one you wanted: it shipped
    # one vocabulary and a workspace needs whichever names its own definitions
    # ask for. Seeding this repository's own set named three source ids and pointed
    # at a file declaring five others, none of them the same. `_declare` below
    # prints what to write instead, using the names that are actually missing.
    "source_ids": "Declare the source ids in source_ids.yaml",
}


def _declare(written: Seeded) -> tuple[str, ...]:
    """The `source_ids.yaml` to write, or nothing when no source id was missing."""
    wanted = sorted(
        {name for left in written.skipped if left.wants == "source_ids" for name in left.names}
    )
    if not wanted:
        return ()
    return (
        "",
        # The artifact rather than the instruction. Each skipped line already
        # says to declare them and to seed again; a third copy of that sentence
        # would be the noise, and what none of those lines can give is the one
        # list that covers all of them.
        "the source_ids.yaml that unblocks every one of them:",
        "",
        f"    source_ids: [{', '.join(wanted)}]",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kingfisher",
        # This said "running a task is the library's job -- see
        # `kingfisher.run`", which was the rule `run` reversed. `d53d85f`
        # corrected the same sentence where the console script is declared and
        # did not reach this one, which is the copy a person actually reads.
        # See *The command line* in `docs/decisions.md`.
        description=(
            "Fill a kingfisher workspace, run a task in it, see what is in it, "
            "and check it over."
        ),
    )
    # `required=False`, so bare `kingfisher` reaches `main` and prints help
    # rather than argparse exiting 2 with a usage line. Someone typing the bare
    # name is asking what this is, and a usage line is a worse answer than help.
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
    # A directory rather than a package. Definitions arrived as installed packs
    # found through an entry point once, then as a set inside the wheel; a path
    # needs no wheel, no metadata and no publish step, which is the whole of
    # what a deployment ever wanted from either.
    seeding.add_argument(
        "--from",
        dest="source",
        metavar="DIR",
        help="seed from this directory instead of the one KINGFISHER_ASSETS names",
    )
    # Off by default, because the default has to be right for a workspace that
    # has registered nothing -- which is every first run. A definition naming
    # middleware is refused when it is built, so copying one in by default
    # would fill a fresh workspace with a file that cannot run.
    seeding.add_argument(
        "--all",
        dest="everything",
        action="store_true",
        help=(
            "also copy definitions that name middleware, which are left behind "
            "by default because a workspace that has not registered those names "
            "cannot build them"
        ),
    )
    doing = sub.add_parser(
        "run",
        help="run one task",
        # Wrapped by hand, like `doctor`: the raw formatter is what keeps the
        # blank lines, and it does no wrapping of its own.
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
    # Required, and refused downstream rather than defaulted anywhere: an agent
    # decides which endpoint the session's prompts go to and whose credentials
    # pay, so a default would put that choice somewhere the command line never
    # mentions. `kingfisher list` shows what this workspace offers.
    doing.add_argument(
        "--agent",
        required=True,
        help="which agent runs this, from the workspace's agents/ (`kingfisher list` shows them)",
    )
    doing.add_argument("--session", metavar="ID", help="continue an existing session")
    # One flag, where there were two. `--input` put a file in the turn's own
    # directory and `--data` in the session's; the turn directory is gone, so
    # every caller-supplied file lives the length of the session and a name sent
    # twice replaces the first, which `place_data` reports.
    doing.add_argument(
        "--data",
        metavar="PATH",
        action="append",
        default=[],
        help="a file for the agent to read, in /data (read-only); repeatable",
    )
    # Unlike `list --as`, an absent one is not the operator's view. A listing is
    # read-only and whoever runs it is on the host with the policy in front of
    # them; a turn acts, so this is left to the library to refuse -- which it
    # does, naming this flag.
    # Off by default, because the default has to be right for somebody who does
    # not know sessions exist yet: a run whose files are gone before they knew
    # to look for them is worse than a directory they can delete later.
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
        "--as",
        dest="held",
        type=_held,
        default=None,
        metavar="SOURCE_IDS",
        help=(
            "who is calling: comma-separated source ids, or UNSCOPED to run "
            "with no caller. Required where the workspace declares source ids"
        ),
    )
    checkup = sub.add_parser(
        "doctor",
        help="check everything that stands between this install and a run",
        # Wrapped by hand, because the raw formatter is what keeps the blank
        # line below and it does no wrapping of its own. The paragraph break is
        # worth the trade: the second half is a limit, and a limit buried in a
        # justified block is one nobody reaches.
        description=(
            "The configuration, the credentials it names, the three catalogues,\n"
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
            "one came from. Exits non-zero if a catalogue will not load."
        ),
    )
    # A flag rather than the default. A listing whose default output is JSON is
    # a listing nobody reads, and whoever wants one already knows to ask.
    listing.add_argument(
        "--json",
        action="store_true",
        help="emit the same answer as JSON, for a script rather than a person",
    )
    # Unscoped is the operator's view: everything, plus who reaches it. Naming
    # source ids simulates a caller, which is how a policy gets checked before
    # somebody trusts it.
    listing.add_argument(
        "--as",
        dest="held",
        type=_held,
        default=None,
        metavar="SOURCE_IDS",
        help=(
            "show what these source ids reach: comma-separated names, or UNSCOPED "
            "for the operator's view of everything"
        ),
    )
    holding = sub.add_parser(
        "sessions",
        help="show the sessions this workspace is holding",
        # Wrapped by hand, like `run` and `doctor`: the raw formatter keeps the
        # blank lines and does no wrapping of its own.
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
            "A deployment that moved its sessions with the SessionRoot port sees\n"
            "nothing here: this reads <workspace>/sessions, which such a\n"
            "deployment never uses."
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
    # One or the other. An age and a name are two different questions, and a
    # command handed both would have to decide which one it had been asked.
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


def _seed(source: str | None = None, *, everything: bool = False) -> int:
    """Fill the workspace with definitions."""
    paths = paths_from_env()
    # The destination has to exist before anything is copied into it, and this is
    # idempotent -- an already-laid-out workspace is untouched.
    ensure_layout(paths.workspace, authored=paths.authored_files)

    tree = definitions_source(paths, source)
    written = seed(paths, tree, everything=everything)
    for name in written.written:
        print(f"seeded {name}")
    for left in written.skipped:
        # Named with what to do about it, because "skipped" on its own reads as
        # a failure and this is a choice. The names are the actionable half, and
        # `wants` is what makes them actionable: middleware is registered in
        # code and a source id is declared in `source_ids.yaml`, so one sentence for
        # both would send half its readers to the wrong file.
        print(
            f"skipped {left.label} — names {left.wants} "
            f"({', '.join(left.names)}), and seed does not check "
            f"{UNCONSULTED[left.wants]}. "
            f"{REMEDY[left.wants]}, then seed again with --all"
        )
    for line in _declare(written):
        print(line)
    for name in written.overwritten:
        # After the list, not beside each entry: the point is that you edit your
        # copy, so losing one is the line that has to survive being skimmed.
        print(f"warning: overwrote your edited {name}")

    # Non-zero, and this changed with the definitions leaving the wheel. It was nearly
    # unreachable before -- the shipped set always held all four kinds -- and is now one
    # of the likelier mistakes: `--from ./assets_examples/skills` names a directory that
    # exists, is readable, and holds none of them.
    if not written.written:
        if written.skipped:
            # A different failure from an empty directory, and the remedy is
            # the opposite: everything here was found and understood, and left
            # behind on purpose. Saying "holds none of agents, skills..." would
            # send a reader looking one directory up for files that are right
            # where they thought.
            wants = written.skipped[0].wants
            print(
                f"nothing seeded — every definition in {tree} names {wants}, "
                f"and seed does not check {UNCONSULTED[wants]}. "
                f"{REMEDY[wants]}, then seed again with --all"
            )
            return 1
        print(f"nothing to seed — {tree} holds none of {', '.join(DEFINITION_KINDS)}")
        return 1
    return 0


#: Where kingfisher's own code is, which is what decides who a warning is for.
_OURS = Path(kingfisher.__file__).resolve().parent


@contextmanager
def _plain_warnings() -> Iterator[None]:
    """Kingfisher's own warnings as one `warning:` line each, on stderr.

    Only its own. They are written for whoever runs the command and name the fix, so
    a path into the installed package and the source of the `warnings.warn` call are
    noise around the one sentence meant for them. A dependency's warning is about
    code they did not write, and its file and line are the only pointer to it.
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

        # Assigning is the hook the standard library documents. ty types the attribute
        # as the stdlib's own function, which no replacement can be, signature and all.
        warnings.showwarning = plain  # ty: ignore[invalid-assignment]
        yield


def _run(args: argparse.Namespace) -> int:
    """Run one task, and say how it ended in the only channel that is left."""
    missing = [p for p in args.data if not Path(p).expanduser().is_file()]
    if missing:
        # Before the model, because this is the one mistake that would otherwise
        # cost money to discover.
        print(f"no such file: {', '.join(missing)}", file=sys.stderr)
        return 2

    # Imported here, not at module scope. `Kingfisher` pulls deepagents and
    # three provider SDKs -- about a second -- and
    # `test_reaching_the_cli_stays_free_of_provider_sdks` holds every other verb
    # to not paying it. `seed`, `list` and `doctor` do not build one.
    from kingfisher import Kingfisher, default_backend  # noqa: PLC0415

    kf = Kingfisher(config_from_env(), backend=default_backend)
    request = Request(
        task=args.task,
        agent=args.agent,
        session_id=args.session,
        data=tuple(Path(p).expanduser() for p in args.data),
    )
    result = show(kf.stream(request, source_ids=args.held), sys.stdout, sys.stderr)
    if result is None:
        # The stream ended without a terminal event, which is not a shape the
        # library produces -- said out loud rather than reported as success.
        print("the run ended without a result", file=sys.stderr)
        return 2

    print(f"\nsession {result.session_id}  turn {result.turn_id}", file=sys.stderr)
    if not result.completed:
        print(
            f"stopped: {result.stop_reason} -- the answer above is what was "
            f"reached, and what it wrote is in /derived and /memory",
            file=sys.stderr,
        )
        if args.delete_session:
            # Said rather than done quietly, because the flag was asked for and
            # this is the one ending that declines it. Both ways out are named:
            # the work is still there to pick up, and still there to remove.
            print(
                f"session kept: continue it with --session {result.session_id}, "
                f"or remove it with kingfisher reap --session {result.session_id}",
                file=sys.stderr,
            )
        return 1
    if args.delete_session:
        _discard(kf, result)
    return 0


def _discard(kf: Kingfisher, result: RunResult) -> None:
    """Delete the session this run used, having named what goes with it.

    The files are listed before the deletion and not after, because this is the
    moment they stop being recoverable -- and nothing else in this command ever
    prints them, so without this a run that wrote a file and a run that wrote
    nothing end identically.

    A deletion that fails does not change the exit code. Those three codes say
    how the *turn* ended, which is what a script reading them is asking, and 1
    already means the answer above was cut short -- which would be a lie told
    about a turn that finished and a directory that stayed.
    """
    if result.artifacts:
        many = "" if len(result.artifacts) == 1 else "s"
        print(
            f"the session goes, and {len(result.artifacts)} file{many} with it:",
            file=sys.stderr,
        )
        for name in result.artifacts:
            print(f"  {name}", file=sys.stderr)
    failure = kf.delete_session(result.session_id)
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

    Refused rather than read as seconds, which is what `KINGFISHER_SESSION_TTL_S`
    holds and would have been the obvious reading. Somebody who means a week
    types `--older-than 7`, and seven *seconds* sweeps every session no turn is
    running in: the plausible misreading is the destructive one, so there is no
    reading at all. `0` is exempt because zero is the same number in every unit.
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
    # `UNSCOPED` and an absent flag are the same answer here, and that is not
    # the inconsistency it looks like. A listing is read-only and whoever runs
    # it is on the host with the policy file in front of them, so it is exempt
    # from the refusal that covers a *turn*: there is nothing to protect by
    # making an operator name themselves to read their own workspace.
    source_ids = held if isinstance(held, tuple) else None
    found = inventory(cfg, source_ids=source_ids)
    if as_document:
        print(json.dumps(as_json(found), indent=2, sort_keys=True))
    else:
        for line in render(found):
            print(line)
    return 1 if failed(found) else 0


def _age(seconds: float) -> str:
    """A span in the units `--older-than` takes, so a listing reads into a flag."""
    for suffix, size in (("d", 24 * 3600), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds / size:.0f}{suffix}"
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
    from kingfisher import Kingfisher, default_backend  # noqa: PLC0415

    kf = Kingfisher(config_from_env(), backend=default_backend)
    root = sessions_root(kf.workspace)
    now = time.time()
    # A walk per session, at ~0.8ms each. The same trade `sessions()` already
    # makes for the listing: cheap where it is read, and a workspace large
    # enough to mind wants an index rather than a cheaper column.
    held = [(info, session_bytes(root / info.id)) for info in kf.sessions()]

    if as_document:
        print(
            json.dumps(
                {
                    # Named here as well as in the block below, because the two
                    # forms have to say the same thing -- the rule `doctor` puts
                    # its origins in both for.
                    "root": str(root),
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
        print(f"no sessions in {root}")
        return 0

    print(f"{_count(len(held))}, {_size(sum(size for _, size in held))}, in {root}")
    print()
    width = max(len(info.id) for info, _ in held)
    for info, size in held:
        idle = _age(now - info.last_used)
        print(f"  {info.id.ljust(width)}  {idle.rjust(5)} idle  {_size(size).rjust(9)}")
    return 0


def _reap(args: argparse.Namespace) -> int:
    """Delete sessions: one by name, or every one that has been idle too long."""
    from kingfisher import Kingfisher, default_backend  # noqa: PLC0415

    kf = Kingfisher(config_from_env(), backend=default_backend)
    if args.session is not None:
        return _reap_one(kf, args.session)

    # Read once and used twice -- to sweep, and to report what swept -- so the
    # number that decided cannot differ from the number printed.
    age = kf.cfg.session_ttl_s if args.older_than is None else args.older_than
    result = kf.reap(older_than_seconds=age, now=time.time())

    for gone in result.removed:
        print(f"reaped {gone}")
    if result.orphans:
        # Not sessions this sweep ended: conversations left behind by sessions
        # that went some other way, which nothing but a sweep ever looks for.
        print(f"and {len(result.orphans)} conversations no session owned any more")
    for failure in result.failures:
        print(f"not reaped -- {failure}", file=sys.stderr)

    if not result.removed and not result.failures:
        _nothing_reaped(result.kept, age, from_config=args.older_than is None)
    return 1 if result.failures else 0


def _nothing_reaped(kept: int, age: float, *, from_config: bool) -> None:
    """Say what decided, because a sweep that removes nothing reads as a broken one.

    The ordinary case on a workspace in daily use is that nothing has expired,
    and a command that prints nothing at all there is one whose next user
    deletes the directory by hand -- which leaves the conversation, the claim
    and whatever a store kept exactly where they were.
    """
    if kept == 0:
        print("nothing to reap -- this workspace holds no sessions")
        return
    why = f"none of the {_count(kept)} here has been idle longer than {_age(age)}"
    if not from_config:
        print(f"nothing to reap -- {why}")
        return
    print(f"nothing to reap -- {why}, which is KINGFISHER_SESSION_TTL_S")
    print("--older-than sweeps on a shorter age: kingfisher reap --older-than 1d")


def _reap_one(kf: Kingfisher, session_id: str) -> int:
    """Reap one session by name, whatever its age and whatever is running in it."""
    # `UNSCOPED` because this is housekeeping on the machine rather than a call on
    # anyone's behalf, and a policied deployment now refuses a read that names nobody.
    # `reap` and `sessions` are the operator's, which is why neither takes `--as`.
    if kf.session(session_id, source_ids=UNSCOPED) is None:
        # Asked before deleting, because `delete_session` answers `None` both
        # for "removed it" and for "there was no such session" -- so without
        # this a mistyped id reports success for work nothing did.
        msg = f"no session {session_id!r}"
        raise UnknownSessionError(msg)
    failure = kf.delete_session(session_id)
    if failure:
        print(f"not reaped -- {failure}", file=sys.stderr)
        return 1
    print(f"reaped {session_id}")
    return 0


def _doctor(*, as_document: bool = False) -> int:
    """Say what would stop a run, and what would merely surprise."""
    try:
        # Silenced rather than shown: the `credentials` check says the same thing, and
        # says it inside the report instead of above it.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", MissingCredentialsWarning)
            cfg = config_from_env()
    except ConfigError:
        # Said before the error rather than instead of it. `examine` reports a
        # retired setting, and a `Config` has to exist before it can -- so the
        # run that most needs to hear it is the one that cannot: a deployment
        # from when `KINGFISHER_MODEL` and `KINGFISHER_API_STYLE` chose the model
        # has no `models.yaml`, because the catalogue is what replaced them. It
        # gets told the file is missing, and without this, nothing connects that
        # to the three variables it is still setting.
        #
        # `stderr`, so `--json` still emits a document or nothing at all.
        for check in _retired():
            print(f"{check.verdict}  {check.name}  {check.detail}", file=sys.stderr)
            print(f"      -> {check.remedy}", file=sys.stderr)
        raise
    # Built here and handed on, rather than each of the two asking for its own.
    # The header and the checks are then reading one object -- and `examine`
    # building its own is what made "your configuration is being ignored"
    # unsayable, since a catalogue resolved from `cfg` agrees with `cfg` by
    # construction.
    found = inventory(cfg)
    checks = examine(cfg, found)
    origins = found.origins
    if as_document:
        # An object where this was a bare list of checks. The two forms of this
        # command have to say the same thing, and the human one now opens with
        # where everything was read from -- a JSON form that omitted it would
        # be the disagreement between surfaces that record exists to end.
        print(
            json.dumps(
                {"origins": origins_document(origins), "checks": [vars(c) for c in checks]},
                indent=2,
            )
        )
    else:
        # Before the checks, because it is what they are about. `doctor` could
        # report twelve tools and never say which directory they came from,
        # which is a strange thing for a diagnostic not to be able to answer.
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


#: What a caller can put wrong, as against what a deployment can. Every one is
#: reported rather than raised: a traceback out of langgraph in front of somebody
#: who mistyped an agent name buries the one line that would have helped.
#:
#: `SessionBusyError` is handled before these and not among them -- see `main`.
#: `AccessError` and `ConfigError` keep their own branches too, because each has
#: something extra to say.
REFUSALS = (
    CapabilityError,
    QuotaExceededError,
    SkillError,
    SubagentError,
    UnknownSessionError,
    UnsafeReferenceError,
)

#: Verb -> what runs it. A table rather than a chain of `if`s. The chain needed
#: one branch per verb *in the right order*, because not every verb takes
#: `--json` and the fallthrough read `args.json` -- so a verb without it reaching
#: that line was an `AttributeError` waiting on somebody reordering two blocks
#: that looked interchangeable. Here each verb names the arguments it has, and
#: the order of this table means nothing.
HANDLERS = {
    "run": _run,
    "seed": lambda args: _seed(args.source, everything=args.everything),
    "doctor": lambda args: _doctor(as_document=args.json),
    "list": lambda args: _list(as_document=args.json, held=args.held),
    "sessions": lambda args: _sessions(as_document=args.json),
    "reap": _reap,
}


def main(argv: list[str] | None = None) -> int:
    # Before anything reads the environment, and it must not become a reason to
    # depend on being in a checkout: absent is the ordinary case for an
    # installed kingfisher, and `load_dotenv` returns False rather than raising.
    load_dotenv(ENV_FILE, override=False)

    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    if args.command is None:
        parser.print_help()
        return 0

    try:
        # Around every verb rather than `run` alone: each one that reads a configuration
        # can be warned while it does, and for `list` that warning is the only report of
        # a missing key it gives.
        with _plain_warnings():
            return HANDLERS[args.command](args)
    except SessionBusyError as exc:
        # Its own branch, and the only one of these that is not the caller's
        # mistake: another turn holds the session and waiting fixes it. The code
        # is still 2 -- it does not earn one of its own -- so the line has to be
        # what says "wait" rather than "edit something".
        print(f"session busy: {exc}", file=sys.stderr)
        print("nothing to change -- run it again when that turn finishes", file=sys.stderr)
        return 2
    except REFUSALS as exc:
        # Nine errors that reached a stranger as a traceback until `run` existed,
        # because nothing but `run` could raise them from a command. Every one is the
        # same shape as `ConfigError` below: something the person at the terminal wrote
        # and can fix -- an agent they cannot reach, a session id that is not there, a
        # file reference that does not resolve.
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    except AccessError as exc:
        # Beside `ConfigError` because it is the same kind of thing: something
        # the person at the terminal wrote and can fix, in a file or on the
        # command line. A traceback for `--as Q` would bury the one line that
        # says which source ids this deployment actually defines.
        print(f"access error: {exc}", file=sys.stderr)
        return 2
    except ConfigError as exc:
        # The one error a caller causes and can fix, so it is reported rather
        # than raised. Anything else is a bug and should keep its traceback.
        print(f"configuration error: {exc}", file=sys.stderr)
        # Where the answer would have come from, said only when it is somewhere
        # the reader is not. This used to say `.env` is never read -- true then,
        # and the reason this command failed while the driver worked with the key
        # three lines away in a file. Now the useful thing to say is *which*
        # file was read, because a caller standing one directory from theirs
        # gets a message about a variable that is set, just not here.
        where = Path(ENV_FILE).resolve()
        found = "read" if Path(ENV_FILE).is_file() else "not found"
        print(f"configuration comes from the environment and {where} ({found})", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover -- exercised as a subprocess
    raise SystemExit(main())
