"""`kingfisher`, and `python -m kingfisher.cli`.

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

from kingfisher import (
    ConfigError,
    ensure_layout,
    from_env,
    inventory,
    paths_from_env,
    seed,
)
from kingfisher.cli.listing import as_json, failed, render


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
    sub.add_parser(
        "seed",
        help="copy definitions from installed asset packs into the workspace",
        description=(
            "Copies what every installed pack holds into this deployment's "
            "catalogues. Overwrites, which is how you take an upgrade -- and is "
            "why it has to be asked for. A workspace that has never been used "
            "seeds itself on its first run without this."
        ),
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


def _seed() -> int:
    """Fill the workspace from whatever packs are installed.

    On the paths half of the configuration, not the whole of it: `models.yaml`
    lives *inside* the workspace, so a first run has no catalogue to read and
    requiring one would make this unusable exactly when it is needed.
    """
    paths = paths_from_env()
    # The destination has to exist before anything is copied into it, and this
    # is idempotent -- an already-laid-out workspace is untouched.
    ensure_layout(paths.workspace)

    written = seed(paths)
    for name in written.written:
        print(f"seeded {name}")
    for name in written.overwritten:
        # After the list, not beside each entry: the point is that you edit your
        # copy, so losing one is the line that has to survive being skimmed.
        print(f"warning: overwrote your edited {name}")
    if not written.written:
        print("nothing to seed — no asset pack is installed")
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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    if args.command is None:
        parser.print_help()
        return 0

    try:
        # Before `_list`, and not only for tidiness: `help` has no `--json`, so
        # reaching `args.json` on that path would be an AttributeError.
        if args.command == "help":
            return _help(parser, args.verb)
        if args.command == "seed":
            return _seed()
        return _list(as_document=args.json)
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
