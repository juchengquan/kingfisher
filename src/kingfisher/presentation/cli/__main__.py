"""`kingfisher`, and `python -m kingfisher.presentation.cli`.

Nothing here decides anything. It reads the configuration the library reads,
calls the library, and prints what came back -- so running a command and calling
the function give the same answer, and there is no second way to configure one.

Subcommands rather than flags, and bare `kingfisher` prints help. A shipped
command needs a safe do-nothing default: `main.py`'s bare invocation spends
money, which is right for a driver you type daily and wrong for a stranger's
first contact. Flags would force a default to be invented, and there is no good
one.

**`./.env` if there is one, and nowhere else.** This read the environment
alone at first, on the grounds that `load_dotenv()` with no argument searches
*upward from the calling file* -- which for an installed package starts in
`site-packages` and finds either nothing or something nobody meant.

That objection is sound and it is about the search, not about the file. The two
were run together and the conclusion was too broad: a checkout keeps its keys in
`.env`, so `kingfisher list` failed on a deployment where `main.py --list`
worked, with the key sitting in a file three lines away. Naming the path takes
the search away and leaves the file, which is what was wanted.

Relative on purpose. `load_dotenv(".env")` resolves against the working
directory and stops there, so it finds the one beside you or nothing -- never
one belonging to a parent, and never one under `site-packages`. Values already
in the environment win, which is what `override=False` is for: an explicit
`KINGFISHER_WORKSPACE=... kingfisher list` must not be quietly replaced by a
file the caller may not have known was there.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from kingfisher import (
    DEFINITION_KINDS,
    UNSCOPED,
    AccessError,
    ConfigError,
    Held,
    config_from_env,
    definitions_source,
    ensure_layout,
    inventory,
    paths_from_env,
    seed,
)
from kingfisher.presentation.cli.health import examine, worst
from kingfisher.presentation.cli.listing import as_json, failed, origins_document, render

#: Read from the working directory and nowhere else. A bare `load_dotenv()`
#: walks up looking for one, which is the behaviour this deliberately does not
#: have -- a command should not pick up a file two directories above the one you
#: are standing in.
ENV_FILE = ".env"

#: What a workspace cannot do with a name it has not been given, per kind. Two
#: verbs rather than one, because "cannot build" is exact for middleware and
#: wrong for a group -- a definition naming an undeclared group does not fail to
#: build, it stops the catalogue being read at all.
CANNOT = {
    "middleware": "cannot build",
    "groups": "does not declare",
}

#: And what to do about it. The half a reader acts on, and the half that would
#: be wrong if one sentence served both: middleware is registered in code, a
#: group is declared in a file.
REMEDY = {
    "middleware": "Register the names",
    # Names the example as well as the file, because the file does not exist
    # yet -- that is why the definition was skipped. `ensure_layout` puts the
    # example beside it, so this is a copy rather than a search.
    "groups": "Declare the groups in groups.yaml (groups.yaml.example is beside it)",
}


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
    # Unscoped is the operator's view: everything, plus who reaches it. Naming
    # groups simulates a caller, which is how a policy gets checked before
    # somebody trusts it.
    listing.add_argument(
        "--as",
        dest="held",
        type=_held,
        default=None,
        metavar="GROUPS",
        help=(
            "show what these groups reach: comma-separated names, or UNSCOPED "
            "for the operator's view of everything"
        ),
    )
    return parser


def _seed(source: str | None = None, *, everything: bool = False) -> int:
    """Fill the workspace with definitions.

    On the paths half of the configuration, not the whole of it: `models.yaml`
    lives *inside* the workspace, so a first run has no catalogue to read and
    requiring one would make this unusable exactly when it is needed.
    """
    paths = paths_from_env()
    # The destination has to exist before anything is copied into it, and this
    # is idempotent -- an already-laid-out workspace is untouched.
    #
    # Before the source is resolved, deliberately. Laying out a workspace writes
    # `models.yaml.example`, and that has to happen even when there is nothing
    # to seed: a deployment told to write `models.yaml` and given no example of
    # one is the dead end this ordering exists to avoid.
    ensure_layout(paths.workspace)

    tree = definitions_source(paths, source)
    written = seed(paths, tree, everything=everything)
    for name in written.written:
        print(f"seeded {name}")
    for left in written.skipped:
        # Named with what to do about it, because "skipped" on its own reads as
        # a failure and this is a choice. The names are the actionable half, and
        # `wants` is what makes them actionable: middleware is registered in
        # code and a group is declared in `groups.yaml`, so one sentence for
        # both would send half its readers to the wrong file.
        print(
            f"skipped {left.label} — names {left.wants} "
            f"({', '.join(left.names)}) that this workspace {CANNOT[left.wants]}. "
            f"{REMEDY[left.wants]}, then seed again with --all"
        )
    for name in written.overwritten:
        # After the list, not beside each entry: the point is that you edit your
        # copy, so losing one is the line that has to survive being skimmed.
        print(f"warning: overwrote your edited {name}")

    # Non-zero, and this changed with the definitions leaving the wheel. It was
    # nearly unreachable before -- the shipped set always held all four kinds --
    # and is now one of the likelier mistakes: `--from ./examples/skills` names
    # a directory that exists, is readable, and holds none of them.
    #
    # `doctor` only warns about the same state, and that is not an
    # inconsistency. It reports on a deployment, which runs fine on a workspace
    # seeded months ago. This is an action, and the action did not happen.
    #
    # The four kinds are named because the mistake is almost always one
    # directory level in the wrong direction, and "holds no definitions" leaves
    # a reader guessing which direction.
    if not written.written:
        if written.skipped:
            # A different failure from an empty directory, and the remedy is
            # the opposite: everything here was found and understood, and left
            # behind on purpose. Saying "holds none of agents, skills..." would
            # send a reader looking one directory up for files that are right
            # where they thought.
            wants = written.skipped[0].wants
            print(
                f"nothing seeded — every definition in {tree} names {wants} "
                f"this workspace {CANNOT[wants]}. {REMEDY[wants]}, then seed "
                f"again with --all"
            )
            return 1
        print(f"nothing to seed — {tree} holds none of {', '.join(DEFINITION_KINDS)}")
        return 1
    return 0


def _held(raw: str) -> Held:
    """`--as A,B` as the groups it names, or the explicit absence of any.

    `UNSCOPED` is spelled out rather than being what an empty value means: an
    empty `--as` is far more likely to be a shell variable that did not expand
    than a considered decision to run with no caller at all, and the two must
    not look the same at the one place somebody says who they are.
    """
    if raw.strip() == "UNSCOPED":
        return UNSCOPED
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _list(*, as_document: bool = False, held: Held | None = None) -> int:
    """Print what the workspace offers.

    The whole configuration here, unlike `seed`: answering means building an
    agent, and an agent needs to know which model it would run on.

    The exit code does not depend on the format. A broken catalogue is still one
    when a script is reading, and the reason is in the document as well -- so a
    caller can find out either way round rather than having to pick.
    """
    cfg = config_from_env()
    # `UNSCOPED` and an absent flag are the same answer here, and that is not
    # the inconsistency it looks like. A listing is read-only and whoever runs
    # it is on the host with the policy file in front of them, so it is exempt
    # from the refusal that covers a *turn*: there is nothing to protect by
    # making an operator name themselves to read their own workspace.
    groups = held if isinstance(held, tuple) else None
    found = inventory(cfg, groups=groups)
    if as_document:
        print(json.dumps(as_json(found), indent=2, sort_keys=True))
    else:
        for line in render(found):
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
    cfg = config_from_env()
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


#: Verb -> what runs it. A table rather than a chain of `if`s, which four verbs
#: made worth it twice over. The chain needed one branch per verb *in the right
#: order*, because only two of them take `--json` and the fallthrough read
#: `args.json` -- so `serve` reaching that line was an `AttributeError` waiting
#: on somebody reordering two blocks that looked interchangeable. Here each verb
#: names the arguments it has, and the order of this table means nothing.
HANDLERS = {
    "seed": lambda args, parser: _seed(args.source, everything=args.everything),  # noqa: ARG005
    "serve": lambda args, parser: _serve(),  # noqa: ARG005
    "doctor": lambda args, parser: _doctor(as_document=args.json),  # noqa: ARG005
    "list": lambda args, parser: _list(as_document=args.json, held=args.held),  # noqa: ARG005
    "help": lambda args, parser: _help(parser, args.verb),
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
        return HANDLERS[args.command](args, parser)
    except AccessError as exc:
        # Beside `ConfigError` because it is the same kind of thing: something
        # the person at the terminal wrote and can fix, in a file or on the
        # command line. A traceback for `--as Q` would bury the one line that
        # says which groups this deployment actually defines.
        print(f"access error: {exc}", file=sys.stderr)
        return 2
    except ConfigError as exc:
        # The one error a caller causes and can fix, so it is reported rather
        # than raised. Anything else is a bug and should keep its traceback.
        print(f"configuration error: {exc}", file=sys.stderr)
        # Where the answer would have come from, said only when it is somewhere
        # the reader is not. This used to say `.env` is never read -- true then,
        # and the reason this command failed while `main.py` worked with the key
        # three lines away in a file. Now the useful thing to say is *which*
        # file was read, because a caller standing one directory from theirs
        # gets a message about a variable that is set, just not here.
        where = Path(ENV_FILE).resolve()
        found = "read" if Path(ENV_FILE).is_file() else "not found"
        print(f"configuration comes from the environment and {where} ({found})", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover -- exercised as a subprocess
    raise SystemExit(main())
