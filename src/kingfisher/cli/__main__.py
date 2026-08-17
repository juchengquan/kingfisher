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
from kingfisher.cli.health import examine, worst
from kingfisher.cli.listing import failed, render


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
    checkup = sub.add_parser(
        "doctor",
        help="check everything that stands between this install and a run",
        description=(
            "The configuration, the asset packs, the three catalogues and the "
            "shell confinement. Nothing here calls a model, so it costs nothing "
            "to run before a deployment rather than after its first failure -- "
            "which also means a credential it reports as present may still be "
            "wrong. Exits non-zero only on something that will stop a run."
        ),
    )
    checkup.add_argument(
        "--json",
        action="store_true",
        help="emit the same checks as JSON, for a script rather than a person",
    )
    sub.add_parser(
        "list",
        help="show what this workspace offers a request",
        description=(
            "Every name a request may activate here, per grant, with where each "
            "one came from. Exits non-zero if a catalogue will not load."
        ),
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


def _list() -> int:
    """Print what the workspace offers.

    The whole configuration here, unlike `seed`: answering means building an
    agent, and an agent needs to know which model it would run on.
    """
    cfg = from_env()
    found = inventory(cfg)
    for line in render(found, workspace=cfg.workspace):
        print(line)
    return 1 if failed(found) else 0


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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    if args.command is None:
        parser.print_help()
        return 0

    try:
        if args.command == "seed":
            return _seed()
        if args.command == "doctor":
            return _doctor(as_document=args.json)
        return _list()
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
