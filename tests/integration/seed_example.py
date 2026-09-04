"""Seeding a workspace from Python, as short as it goes. Not a test.

`driver.py` beside this file also seeds, and does it inside seven hundred lines
that also resolve a model, open a session, build capabilities and stream a turn.
That is the right shape for a driver and the wrong one for the question "what do
I write to seed a workspace" -- the answer is four calls, and finding them meant
reading past everything else.

So this is those four calls and nothing else. It is the README's example with
the errors handled, which is the difference between a snippet and something you
can paste.

Here rather than under `tests/unit/` because of what it *is*: a caller, not a
test. It calls `seed` in order to seed a workspace, where a test constructs a
call in order to observe one -- the distinction `PRODUCTION` in
`test_architecture` is drawn on, and the same one that puts `driver.py` on this
shelf.

Not named `test_*.py`, and that matters rather than being a style choice:
everything collected from this directory by a bare `pytest` would run, and this
shelf is where the things that cost money live.
`test_nothing_on_the_live_shelf_is_collected` enforces the naming.

Nothing here reaches a model or spends anything -- seeding copies files. It sits
on this shelf for what it is, not what it costs.

    uv run tests/integration/seed_example.py                    # from KINGFISHER_ASSETS
    uv run tests/integration/seed_example.py --from ./assets_examples
    uv run tests/integration/seed_example.py --from ./assets_examples --all

**Driven, not just readable.** `test_the_example_script_seeds_a_workspace` in
`tests/unit/test_seeding_from_python.py` imports `seed_workspace` and runs it,
so an argument that moves or a field that is renamed turns a test red rather
than leaving a file that only ever gets read. An example nothing exercises is an
example nobody notices has gone stale, which is the condition `call_cap.py` is
driven by a scripted model to avoid.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from kingfisher import (
    ConfigError,
    Seeded,
    WorkspacePaths,
    definitions_source,
    ensure_layout,
    seed,
)


def seed_workspace(
    workspace: Path, source: str | Path | None = None, *, everything: bool = False
) -> Seeded:
    """Copy definitions into `workspace`, and return what happened.

    The whole of the flow. `source` overrides `KINGFISHER_ASSETS` and is what
    `--from` passes; `everything` is `--all`.
    """
    paths = WorkspacePaths(workspace)

    # Belt-and-braces rather than an ordering that has to be got right: `seed`
    # lays the workspace out itself, so `models.yaml.example` arrives either way.
    #
    # What this line buys is the path `seed` never reaches. `definitions_source`
    # below refuses when a deployment has named no assets, and it refuses before
    # seeding starts -- so without this, that deployment gets an empty directory
    # and a traceback instead of a laid-out workspace and an error explaining
    # itself. `test_the_example_script_refuses_with_no_source_configured` drives
    # exactly that path.
    #
    # `authored` because both examples belong beside the file they describe, and
    # both files relocate.
    ensure_layout(paths.workspace, authored=paths.authored_files)

    # An explicit path wins, else `KINGFISHER_ASSETS`, else a `ConfigError`
    # naming both ways to say where. Nothing ships with the library, so there is
    # no set to fall back on and guessing one would seed from somewhere the
    # caller never named.
    tree = definitions_source(paths, source)

    return seed(paths, tree, everything=everything)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workspace", metavar="DIR", help="default: KINGFISHER_WORKSPACE")
    parser.add_argument("--from", dest="source", metavar="DIR", help="what to copy from")
    parser.add_argument(
        "--all",
        dest="everything",
        action="store_true",
        help="also copy definitions naming middleware or groups this deployment has not registered",
    )
    args = parser.parse_args(argv)

    try:
        # `paths_from_env()` is the other way to answer this, and is what the
        # README shows; spelled out here so the script takes a `--workspace`.
        workspace = Path(args.workspace) if args.workspace else _from_env()
        done = seed_workspace(workspace, args.source, everything=args.everything)
    except ConfigError as exc:
        # The refusal says which setting to write, so printing it is the whole
        # of the handling. Exit 2 rather than 1: nothing was attempted.
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    for name in done.written:
        print(f"seeded {name}")

    # The half a caller forgets, and the reason this script exists rather than
    # the README snippet alone. A definition naming middleware or groups this
    # deployment has not registered is refused when it is built, so `seed`
    # leaves it -- and a workspace quietly missing an agent you can see in the
    # source directory is worse than one that says why.
    for left in done.skipped:
        print(
            f"skipped {left.label} — needs {', '.join(left.names)}; "
            f"register those, then run again with --all"
        )

    # Last, so it survives being skimmed: the point of seeding is that you edit
    # your copy, and this is the line saying you just lost an edit.
    for name in done.overwritten:
        print(f"warning: overwrote your edited {name}")

    if not done.written:
        print("nothing seeded", file=sys.stderr)
        return 1
    return 0


def _from_env() -> Path:
    """`KINGFISHER_WORKSPACE`, or the refusal that names it."""
    from kingfisher import paths_from_env

    return paths_from_env().workspace


if __name__ == "__main__":  # pragma: no cover -- driven through `main` by the test
    raise SystemExit(main())
