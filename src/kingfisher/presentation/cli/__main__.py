"""`kingfisher`, and `python -m kingfisher.presentation.cli`.

Nothing here decides anything. It reads the configuration the library reads,
calls the library, and prints what came back -- so running a command and calling
the function give the same answer, and there is no second way to configure one.

Subcommands rather than flags, and bare `kingfisher` prints help. A shipped
command needs a safe do-nothing default: `main.py`'s bare invocation spends
money, which is right for a driver you type daily and wrong for a stranger's
first contact. Flags would force a default to be invented, and there is no good
one.

**The environment only, no `.env`** -- which is where this differs from
`main.py`, deliberately, and the difference is visible: run both `--list`s in a
checkout and they disagree about `KINGFISHER_SKILLS`. `load_dotenv()` with no
argument searches upward from the *calling file*, which for an installed package
is `site-packages`, so what it finds is either nothing or something nobody meant.
`main.py` is a driver in a checkout and that search is exactly right for it.
`kingfisher-server` reads the environment and so does this.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from kingfisher import (
    ConfigError,
    ensure_layout,
    from_env,
    inventory,
    paths_from_env,
    seed,
)
from kingfisher.presentation.cli.health import examine, worst
from kingfisher.presentation.cli.listing import as_json, failed, render


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kingfisher",
        description=(
            "Fill a kingfisher workspace, and see what is in it. "
            "Running a task is the library's job -- see `kingfisher.run`."
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
            "Copies definitions into this deployment's catalogues -- the ones "
            "that ship with kingfisher, or a directory of your own. Overwrites, "
            "which is how you take an upgrade and is why it has to be asked for. "
            "A workspace that has never been used seeds itself on its first run "
            "without this."
        ),
    )
    # A directory rather than a package. Definitions used to arrive as installed
    # packs found through an entry point; a path needs no wheel, no metadata and
    # no publish step, which is the whole of what a deployment wanted from that.
    seeding.add_argument(
        "--from",
        dest="source",
        metavar="DIR",
        help="seed from this directory instead of the definitions that ship with kingfisher",
    )
    explain = sub.add_parser(
        "help",
        help="show this, or what one verb does",
        description=(
            "The same text `--help` prints. It exists because a reader looking "
            "for it types the word, and finding it listed beside the verbs it "
            "describes costs less than knowing that a bare invocation would "
            "have done."
        ),
    )
    explain.add_argument(
        "verb",
        nargs="?",
        help="a verb to explain; omit for the whole command",
    )
    sub.add_parser(
        "serve",
        help="run the HTTP surface (ships separately)",
        description=(
            "The same thing `kingfisher-service` starts, reading the same "
            "environment, with one implementation behind both names. The "
            "service is its own distribution: `pip install "
            "'kingfisher[service]'`. This verb says so when it is missing "
            "rather than being absent from this list."
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
    return parser


def _seed(source: str | None = None) -> int:
    """Fill the workspace with definitions.

    On the paths half of the configuration, not the whole of it: `models.yaml`
    lives *inside* the workspace, so a first run has no catalogue to read and
    requiring one would make this unusable exactly when it is needed.
    """
    paths = paths_from_env()
    # The destination has to exist before anything is copied into it, and this
    # is idempotent -- an already-laid-out workspace is untouched.
    ensure_layout(paths.workspace)

    written = seed(paths, Path(source) if source else None)
    for name in written.written:
        print(f"seeded {name}")
    for name in written.overwritten:
        # After the list, not beside each entry: the point is that you edit your
        # copy, so losing one is the line that has to survive being skimmed.
        print(f"warning: overwrote your edited {name}")
    if not written.written:
        print("nothing to seed — the directory holds no definitions")
    return 0


def _list(*, as_document: bool = False) -> int:
    """Print what the workspace offers.

    The whole configuration here, unlike `seed`: answering means building an
    agent, and an agent needs to know which model it would run on.

    The exit code does not depend on the format. A broken catalogue is still one
    when a script is reading, and the reason is in the document as well -- so a
    caller can find out either way round rather than having to pick.
    """
    cfg = from_env()
    found = inventory(cfg)
    if as_document:
        print(json.dumps(as_json(found), indent=2, sort_keys=True))
    else:
        for line in render(found, workspace=cfg.workspace):
            print(line)
    return 1 if failed(found) else 0


def _verbs(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    """Every subcommand, from the parser rather than from a list beside it.

    A second list of verb names is one that goes stale the first time somebody
    adds a verb and does not think about `help` -- and `help` is precisely the
    thing nobody thinks about.
    """
    return {
        name: subparser
        for action in parser._actions
        for name, subparser in (getattr(action, "choices", None) or {}).items()
    }


def _help(parser: argparse.ArgumentParser, verb: str | None) -> int:
    """Print the whole thing, or one verb's part of it.

    An unknown verb is the one case this does better than `--help`: argparse
    would refuse it with a usage line, and this says which words exist.
    """
    if verb is None:
        parser.print_help()
        return 0

    verbs = _verbs(parser)
    if verb not in verbs:
        known = ", ".join(sorted(verbs))
        print(f"no such command: {verb}. kingfisher knows {known}", file=sys.stderr)
        return 2

    verbs[verb].print_help()
    return 0


def _serve() -> int:
    """Hand off to the server's own entry point, which decides everything.

    Imported here rather than at module scope, and that is not a style choice.
    The service is a separate distribution now, so on a base install the module
    is not there at all -- importing it at the top would make `kingfisher list`
    fail over a verb nobody asked for.

    This is also the only place that says how to get it. The service ships a
    `kingfisher-service` command, and the base deliberately does not declare one
    of the same name: two distributions owning one script is not an override but
    a shared file, and reinstalling the base would silently swap a working server
    for a note telling you to install what you already have.
    """
    try:
        from kingfisher_service.__main__ import main as serve_forever  # noqa: PLC0415
    except ImportError:
        print(
            "kingfisher serve needs the service: pip install 'kingfisher[service]'",
            file=sys.stderr,
        )
        return 1
    return serve_forever()


def _doctor(*, as_document: bool = False) -> int:
    """Say what would stop a run, and what would merely surprise.

    Non-zero on a failure and zero on a warning. An unconfined shell is a
    deployment's choice, and a command that failed on one would go unrun in
    exactly the deployments most worth checking.
    """
    checks = examine(from_env())
    if as_document:
        print(json.dumps([vars(check) for check in checks], indent=2))
    else:
        width = max(len(check.name) for check in checks)
        for check in checks:
            mark = {"ok": "ok  ", "warn": "warn", "fail": "FAIL"}[check.verdict]
            print(f"{mark}  {check.name.ljust(width)}  {check.detail}")
            if check.remedy:
                print(f"      {' ' * width}  -> {check.remedy}")
    return 1 if worst(checks) == "fail" else 0


#: Verb -> what runs it. A table rather than a chain of `if`s, which four verbs
#: made worth it twice over. The chain needed one branch per verb *in the right
#: order*, because only two of them take `--json` and the fallthrough read
#: `args.json` -- so `serve` reaching that line was an `AttributeError` waiting
#: on somebody reordering two blocks that looked interchangeable. Here each verb
#: names the arguments it has, and the order of this table means nothing.
HANDLERS = {
    "seed": lambda args, parser: _seed(args.source),  # noqa: ARG005
    "serve": lambda args, parser: _serve(),  # noqa: ARG005
    "doctor": lambda args, parser: _doctor(as_document=args.json),  # noqa: ARG005
    "list": lambda args, parser: _list(as_document=args.json),  # noqa: ARG005
    "help": lambda args, parser: _help(parser, args.verb),
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    if args.command is None:
        parser.print_help()
        return 0

    try:
        return HANDLERS[args.command](args, parser)
    except ConfigError as exc:
        # The one error a caller causes and can fix, so it is reported rather
        # than raised. Anything else is a bug and should keep its traceback.
        print(f"configuration error: {exc}", file=sys.stderr)
        # Said because the other driver reads a `.env` and this one does not:
        # somebody who has one and is being told a variable is unset would
        # otherwise go looking in the file rather than at their shell.
        print("kingfisher reads the environment; `.env` is not loaded", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover -- exercised as a subprocess
    raise SystemExit(main())
