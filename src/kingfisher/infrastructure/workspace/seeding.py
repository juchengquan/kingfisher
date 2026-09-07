"""Copying a set of definitions into a workspace.

Kingfisher does not *read* the definitions a deployment runs. Its job is to find,
validate and compose definitions held as static files, and it does all three against
files it did not write -- content a workspace rewrites on first contact with a real
task, which is a different kind of thing from the code that reads it.

**Nothing ships.** Where a deployment gets its definitions is a setting,
`KINGFISHER_ASSETS`, and a directory needs no wheel, no metadata and no publish step.
This repository keeps a worked set in `assets_examples/` for the same reason it keeps
documentation.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from kingfisher.config import ConfigError
from kingfisher.infrastructure.catalogue import DEFINITION_KINDS
from kingfisher.infrastructure.documents import groups_named, middleware_named
from kingfisher.infrastructure.workspace.layout import ensure_layout

#: Named in the refusal below, and only when it is really there.
#:
#: A path inside this repository is true for someone standing in a checkout and
#: false for everyone else, and the reader most likely to hit that refusal is the
#: one who installed the package -- who has no `assets_examples/` anywhere. Advice that
#: fails the way the thing it is advising about failed is the fault four other
#: messages here were rewritten to stop making.
SUGGESTION = Path("assets_examples")

#: Named beside `SUGGESTION`, and conditional for exactly the same reason.
#:
#: `SUGGESTION` says where definitions can be copied *from*; this says where a
#: deployment's own belong once it has fetched some. Both are directories in
#: this checkout and nowhere else, so the reader who installed the package must
#: not be sent to either -- see `SUGGESTION` for the fault that guards against.
DESTINATION = Path("assets")

#: How the four "this workspace is empty" messages tell a reader to fill it.
#:
#: One string, because four wordings drift and the one seen daily is the one
#: nobody reviews.
#:
#: `--from DIR` rather than the bare verb, because that form works whether or not
#: `KINGFISHER_ASSETS` is set -- which is the whole property these messages need.
#: The bare verb hits a refusal for a source that was never configured.
SEED_HINT = "`kingfisher seed --from DIR`"

#: The other half of that answer, for the reader `SEED_HINT` cannot help.
#:
#: `--from DIR` needs a DIR, and `SUGGESTION` deliberately names none to
#: somebody who installed the package -- correctly, because neither directory it
#: could name exists for them. Correct and terminal: an empty workspace, a hint
#: that needs something they have not got, and `docs/guides/formats.md` to read before
#: anything runs.
#:
#: So the smallest agent that works, printed where the refusal is. The pattern is
#: `model_catalogue`'s, which prints a working catalogue inline for the same
#: reason and in the same situation -- a file you cannot start without and have
#: not written.
#:
#: Not a shipped `agents/assistant.yaml.example`. `groups.yaml.example` was
#: deleted for being a *choice* wearing a template's clothes: it shipped one
#: vocabulary while a workspace needs whichever its own definitions ask for, so
#: following the pointer led away from the answer. This has no such content --
#: three fields, no delegates that must exist, no middleware to register, no
#: groups -- which is the same property that let `models.yaml.example` survive
#: that cull.
#:
#: Verified rather than assumed: an agent with exactly these three fields loads,
#: is listed, and compiles to a real graph. A minimal example that turned out to
#: be refused would be `groups.yaml.example` again.
STARTER_AGENT = """\
An agent is a file in agents/, and three fields are required. A minimal one:

    # agents/assistant.yaml
    name: assistant
    description: General-purpose agent.
    system_prompt: |
      You work in this workspace, on whatever the caller asks for.

Omitting `tools:` grants every tool there is; omitting `skills:` and
`subagents:` grants none. See docs/guides/formats.md for the rest."""


@runtime_checkable
class Destination(Protocol):
    """Where seeding puts things: a workspace, its catalogues, and two files."""

    @property
    def workspace(self) -> Path: ...

    @property
    def catalogue_roots(self) -> dict[str, Path]: ...

    @property
    def authored_files(self) -> dict[str, Path]: ...


@runtime_checkable
class Source(Protocol):
    """Where definitions are copied *from*, as a deployment configured it."""

    @property
    def assets(self) -> Path | None: ...


def destinations(cfg: Destination) -> tuple[tuple[str, Path], ...]:
    """Each kind of definition, and the catalogue it belongs in."""
    roots = cfg.catalogue_roots
    return tuple((kind, roots[kind]) for kind in DEFINITION_KINDS)


#: The kinds whose definitions are YAML documents with a `middleware:` field.
#:
#: `skills` is markdown and `tools` is Python, so neither has one to read. Named
#: rather than "try to parse everything and see", because a `.yaml` under
#: `tools/` would be a tool's data file and reading it as a definition would be
#: this module inventing a meaning for somebody else's file.
DOCUMENT_KINDS = ("agents", "subagents")


@dataclass(frozen=True)
class Skipped:
    """A definition left behind, and the names that decided it."""

    label: str
    names: tuple[str, ...]
    #: What those names *are* -- `middleware` or `groups`. The remedy differs by
    #: kind, so a message built from the names alone could only be right for one
    #: of them: middleware is registered in code, a group is declared in
    #: `groups.yaml`, and sending a reader to the wrong file is worse than
    #: saying less.
    wants: str = "middleware"


@dataclass(frozen=True)
class Seeded:
    """What `seed` did. `overwritten` names files, where `written` names entries."""

    written: tuple[str, ...] = ()
    overwritten: tuple[str, ...] = ()
    skipped: tuple[Skipped, ...] = ()


def _is_debris(name: str) -> bool:
    """Bytecode and dotfiles: present in a source tree, never part of a definition."""
    return name == "__pycache__" or name.startswith(".")


def _deployment_specific(path: Path) -> tuple[str, tuple[str, ...]] | None:
    """What this file needs that a workspace may not have, or `None` for nothing."""
    if path.suffix not in (".yaml", ".yml") or not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # Unreadable is the loader's problem to report, in its own words. This
        # copies it and lets that happen.
        return None
    if named := middleware_named(text):
        return "middleware", named
    if grouped := groups_named(text):
        return "groups", grouped
    return None


def _ignoring(
    root: Path, kind: str, *, everything: bool, found: list[Skipped]
) -> Callable[[str, list[str]], set[str]]:
    """`copytree(ignore=...)` that drops debris and deployment-specific definitions."""

    def ignore(directory: str, names: list[str]) -> set[str]:
        dropped = {name for name in names if _is_debris(name)}
        if everything or kind not in DOCUMENT_KINDS:
            return dropped
        here = Path(directory)
        for name in names:
            if name in dropped:
                continue
            if (wanted := _deployment_specific(here / name)) is not None:
                label = f"{kind}/{(here / name).relative_to(root)}"
                found.append(Skipped(label, wanted[1], wants=wanted[0]))
                dropped.add(name)
        return dropped

    return ignore


def _overwritten(source: Path, target: Path, label: str) -> list[str]:
    """Files under `target` this copy is about to change, by content."""
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


def kinds_at(source: Path) -> tuple[str, ...]:
    """Which of the four kinds a directory actually provides."""
    return tuple(kind for kind in DEFINITION_KINDS if (source / kind).is_dir())


def destination_hint() -> str:
    """The clause naming `assets/`, and nothing at all where there is none."""
    return f", and ./{DESTINATION} is where your own go" if DESTINATION.is_dir() else ""


def definitions_source(paths: Source, override: str | Path | None = None) -> Path:
    """The directory `seed` should copy from, or a refusal saying how to name one."""
    if override is not None:
        return Path(override).expanduser()
    if paths.assets is not None:
        return paths.assets

    msg = (
        "no definitions to seed from: set KINGFISHER_ASSETS to a directory "
        "holding agents/, skills/, subagents/ or tools/, or pass --from DIR"
    )
    if SUGGESTION.is_dir():
        msg += f" -- ./{SUGGESTION} is one"
    msg += destination_hint()
    raise ConfigError(msg)


def seed(into: Destination, source: Path, *, everything: bool = False) -> Seeded:
    """Copy definitions into this deployment's catalogues, and say what changed."""
    # Before anything is copied, and not left to the caller. Seeding into a
    # workspace that was never laid out succeeds, reports every definition
    # written, and leaves no `models.yaml.example` -- which is the dead end that
    # write was moved into `ensure_layout` to avoid: a deployment told to write
    # `models.yaml` and given no example of one. The CLI got the ordering right
    # and nothing made a library caller do the same, so the obvious two-liner
    # produced a workspace that looked seeded and could not start.
    #
    # Idempotent, and safe on a workspace that is already in use: it creates
    # directories and refreshes `models.yaml.example`, which is a shipped
    # template rather than anybody's file. A real `models.yaml` is never touched.
    #
    # `authored_files` because both examples belong beside the file they are an
    # example of, and both files relocate. A library caller who seeds a
    # deployment with `KINGFISHER_MODELS_FILE` set would otherwise get the
    # annotated catalogue in the workspace and read the real one elsewhere.
    #
    # It does not cover every route to the same state. A caller whose
    # `definitions_source` raises never reaches this line, and gets an
    # unlaid-out workspace with an exception to explain it -- which is the
    # loud version of the same thing, and why the CLI still lays out first.
    ensure_layout(into.workspace, authored=into.authored_files)

    if not source.is_dir():
        msg = f"nothing to seed from: {source} is not a directory"
        raise ConfigError(msg)
    written, overwritten, skipped = _copy(into, source, everything=everything)

    return Seeded(tuple(written), tuple(overwritten), tuple(skipped))


def _copy(
    into: Destination, tree: Path, *, everything: bool
) -> tuple[list[str], list[str], list[Skipped]]:
    """Copy one opened tree of definitions into this deployment's catalogues."""
    written: list[str] = []
    overwritten: list[str] = []
    skipped: list[Skipped] = []
    for kind, destination in destinations(into):
        source = tree / kind
        if not source.is_dir():
            # A source holding only some of the kinds, which is ordinary rather
            # than exceptional: `--from ./my-agents` is a directory with an
            # `agents/` and nothing else, and seeding it should write the agents
            # rather than refuse. `kinds_at` answers the same question for
            # `doctor`, and `_seed` refuses only when *nothing* was written.
            continue
        ignore = _ignoring(source, kind, everything=everything, found=skipped)
        for item in sorted(source.iterdir()):
            # `tools/` holds Python, so importing one of them once -- a test
            # run is enough -- leaves bytecode beside it. Seeding that
            # would put a `__pycache__` in the workspace and, worse, teach
            # that it belongs there.
            if _is_debris(item.name):
                continue
            target = destination / item.name
            label = f"{kind}/{item.name}"
            # The same question `ignore` asks of a nested file, asked here
            # because `copytree` never sees a top-level one.
            if not everything and (wanted := _deployment_specific(item)) is not None:
                skipped.append(Skipped(label, wanted[1], wants=wanted[0]))
                continue
            # Before the copy: afterwards there is nothing left to compare.
            overwritten += _overwritten(item, target, label)
            target.parent.mkdir(parents=True, exist_ok=True)
            if item.is_dir():
                # `ignore` rather than the check above, which only ever sees the
                # top level: a packaged tool is a directory and can hold bytecode
                # of its own, which `copytree` would take along with the rest.
                shutil.copytree(item, target, dirs_exist_ok=True, ignore=ignore)
            else:
                shutil.copy(item, target)
            written.append(label)

    return written, overwritten, skipped
