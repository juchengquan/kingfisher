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
    sub.add_parser(
        "serve",
        help="run the HTTP surface (needs the server extra)",
        description=(
            "The same thing `kingfisher-server` starts, reading the same "
            "environment. Both names exist because scripts and unit files "
            "already call the older one, and there is one implementation behind "
            "them. Needs `pip install 'kingfisher[server]'`, and says so if it "
            "is missing rather than being absent from this list."
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


def _serve() -> int:
    """Hand off to the server's own entry point, which decides everything.

    Imported here rather than at module scope, and that is not a style choice.
    `kingfisher.presentation` reaches fastapi as it loads, so importing it at the
    top would make `kingfisher list` fail on an install without the server extra
    -- a verb nobody asked for taking down the two they did.

    The same reason `presentation.__main__` imports uvicorn inside `serve`, and
    the same reason this subcommand is in `--help` whether or not the extra is
    installed: a command that exists and says what to install beats one that is
    silently absent.
    """
    try:
        from kingfisher.presentation.__main__ import main as serve_forever  # noqa: PLC0415
    except ImportError:
        print(
            "kingfisher serve needs the server extra: pip install 'kingfisher[server]'",
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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    if args.command is None:
        parser.print_help()
        return 0

    try:
        if args.command == "seed":
            return _seed()
        # `serve` before `_list`, and not only for tidiness: it has no
        # `--json`, so reaching `args.json` on that path is an AttributeError.
        if args.command == "serve":
            return _serve()
        if args.command == "doctor":
            return _doctor(as_document=args.json)
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
