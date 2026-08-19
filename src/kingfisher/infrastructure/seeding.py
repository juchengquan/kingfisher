"""Copying a set of definitions into a workspace, so a fresh install works.

Kingfisher does not *read* the definitions it ships. Its job is to find,
validate and compose definitions held as static files, and it does all three
against files it did not write — every asset is content a workspace rewrites on
first contact with a real task, which is a different kind of thing from the code
that reads it. Shipping a working set and copying it out keeps that true: what
lands in the workspace is yours the moment it arrives.

One directory, `kingfisher.assets`, inside this wheel. That is the whole
discovery story now, and it used to be longer -- definitions were their own
distribution, found through an entry point so anyone could publish a pack. The
constants below say what went and why; what matters here is that nothing
enumerates publishers any more, so there is no loop and no question of who is
installed. A deployment with its own definitions points `seed` at a directory
and needs no wheel, no metadata and no publish step.

`models.yaml.example` used to be seeded here too, apart from the definitions,
because it is the one thing that is not content: `models.yaml` is required and
has no fallback, and the error a deployment without one hits names that file as
the place to look. It is written by `ensure_layout` now, which is the honest
owner of a file that must arrive whether or not a deployment has definitions --
and this function is about to become able to refuse.

Everything is read through `importlib.resources`, the way `kingfisher.prompts`
is, so the same code finds a source tree and an installed wheel.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Protocol, runtime_checkable

from kingfisher.config import ConfigError
from kingfisher.infrastructure.catalogue import DEFINITION_KINDS

#: The definitions that ship with kingfisher: one working tool, skill and
#: subagent. Inside the package rather than beside it, so `pip install
#: kingfisher` followed by `kingfisher seed` writes a workspace that already
#: works -- content a reader has to go and find teaches nobody.
#:
#: They were their own distribution, discovered through a
#: `kingfisher.assets` entry point, so that a team could publish a pack and have
#: it seed alongside. That went when the seeding source became a plain path: a
#: deployment pointing `seed` at its own directory needs no wheel and no
#: metadata, which covers the same ground more simply. Rebuilding the plugin
#: group is a small change if a second publisher ever wants one.
ASSETS = "kingfisher.assets"


@contextmanager
def opened(package: str) -> Iterator[Path]:
    """A package's files as real files, wherever it was installed.

        with opened(ASSETS) as root:
            shutil.copytree(root / "skills" / "tabular-qa", target)

    A context manager because `importlib.resources` does not promise the files
    exist on disk — a zip-imported package materialises them for the duration
    and cleans up afterwards. In a source tree and an ordinary wheel this hands
    back the real directory and costs nothing.

    `package` is required and has never had a default. One meaning *kingfisher's
    own tree* was the wrong shape for a reader who wants the definitions: code
    written `opened()` while meaning the assets would silently read the
    framework's own files and copy nothing, which is a failure with no error in
    it. Every caller says which package it wants.
    """
    with resources.as_file(resources.files(package)) as root:
        yield Path(root)


@runtime_checkable
class Destination(Protocol):
    """Where seeding puts things: a workspace, and the three catalogues.

    A Protocol rather than `Config` because seeding a *fresh* workspace has to
    run before a model catalogue can be read -- the catalogue is a file inside
    the workspace, so `from_env` raises before the directory exists. `Config`
    satisfies this by shape, and so does `WorkspacePaths`, which is the part of
    a configuration a first run can actually know.

    Nothing here needs an endpoint, a credential or a timeout. Asking for a
    whole `Config` to copy files was always more than the job required; it only
    became a problem when the job had to happen earlier.
    """

    @property
    def workspace(self) -> Path: ...

    @property
    def catalogue_roots(self) -> dict[str, Path]: ...


def destinations(cfg: Destination) -> tuple[tuple[str, Path], ...]:
    """Each kind of definition, and the catalogue it belongs in.

    The catalogues, not the workspace. They are the same directory until a
    deployment moves one, and seeding the workspace unconditionally is how
    `--seed-assets` used to fill a directory nothing reads.

    Derived from `DEFINITION_KINDS` rather than listed again. This was the
    fourth place the three kinds were written out, and the one where getting it
    wrong is quietest: a kind missing here is one the definitions ship and
    nothing ever copies.
    """
    roots = cfg.catalogue_roots
    return tuple((kind, roots[kind]) for kind in DEFINITION_KINDS)


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
    source does not survives and is not reported. Only a collision loses work.
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


def shipped_kinds() -> tuple[str, ...]:
    """Which catalogue kinds the definitions that ship with kingfisher provide.

    For `kingfisher doctor`, which used to enumerate installed asset packs.
    There is nothing to enumerate now -- one directory either came with the
    install or did not -- so the question became "is there anything to seed
    from", and this is the smallest honest answer to it.
    """
    with opened(ASSETS) as tree:
        return tuple(kind for kind in DEFINITION_KINDS if (tree / kind).is_dir())


def seed(cfg: Destination, source: Path | None = None) -> Seeding:
    """Copy definitions into this deployment's catalogues, and say what changed.

    `source` is a directory holding `tools/`, `skills/` and `subagents/`. Left
    out, it is the set that ships with kingfisher -- so a fresh install seeds a
    workspace that already works, and a deployment with its own definitions
    points here instead and needs no package, no metadata and no publish step.

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
    written: list[str] = []
    overwritten: list[str] = []
    with ExitStack() as stack:
        tree = stack.enter_context(opened(ASSETS)) if source is None else source
        if not tree.is_dir():
            msg = f"nothing to seed from: {tree} is not a directory"
            raise ConfigError(msg)
        written, overwritten = _copy(cfg, tree)

    return Seeding(tuple(written), tuple(overwritten))


def _copy(cfg: Destination, tree: Path) -> tuple[list[str], list[str]]:
    """Copy one opened tree of definitions into this deployment's catalogues."""
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
