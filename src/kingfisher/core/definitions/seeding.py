"""Copying a set of definitions into a deployment's definitions folder."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

import yaml

from kingfisher.core.access.source_ids import AUDIENCED
from kingfisher.core.config import ConfigError, definition_roots_for
from kingfisher.core.definitions.catalogue import DEFINITION_KINDS
from kingfisher.core.workspace import ensure_definitions

#: How the four "this workspace is empty" messages tell a reader to fill it.
SEED_HINT = "`kingfisher seed --from DIR`"


def starter_agent(agents: Path) -> str:
    """How to write an agent by hand, for the reader `SEED_HINT` cannot help.

    Names the directory agents are read from rather than `agents/`, because that is in
    the definitions folder rather than wherever the reader is standing, and a file
    written where nothing reads it is the dead end this exists to get a reader out of.
    """
    return f"""\
An agent is a file in {agents}/, and three fields are required. A minimal one:

    # {agents / "helper.yaml"}
    name: helper
    description: General-purpose agent.
    system_prompt: |
      You work in this workspace, on whatever the caller asks for.

Omitting `tools:` grants every tool there is; omitting `skills:` and
`subagents:` grants none. See docs/guides/formats.md for the rest."""


@runtime_checkable
class Destination(Protocol):
    """Where seeding puts things: a definitions folder, and nothing else."""

    @property
    def definitions(self) -> Path: ...


@runtime_checkable
class Source(Protocol):
    """Where definitions are copied *from*, as a deployment configured it."""

    @property
    def assets(self) -> Path | None: ...


def destinations(into: Destination) -> tuple[tuple[str, Path], ...]:
    """Each kind of definition, and the catalogue it belongs in."""
    roots = definition_roots_for(into.definitions)
    return tuple((kind, roots[kind]) for kind in DEFINITION_KINDS)


#: The kinds whose definitions are YAML documents with a `middlewares:` field.
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
    #: What those names *are* -- `middlewares` or `source_ids`. The remedy differs by
    #: kind, so a message built from the names alone could only be right for one
    #: of them: middleware is registered in code, a source id is declared in
    #: `source_ids.yaml`, and sending a reader to the wrong file is worse than
    #: saying less.
    wants: str = "middlewares"


#: What `seed` did *not* look at before leaving a definition behind, per kind.
UNCONSULTED = {
    "middlewares": "what this deployment registered",
    "source_ids": "your source_ids.yaml",
}

#: And what to do about it. The half a reader acts on, and the half that would be
#: wrong if one sentence served both: a middleware is a class this deployment offers,
#: a source id is declared in a file.
#:
#: This said "Register the names", which sent a reader to write Python three lines
#: after the same run had copied the examples into `middlewares/` -- and a file there
#: counts as registered, which `middleware.md` says in as many words.
REMEDY = {
    "middlewares": "Seed or write the middlewares first",
    # No file named here: an example vocabulary holds one deployment's names, and a
    # workspace needs whichever names its own definitions ask for. `Seeded.report`
    # prints what to
    # write instead, using the names that are actually missing.
    "source_ids": "Declare the source ids in source_ids.yaml",
}


@dataclass(frozen=True)
class Seeded:
    """What `seed` did. `overwritten` names files, where `written` names entries."""

    written: tuple[str, ...] = ()
    overwritten: tuple[str, ...] = ()
    skipped: tuple[Skipped, ...] = ()

    def report(self) -> tuple[str, ...]:
        """Every line a person is owed about this, in the order to print them.

        On the record rather than in the command, because the command was not the
        only thing printing it. The driver and the example script each wrote their
        own version and both said *register those* for a source id -- which is the
        one mistake `Skipped.wants` exists to stop, and neither of them offered the
        `source_ids.yaml` that would unblock the run. A sentence three callers write
        separately is a sentence two of them get wrong.
        """
        lines = [f"seeded {name}" for name in self.written]
        lines += [
            # Named with what to do about it, because "skipped" on its own reads as
            # a failure and this is a choice. The names are the actionable half, and
            # `wants` is what makes them actionable.
            f"skipped {left.label} — names {left.wants} ({', '.join(left.names)}), "
            f"and seed does not check {UNCONSULTED[left.wants]}. "
            f"{REMEDY[left.wants]}, then seed again with --all"
            for left in self.skipped
        ]
        lines += self._declaration()
        # After the list, not beside each entry: the point is that you edit your
        # copy, so losing one is the line that has to survive being skimmed.
        lines += [f"warning: overwrote your edited {name}" for name in self.overwritten]
        return tuple(lines)

    def _declaration(self) -> tuple[str, ...]:
        """The `source_ids.yaml` to write, or nothing when no source id was missing."""
        wanted = sorted(
            {name for left in self.skipped if left.wants == "source_ids" for name in left.names}
        )
        if not wanted:
            return ()
        return (
            "",
            # The artifact rather than the instruction. Each skipped line already
            # says to declare them and to seed again; a third copy of that sentence
            # would be the noise, and what none of those lines can give is the one
            # list that covers all of them.
            "the source_ids.yaml that unblocks every one of them:",
            "",
            f"    source_ids: [{', '.join(wanted)}]",
        )


def source_ids_named(text: str) -> tuple[str, ...]:
    """Every source id a definition names, for a document that may not parse."""
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError:
        return ()
    if not isinstance(parsed, dict):
        return ()

    found: list[str] = []
    _collect(parsed.get("source_ids"), into=found)
    for field_name in AUDIENCED:
        entries = parsed.get(field_name)
        # A list whose entries are names, or mappings of `name` and `source_ids`. A
        # reader that walks the wrong shape finds nothing and reports nothing:
        # a definition naming no source id is exactly what a definition this cannot
        # read looks like from here.
        if isinstance(entries, (list, tuple)):
            for entry in entries:
                if isinstance(entry, dict):
                    _collect(entry.get("source_ids"), into=found)
    return tuple(dict.fromkeys(found))


def _collect(written: object, *, into: list[str]) -> None:
    """The names in one audience, appended. Anything else is the format's to refuse."""
    if isinstance(written, str):
        written = [written]
    if not isinstance(written, (list, tuple)):
        return
    for one in written:
        if isinstance(one, dict):
            # A requirement entry is a set, so its names are the keys.
            _collect(list(one), into=into)
        elif isinstance(one, str) and one.strip() and one.strip() != "*":
            into.append(one.strip())


def middleware_named(text: str) -> tuple[str, ...]:
    """The middleware a definition names, for a document that may not parse."""
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError:
        return ()
    if not isinstance(parsed, dict):
        return ()

    written = parsed.get("middlewares")
    if isinstance(written, str):
        written = [written]
    if not isinstance(written, (list, tuple)):
        return ()

    found = []
    for entry in written:
        # A mapping is the long form, `{name, settings}`; a string is the name
        # on its own. Anything else is a definition the format will refuse, and
        # refusing it here is not this function's job.
        name = entry.get("name") if isinstance(entry, dict) else entry
        if isinstance(name, str) and name.strip() and name.strip() != "*":
            found.append(name.strip())
    return tuple(found)


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
        return "middlewares", named
    if declared := source_ids_named(text):
        return "source_ids", declared
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
    """Which kinds a directory actually provides."""
    return tuple(kind for kind in DEFINITION_KINDS if (source / kind).is_dir())


def definitions_source(paths: Source, override: str | Path | None = None) -> Path:
    """The directory `seed` should copy from, or a refusal saying how to name one."""
    if override is not None:
        return Path(override).expanduser()
    if paths.assets is not None:
        return paths.assets

    msg = (
        "no definitions to seed from: set KINGFISHER_ASSETS to a directory "
        "holding agents/, middlewares/, skills/, subagents/ or tools/, or pass --from DIR"
    )
    raise ConfigError(msg)


def seed(into: Destination, source: Path, *, everything: bool = False) -> Seeded:
    """Copy definitions into this deployment's catalogues, and say what changed."""
    # Before anything is copied, and not left to the caller. Seeding into a folder
    # never laid out succeeds, reports every definition written, and leaves no
    # `models.yaml.example`: a deployment told to write `models.yaml` and given no
    # example of one. The CLI got the ordering right and nothing made a library caller
    # do the same, so the obvious two-liner produced a folder that looked seeded and
    # could not start.
    ensure_definitions(into.definitions)

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
