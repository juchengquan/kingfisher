"""The definitions kingfisher ships, and putting them in a workspace.

`presets/` holds one working example of each thing a request can activate — a
skill, a subagent, a tool — for you to copy and edit. They are shipped *inside*
the package rather than kept beside it in the repo, and that is the whole point
of this module: a `pip install`ed kingfisher has no repo to copy from, so
seeding used to work only from a checkout.

They are read through `importlib.resources`, the way `kingfisher.prompts` is,
so the same code finds them in a source tree and in an installed wheel.

This does not put domain content in the package. A preset demonstrates a
*format* — it is copied and rewritten on first contact with a real task — where
domain content would presume what your project is about. The distinction is
worth keeping: the reason kingfisher ships no skills of its own is that a
general agent's base behaviour should read the same whatever the project is.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from kingfisher.config import Config

#: Where the shipped definitions live, as an import path rather than a
#: filesystem one -- an installed package is not in this repo's directory tree.
from kingfisher.infrastructure.catalogue import CATALOGUE_KINDS

PACKAGE = "kingfisher.presets"

#: The worked example of the one file a deployment *must* write. It lived at the
#: repo root, which meant it existed only in a checkout: `packages =
#: ["src/kingfisher"]`, so anything one level up is not in the wheel. That is the
#: mistake `test_the_package_ships_its_presets` was written about, made again one
#: directory over -- and made for the file a new deployment needs first, since
#: `models.yaml` is required and has no fallback.
EXAMPLE = "models.yaml.example"



@contextmanager
def opened() -> Iterator[Path]:
    """The preset directory as real files, wherever the package was installed.

        with opened() as presets:
            shutil.copytree(presets / "skills" / "tabular-qa", target)

    A context manager because `importlib.resources` does not promise the files
    exist on disk — a zip-imported package materialises them for the duration
    and cleans up afterwards. In a source tree and an ordinary wheel this hands
    back the real directory and costs nothing.
    """
    with resources.as_file(resources.files(PACKAGE)) as root:
        yield Path(root)


def destinations(cfg: Config) -> tuple[tuple[str, Path], ...]:
    """Each kind of preset, and the catalogue it belongs in.

    The catalogues, not the workspace. They are the same directory until a
    deployment moves one, and seeding the workspace unconditionally is how
    `--seed-examples` used to fill a directory nothing reads.

    Derived from `CATALOGUE_KINDS` rather than listed again. This was the
    fourth place the three kinds were written out, and the one where getting it
    wrong is quietest: a kind missing here is a preset that ships and is never
    copied.
    """
    roots = cfg.catalogue_roots
    return tuple((kind, roots[kind]) for kind in CATALOGUE_KINDS)


@dataclass(frozen=True)
class Seeding:
    """What `seed` did. `overwritten` names files, where `written` names entries.

    The two are deliberately different granularities. An entry is what you asked
    for -- `skills/code-review` -- and a file is what you might have lost, which
    is the thing worth being exact about.
    """

    written: tuple[str, ...] = ()
    overwritten: tuple[str, ...] = ()


def _is_debris(name: str) -> bool:
    """Bytecode and dotfiles: present in the source tree, never part of a preset."""
    return name == "__pycache__" or name.startswith(".")


def _debris(_directory: str, names: list[str]) -> set[str]:
    """`copytree(ignore=...)`, so the rule holds at every depth rather than one."""
    return {name for name in names if _is_debris(name)}


def _overwritten(source: Path, target: Path, label: str) -> list[str]:
    """Files under `target` this copy is about to change, by content.

    By content rather than by presence, because seeding twice with nothing
    edited in between must say nothing at all. A warning that fires on the
    ordinary path is one people learn to scroll past, and then it is not there
    on the path that matters.

    `copytree(dirs_exist_ok=True)` merges, so a file the catalogue has and the
    preset does not survives and is not reported. Only a collision loses work.
    """
    if source.is_file():
        changed = target.is_file() and target.read_bytes() != source.read_bytes()
        return [label] if changed else []

    found = []
    for path in sorted(source.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        landing = target / path.relative_to(source)
        if landing.is_file() and landing.read_bytes() != path.read_bytes():
            found.append(f"{label}/{path.relative_to(source)}")
    return found


def seed(cfg: Config) -> Seeding:
    """Copy every preset into this deployment's catalogues, and say what it changed.

    Copied rather than read in place: they are the deployment's content once
    seeded, and the entire point is that you edit your copy. A preset that
    changed under a catalogue because kingfisher was upgraded would be a
    different thing altogether.

    Which is exactly why the overwriting is reported. Seeding is the one
    operation that writes over those edited copies, and it used to do so
    silently -- an edited `reviewer.md` came back as the shipped one, reported
    identically to a file that had not been there at all.

    It still overwrites: refusing would make re-seeding after an upgrade
    impossible, and that is the same trade `place_data` makes for caller files.
    Replacing silently is the part that was wrong.
    """
    written: list[str] = []
    overwritten: list[str] = []
    with opened() as presets:
        for kind, destination in destinations(cfg):
            source = presets / kind
            if not source.is_dir():  # pragma: no cover -- all three ship
                continue
            for item in sorted(source.iterdir()):
                # `tools/` holds Python, so importing a preset once -- a test
                # run is enough -- leaves bytecode beside it. Seeding that
                # would put a `__pycache__` in the workspace and, worse, teach
                # that it belongs there.
                if _is_debris(item.name):
                    continue
                target = destination / item.name
                label = f"{kind}/{item.name}"
                # Before the copy: afterwards there is nothing left to compare.
                overwritten += _overwritten(item, target, label)
                target.parent.mkdir(parents=True, exist_ok=True)
                if item.is_dir():
                    # `ignore` rather than the check above, because that one
                    # only ever saw the top level. A preset tool used to be a
                    # single file, so a directory could not hold bytecode of
                    # its own; a package can, and `copytree` would take the lot.
                    shutil.copytree(item, target, dirs_exist_ok=True, ignore=_debris)
                else:
                    shutil.copy(item, target)
                written.append(label)

        # The catalogue file, which is not a catalogue *kind* and so has no
        # destination among the three above. It goes beside where kingfisher
        # looks for `models.yaml`, because that is where someone would look for
        # the thing they are about to write.
        #
        # As `.example`, never as `models.yaml` itself. Seeding overwrites by
        # design -- that is what makes re-seeding after an upgrade possible --
        # and the one file it must never overwrite is the one naming every
        # endpoint this deployment reaches and whose credentials pay. A template
        # landing on top of a working catalogue is the worst thing this could do.
        example = presets / EXAMPLE
        if example.is_file():  # absence is a packaging fault, caught by a test
            target = cfg.workspace / EXAMPLE
            overwritten += _overwritten(example, target, EXAMPLE)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(example, target)
            written.append(EXAMPLE)
    return Seeding(tuple(written), tuple(overwritten))
