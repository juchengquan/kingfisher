"""Finding installed asset packs, and putting what they hold in a workspace.

Kingfisher ships no skills, no subagents and no tools. Its job is to find,
validate and compose definitions held as static files, and it can do all three
against files it did not write — every asset is content a workspace rewrites on
first contact with a real task, which is a different kind of thing from the code
that reads it.

So the definitions come from *packs*: ordinary distributions that announce
themselves through an entry point. This module asks who is installed and gets an
answer, which is why kingfisher names no pack anywhere in its source. A pack
published by a team internally is no less first-class than any other.

The one thing that does ship here is `models.yaml.example`. That is not content:
`models.yaml` is required and has no fallback, and the error a deployment
without one hits names that file as the place to look, so it has to arrive with
the thing that demands it rather than with a pack somebody may not have
installed. `_copy_example` is why it is seeded outside the loop over packs.

Everything is read through `importlib.resources`, the way `kingfisher.prompts`
is, so the same code finds a source tree and an installed wheel.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from importlib import resources
from importlib.metadata import entry_points
from pathlib import Path

from kingfisher.config import Config, ConfigError
from kingfisher.infrastructure.catalogue import CATALOGUE_KINDS

#: Kingfisher's own package data, as an import path rather than a filesystem one
#: -- an installed package is not in this repo's directory tree. Not called
#: `presets`: leaving the two files that stayed in a directory named for the
#: thing that left is how the next reader concludes assets still ship. It holds
#: the catalogue example and the format documentation, and no definitions.
PACKAGE = "kingfisher.reference"

#: Where an asset pack says it exists. A pack is an ordinary distribution that
#: registers one line of metadata:
#:
#:     [project.entry-points."kingfisher.assets"]
#:     analysis = "my_assets.analysis"
#:
#: Kingfisher names no pack, here or anywhere. It asks which are installed and
#: copies from each, so a pack published by a team internally is no less
#: first-class than one shipped alongside the framework -- which is the whole
#: difference between a library with a blessed bundle and an ecosystem.
#:
#: Kingfisher registered its own definitions under this group for one phase, so
#: the mechanism could be proved before any file moved. It registers nothing
#: now: a framework announcing itself as a source of content is the thing this
#: arrangement exists to stop.
GROUP = "kingfisher.assets"

#: The worked example of the one file a deployment *must* write. It lived at the
#: repo root, which meant it existed only in a checkout: `packages =
#: ["src/kingfisher"]`, so anything one level up is not in the wheel. That is the
#: mistake `test_the_package_ships_the_catalogue_example` now guards against,
#: made for the file a new deployment needs first: `models.yaml` is required and
#: has no fallback.
EXAMPLE = "models.yaml.example"



@dataclass(frozen=True)
class Pack:
    """One installed asset pack: the name it registered under, and its module.

    The name is for messages -- a collision has to say which two packs claimed
    the file, and the reader may own neither.
    """

    name: str
    package: str


def installed_packs() -> tuple[Pack, ...]:
    """Every asset pack this environment offers, in a stable order.

    Sorted by name so two environments holding the same packs seed in the same
    order, and so a collision message reads the same way twice.
    """
    found = [Pack(point.name, point.value) for point in entry_points(group=GROUP)]
    return tuple(sorted(found, key=lambda pack: pack.name))


@contextmanager
def opened(package: str) -> Iterator[Path]:
    """A package's files as real files, wherever it was installed.

        with opened(pack.package) as root:
            shutil.copytree(root / "skills" / "tabular-qa", target)

    A context manager because `importlib.resources` does not promise the files
    exist on disk — a zip-imported package materialises them for the duration
    and cleans up afterwards. In a source tree and an ordinary wheel this hands
    back the real directory and costs nothing.

    `package` is required, though `PACKAGE` was its default until the assets
    left. A default meaning *kingfisher's own tree* is the wrong shape once the
    usual argument is a pack's: code written `opened()` while meaning a pack
    would silently read the framework's two files and copy nothing, which is a
    failure with no error in it. Every caller says which package it wants.
    """
    with resources.as_file(resources.files(package)) as root:
        yield Path(root)


def destinations(cfg: Config) -> tuple[tuple[str, Path], ...]:
    """Each kind of definition, and the catalogue it belongs in.

    The catalogues, not the workspace. They are the same directory until a
    deployment moves one, and seeding the workspace unconditionally is how
    `--seed-assets` used to fill a directory nothing reads.

    Derived from `CATALOGUE_KINDS` rather than listed again. This was the
    fourth place the three kinds were written out, and the one where getting it
    wrong is quietest: a kind missing here is one a pack ships and nothing ever
    copies.
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
    """Bytecode and dotfiles: present in a source tree, never part of a definition."""
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
    pack does not survives and is not reported. Only a collision loses work.
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


def _entries(root: Path) -> tuple[str, ...]:
    """What one pack would write, as labels, without writing any of it.

    Named before copied because a refusal has to leave nothing behind. That is
    the ordering `_admit` keeps for a turn: everything able to reject runs
    first, and only then does anything exist to clean up.
    """
    found = [EXAMPLE] if (root / EXAMPLE).is_file() else []
    for kind in CATALOGUE_KINDS:
        source = root / kind
        if source.is_dir():
            found += [f"{kind}/{item.name}" for item in sorted(source.iterdir())
                      if not _is_debris(item.name)]
    return tuple(found)


def _refuse_collisions(claimed: Mapping[str, list[str]]) -> None:
    """Refuse when two packs claim one entry, naming both.

    Last-one-wins would be "silently different from what you asked for", which
    this codebase refuses at every other boundary -- an upload shadowing a
    catalogue name, two tool modules claiming one tool, a workspace tool
    shadowing a built-in. A collision across packs is that failure one level
    out, and the message names both because whoever reads it may own neither.
    """
    clashes = {entry: packs for entry, packs in claimed.items() if len(packs) > 1}
    if not clashes:
        return
    detail = "; ".join(
        f"{entry} claimed by {' and '.join(sorted(packs))}"
        for entry, packs in sorted(clashes.items())
    )
    msg = (
        f"asset packs disagree about what to seed: {detail}. "
        f"Uninstall one, or seed from a single pack."
    )
    raise ConfigError(msg)


def seed(cfg: Config, packs: Sequence[Pack] | None = None) -> Seeding:
    """Copy every pack's definitions into this deployment's catalogues, and say
    what it changed.

    Copied rather than read in place: they are the deployment's content once
    seeded, and the entire point is that you edit your copy. A definition that
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
    chosen = installed_packs() if packs is None else tuple(packs)
    written: list[str] = []
    overwritten: list[str] = []
    with ExitStack() as stack:
        # Every pack open at once, so the claim check below sees all of them
        # before the first byte is copied.
        roots = [(pack, stack.enter_context(opened(pack.package))) for pack in chosen]
        claimed: dict[str, list[str]] = {}
        for pack, root in roots:
            for entry in _entries(root):
                claimed.setdefault(entry, []).append(pack.name)
        _refuse_collisions(claimed)

        for _pack, tree in roots:
            written_here, overwritten_here = _copy(cfg, tree)
            written += written_here
            overwritten += overwritten_here

    # Outside the pack loop and outside the `ExitStack`: the example is
    # kingfisher's own, so it is written whether or not any pack is installed.
    example_written, example_overwritten = _copy_example(cfg)
    written += example_written
    overwritten += example_overwritten
    return Seeding(tuple(written), tuple(overwritten))


def _copy(cfg: Config, tree: Path) -> tuple[list[str], list[str]]:
    """Copy one opened pack into this deployment's catalogues."""
    written: list[str] = []
    overwritten: list[str] = []
    for kind, destination in destinations(cfg):
        source = tree / kind
        if not source.is_dir():  # pragma: no cover -- all three ship
            continue
        for item in sorted(source.iterdir()):
            # `tools/` holds Python, so importing one of them once -- a test
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
                # only ever saw the top level. A packaged tool used to be a
                # single file, so a directory could not hold bytecode of
                # its own; a package can, and `copytree` would take the lot.
                shutil.copytree(item, target, dirs_exist_ok=True, ignore=_debris)
            else:
                shutil.copy(item, target)
            written.append(label)

    return written, overwritten


def _copy_example(cfg: Config) -> tuple[list[str], list[str]]:
    """Put the catalogue example beside where `models.yaml` is read from.

    Which is where someone would look for the thing they are about to write.
    Not into one of the three catalogues: it is not a definition.

    Not from a pack, either, and not conditional on one being installed.
    `models.yaml` is required and has no fallback, and the error a deployment
    without one hits names this file as the place to look -- so it is the
    worked example of a mandatory *configuration* file rather than content, and
    it arrives with the thing that demands it.

    As `.example`, never as `models.yaml` itself. Seeding overwrites by design
    -- that is what makes re-seeding after an upgrade possible -- and the one
    file it must never overwrite is the one naming every endpoint this
    deployment reaches and whose credentials pay.
    """
    with opened(PACKAGE) as reference:
        example = reference / EXAMPLE
        if not example.is_file():  # a packaging fault, caught by a test
            return [], []
        target = cfg.workspace / EXAMPLE
        overwritten = _overwritten(example, target, EXAMPLE)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(example, target)
        return [EXAMPLE], overwritten
