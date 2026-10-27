"""Seeding a definitions folder from Python, as short as it goes. Not a test."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from kingfisher import (
    ConfigError,
    Seeded,
    WorkspacePaths,
    definitions_source,
    ensure_definitions,
    seed,
)


def seed_definitions(
    definitions: Path, source: str | Path | None = None, *, everything: bool = False
) -> Seeded:
    """Copy definitions into the folder `definitions`, and return what happened."""
    paths = WorkspacePaths(definitions)

    # Before the source is asked for, which refuses when none is named: `seed` lays
    # the folder out itself, so this only buys `models.yaml.example` for a deployment
    # with nothing to seed yet -- somewhere to start rather than a bare refusal.
    ensure_definitions(paths.definitions)

    # An explicit path wins, else `KINGFISHER_ASSETS`, else a `ConfigError`
    # naming both ways to say where. Nothing ships with the library, so there is
    # no set to fall back on and guessing one would seed from somewhere the
    # caller never named.
    tree = definitions_source(paths.assets, source)

    return seed(paths.definitions, tree, everything=everything)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--definitions", metavar="DIR", help="default: KINGFISHER_DEFINITIONS_DIR"
    )
    parser.add_argument("--from", dest="source", metavar="DIR", help="what to copy from")
    parser.add_argument(
        "--all",
        dest="everything",
        action="store_true",
        help=(
            "also copy definitions naming middleware or source ids this "
            "deployment has not registered"
        ),
    )
    args = parser.parse_args(argv)

    try:
        # `paths_from_env()` is the other way to answer this, and is what the
        # README shows; spelled out here so the script takes a `--definitions`.
        definitions = Path(args.definitions) if args.definitions else _from_env()
        done = seed_definitions(definitions, args.source, everything=args.everything)
    except ConfigError as exc:
        # The refusal says which setting to write, so printing it is the whole
        # of the handling. Exit 2 rather than 1: nothing was attempted.
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    # The half a caller forgets, and the reason this script exists rather than the
    # README snippet alone: a definition naming middleware or source ids this
    # deployment has not got is left behind, and a folder quietly missing an
    # agent you can see in the source directory is worse than one that says why.
    # `report` is what to copy -- writing these lines by hand is what this script
    # used to do, and it told a reader to *register* a source id.
    for line in done.report():
        print(line)

    if (closing := done.nothing_seeded()) is not None:
        print(closing, file=sys.stderr)
        return 1
    return 0


def _from_env() -> Path:
    """`KINGFISHER_DEFINITIONS_DIR`, or the refusal that names it."""
    from kingfisher import paths_from_env

    return paths_from_env().definitions


if __name__ == "__main__":  # pragma: no cover -- driven through `main` by the test
    raise SystemExit(main())
