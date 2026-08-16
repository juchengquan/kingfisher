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
from importlib import resources
from pathlib import Path

from kingfisher.config import Config

#: Where the shipped definitions live, as an import path rather than a
#: filesystem one -- an installed package is not in this repo's directory tree.
PACKAGE = "kingfisher.presets"

#: The kinds a preset can be, which are the directories inside it. Each is
#: copied to the workspace directory of the same name.
KINDS: tuple[str, ...] = ("skills", "subagents", "tools")


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
    """
    return (
        ("skills", cfg.skills_dir),
        ("subagents", cfg.subagents_dir),
        ("tools", cfg.tools_dir),
    )


def seed(cfg: Config) -> tuple[str, ...]:
    """Copy every preset into this deployment's catalogues. Returns what was written.

    Copied rather than read in place: they are the deployment's content once
    seeded, and the entire point is that you edit your copy. A preset that
    changed under a catalogue because kingfisher was upgraded would be a
    different thing altogether.
    """
    written: list[str] = []
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
                if item.name == "__pycache__" or item.name.startswith("."):
                    continue
                target = destination / item.name
                target.parent.mkdir(parents=True, exist_ok=True)
                if item.is_dir():
                    shutil.copytree(item, target, dirs_exist_ok=True)
                else:
                    shutil.copy(item, target)
                written.append(f"{kind}/{item.name}")
    return tuple(written)
