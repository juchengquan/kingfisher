"""Copying a set of definitions into a deployment's definitions folder."""

from __future__ import annotations

import ast
import os
import shutil
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Protocol, runtime_checkable

from kingfisher.core.config import ConfigError, definition_roots_for
from kingfisher.core.workspace import ensure_definitions

from . import importing
from .catalogue import DEFINITION_KINDS
from .documents import decode

#: How the "this workspace is empty" messages tell a reader to fill it.
SEED_HINT = "`kingfisher seed --from DIR`"


def starter_agent(agents: Path) -> str:
    """How to write an agent by hand, for the reader `SEED_HINT` cannot help.

    Names the full directory, since a reader would resolve `agents/` against wherever
    they are standing, and write a file nothing reads.
    """
    return f"""\
An agent is a file in {agents}/, and three fields are required. A minimal one,
holding every tool:

    # {agents / "helper.yaml"}
    name: helper
    description: General-purpose agent.
    builtin_tools: ["*"]
    tools: ["*"]
    system_prompt: |
      You work in this workspace, on whatever the caller asks for.

Leave out `builtin_tools:` or `tools:` and it holds none of them, as with
`skills:` and `subagents:`; ["*"] is all. See docs/guides/formats.md for the rest."""


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


#: The kinds whose definitions are YAML with a `middlewares:` field. Named rather than
#: detected: a `.yaml` under `tools/` is a tool's data file, not a definition.
DOCUMENT_KINDS = ("agents", "subagents")

#: The kinds whose definitions may say who reaches them from inside Python: a subagent
#: declared in a module, and a tool, whose audience has nowhere else to live. Not every
#: kind: `source_ids=` in a script a skill ships is somebody's variable, not a field.
AUDIENCED_KINDS = ("subagents", "tools")


@dataclass(frozen=True)
class Skipped:
    """A definition left behind, and the names that decided it."""

    label: str
    names: tuple[str, ...]
    #: What those names are, `middlewares` or `source_ids`, since the reason differs: one
    #: must be registered in code, the other switches on access control.
    wants: str = "middlewares"
    #: Datazones the same file names, kept apart from `names` so the line says each in the
    #: field it was written in.
    datazones: tuple[str, ...] = ()
    #: An audience the file computes, as it is written there: what it names is known only
    #: by running the file, so it is held back as a lock would be.
    unread: tuple[str, ...] = ()

    @property
    def asked_for(self) -> str:
        """The same, with the names, in the words of the fields it wrote."""
        named = [f"{self.wants} ({', '.join(self.names)})"] if self.names else []
        if self.datazones:
            named.append(f"datazone ({', '.join(self.datazones)})")
        if self.unread:
            named.append(
                f"an audience seed cannot read without running it ({'; '.join(self.unread)})"
            )
        return " and ".join(named)


#: Why `seed` left a definition behind and what to do, by `Skipped.wants`: one sentence
#: could not be right for both.
HELD_BACK = {
    "middlewares": (
        "and seed does not check what this deployment registered. Seed or write the "
        "middlewares first, then seed again with --all"
    ),
    # A lock is in force wherever it is written, so copying one is choosing access control.
    "source_ids": (
        "and a lock switches on access control in this workspace: every call must then "
        "say who is calling. Seed again with --all to copy it"
    ),
}


@dataclass(frozen=True)
class Seeded:
    """What `seed` did. `overwritten` names files, where `written` names entries."""

    written: tuple[str, ...] = ()
    overwritten: tuple[str, ...] = ()
    skipped: tuple[Skipped, ...] = ()
    #: The tree it was copied from, which the closing line names.
    source: Path = field(kw_only=True)

    def report(self) -> tuple[str, ...]:
        """Every line a person is owed about this, in the order to print them.

        On the record rather than in the command, so every caller prints the same remedy.
        """
        lines = [f"seeded {name}" for name in self.written]
        lines += [
            # With the remedy, because "skipped" alone reads as a failure and this is a choice.
            f"skipped {left.label} — names {left.asked_for}, {HELD_BACK[left.wants]}"
            for left in self.skipped
        ]
        # Last, where it survives skimming: losing an edited copy is what matters most.
        lines += [f"warning: overwrote your edited {name}" for name in self.overwritten]
        return tuple(lines)

    def nothing_seeded(self) -> str | None:
        """The closing line when nothing was written, else `None`."""
        if self.written:
            return None
        if self.skipped:
            # No reason restated: each `skipped` line gives its own, and they may differ.
            # "Holds none of" would send a reader looking elsewhere for files that are
            # right where they thought.
            return (
                f"nothing seeded — every definition in {self.source} was held back; "
                "each line above says what to do first"
            )
        return f"nothing to seed — {self.source} holds none of {', '.join(DEFINITION_KINDS)}"


def source_ids_named(fields: Mapping[str, object]) -> tuple[str, ...]:
    """Every source id a definition names, from fields that may not be valid."""
    found: list[str] = []
    _collect(fields.get("source_ids"), into=found)
    entries = fields.get("skills")
    # Names, or `{name, source_ids}` mappings. Walking the wrong shape finds nothing,
    # silently: it looks like a definition naming no source id. Only `skills:` entries:
    # one under `tools:` or `subagents:` is refused at load, whatever it names.
    if isinstance(entries, (list, tuple)):
        for entry in entries:
            if isinstance(entry, dict):
                _collect(entry.get("source_ids"), into=found)
    return tuple(dict.fromkeys(found))


def datazones_named(fields: Mapping[str, object]) -> tuple[str, ...]:
    """The datazone a definition names, from fields that may not be valid.

    One name, never a list, and only ever a definition's own top-level field: an agent
    writing one is refused and so is an entry, so there is nowhere else to look.
    """
    written = fields.get("datazone")
    # `"*"` included: it is no "any" here, as it is in an audience, and the loader refuses it.
    if isinstance(written, str) and written.strip():
        return (written.strip(),)
    return ()


def audiences_in_code(text: str) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """The source ids and datazones a Python definition names, as its source says them,
    and each audience it computes instead, as written.

    The two shapes a definition has in Python: a `"source_ids":` key, which is how a
    subagent entry says it, and a `source_ids=` argument, which is how a `ToolSpec` does.
    A tool's audience is only ever here -- there is no YAML for it to be in -- and a
    module is where a subagent says it too.

    Parsed rather than imported: seeding runs before there is a catalogue, and importing
    a definition to find out whether to copy it would run a deployment's code to decide.
    A name the module binds once, at its top level, is read through to its value; any
    other value this cannot walk is returned as unread, never taken for naming nobody,
    which copied a lock as an open definition.
    """
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        # Copied anyway, as an unparseable document is: the loader reports it in its own
        # words, and this is not the place to refuse a file.
        return (), (), ()
    bound = _bound_once(tree)
    found: dict[str, list[str]] = {"source_ids": [], "datazone": []}
    unread: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            owner = _named_in(node)
            for key, value in zip(node.keys, node.values, strict=True):
                field_name = key.value if isinstance(key, ast.Constant) else None
                if (
                    isinstance(field_name, str)
                    and field_name in found
                    and not _written(value, into=found[field_name], field=field_name, bound=bound)
                ):
                    unread.append(_as_written(owner, field_name, value))
        elif isinstance(node, ast.Call):
            # A `ToolSpec`'s tool is its first argument.
            first = node.args[0] if node.args else None
            owner = first.id if isinstance(first, ast.Name) else None
            for keyword in node.keywords:
                if keyword.arg in found and not _written(
                    keyword.value, into=found[keyword.arg], field=keyword.arg, bound=bound
                ):
                    unread.append(_as_written(owner, keyword.arg, keyword.value))
    return (
        tuple(dict.fromkeys(found["source_ids"])),
        tuple(dict.fromkeys(found["datazone"])),
        tuple(dict.fromkeys(unread)),
    )


def _named_in(entry: ast.Dict) -> str | None:
    """The `name` a subagent entry gives itself, where it is written out."""
    for key, value in zip(entry.keys, entry.values, strict=True):
        if isinstance(key, ast.Constant) and key.value == "name":
            written = value.value if isinstance(value, ast.Constant) else None
            return written if isinstance(written, str) else None
    return None


def _as_written(owner: str | None, field: str, value: ast.expr) -> str:
    """An audience this cannot read, as the line reporting it names it."""
    written = f"{field}={ast.unparse(value)}"
    return f"{owner}: {written}" if owner else written


def _bound_once(tree: ast.Module) -> dict[str, ast.expr]:
    """Each name the module binds exactly once, by a plain assignment at its top level.

    Counted over the whole module, a function's own bindings included: a name bound
    twice, or bound again inside a function, may be either value where it is used.
    """
    times = Counter(name for node in ast.walk(tree) for name in _binds(node))
    values: dict[str, ast.expr] = {}
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                if isinstance(target, ast.Name):
                    values[target.id] = statement.value
        elif (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.value is not None
        ):
            values[statement.target.id] = statement.value
    return {name: value for name, value in values.items() if times[name] == 1}


def _binds(node: ast.AST) -> Iterator[str]:
    """The names one node binds, an import's, a parameter's and a pattern's included."""
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
        yield node.id
    elif isinstance(node, ast.alias):
        yield (node.asname or node.name).split(".")[0]
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        yield node.name
    elif isinstance(node, ast.arg):
        yield node.arg
    elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
        yield node.name
    elif isinstance(node, ast.MatchMapping) and node.rest:
        yield node.rest


def _written(
    node: ast.expr,
    *,
    into: list[str],
    field: str,
    bound: Mapping[str, ast.expr],
    seen: frozenset[str] = frozenset(),
) -> bool:
    """Every name one audience spells out, by any of the shapes the field takes, and
    whether all of it could be walked."""
    if isinstance(node, ast.Constant):
        name = node.value.strip() if isinstance(node.value, str) else ""
        # `"*"` is everyone in `source_ids` and no one's name; a datazone has no such word.
        if name and not (name == "*" and field == "source_ids"):
            into.append(name)
        return True
    read = partial(_written, into=into, field=field, bound=bound, seen=seen)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(read(one) for one in node.elts)
    if isinstance(node, ast.Dict):
        # A requirement, which is a set: written in Python it is `{"a", "b"}`, and a
        # reader that walked only `ast.Set` would miss the mapping YAML's `{a, b}` becomes
        # when somebody copies it across. A `None` key is `**`, which this cannot see into.
        return all(key is not None and read(key) for key in node.keys)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in ("frozenset", "set")
        and len(node.args) <= 1
    ):
        # The loader takes a `frozenset({...})` as the requirement it is, so a reader that
        # saw only the braces copied a locked tool as an open one. `frozenset()` names
        # nobody, and is the loader's to refuse.
        return all(read(one) for one in node.args)
    if isinstance(node, ast.Name) and node.id in bound and node.id not in seen:
        return _written(
            bound[node.id], into=into, field=field, bound=bound, seen=seen | {node.id}
        )
    return False


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


def middleware_named(fields: Mapping[str, object]) -> tuple[str, ...]:
    """The middleware a definition names, from fields that may not be valid."""
    written = fields.get("middlewares")
    if isinstance(written, str):
        written = [written]
    if not isinstance(written, (list, tuple)):
        return ()

    found = []
    for entry in written:
        # Anything but a name or `{name, settings}` is the format's to refuse, not this one's.
        name = entry.get("name") if isinstance(entry, dict) else entry
        if isinstance(name, str) and name.strip() and name.strip() != "*":
            found.append(name.strip())
    return tuple(found)


def _read(path: Path) -> str | None:
    """One file's text, or `None` where it cannot be read.

    Unreadable is copied anyway: the loader reports such a file in its own words, and
    refusing to seed over it would leave a workspace with a hole and no explanation.
    """
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


#: Why a definition is held back: `Skipped.wants`, the names, the datazones, and what
#: the file computes.
_Wanted = tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]


def _audienced(
    source_ids: tuple[str, ...], datazones: tuple[str, ...], unread: tuple[str, ...] = ()
) -> _Wanted | None:
    """One audience answer, or `None` where a file names nobody."""
    if not source_ids and not datazones and not unread:
        return None
    return "source_ids", source_ids, datazones, unread


def _deployment_specific(path: Path, *, kind: str) -> _Wanted | None:
    """What this file needs that a workspace may not have, or `None` for nothing."""
    if path.is_dir():
        # A package or a bundle is one definition, so it is decided as one: dropping the
        # file that names an audience and copying the rest leaves a folder that is neither.
        # Any other folder only groups, and the copy judges what is in it file by file.
        return _as_one(path, kind=kind) if _one_definition(path, kind=kind) else None
    if not path.is_file() or (text := _read(path)) is None:
        return None
    if path.suffix in (".yaml", ".yml") and kind in DOCUMENT_KINDS:
        decoded = decode(text)
        # Naming nothing, so it is copied and the loader reports it in its own words.
        fields = {} if isinstance(decoded, str) else decoded
        if named := middleware_named(fields):
            return "middlewares", named, (), ()
        return _audienced(source_ids_named(fields), datazones_named(fields))
    if path.suffix == ".py" and kind in AUDIENCED_KINDS:
        return _audienced(*audiences_in_code(text))
    return None


def _one_definition(folder: Path, *, kind: str) -> bool:
    """Whether a folder is a package or a bundle, rather than a folder of definitions."""
    if kind not in AUDIENCED_KINDS:
        return False
    if (folder / importing.PACKAGE_MARKER).is_file():
        return True
    # By the loader's rule: a bundle holds the definition named for it.
    return kind == "subagents" and any(
        folder.name in _names_declared(path) for path in folder.iterdir() if path.is_file()
    )


def _names_declared(path: Path) -> tuple[str, ...]:
    """The `name` a definition file gives itself, as the tolerant readers above see it."""
    if (text := _read(path)) is None:
        return ()
    if path.suffix in (".yaml", ".yml"):
        decoded = decode(text)
        written = None if isinstance(decoded, str) else decoded.get("name")
        return (written,) if isinstance(written, str) else ()
    if path.suffix != ".py":
        return ()
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return ()
    return tuple(
        value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Dict)
        for key, value in zip(node.keys, node.values, strict=True)
        if isinstance(key, ast.Constant)
        and key.value == "name"
        and isinstance(value, ast.Constant)
        and isinstance(value.value, str)
    )


def _as_one(folder: Path, *, kind: str) -> _Wanted | None:
    """What a package or bundle needs, read as the one definition it is."""
    answers = [
        wanted
        for path in sorted(folder.rglob("*"))
        if path.is_file()
        # Every file the copy would judge on its own, so none is left out of a copied folder.
        and not any(map(importing.skipped, path.relative_to(folder).parts))
        and (wanted := _deployment_specific(path, kind=kind)) is not None
    ]
    middlewares = [
        name for wants, names, _, _ in answers if wants == "middlewares" for name in names
    ]
    if middlewares:
        return "middlewares", tuple(dict.fromkeys(middlewares)), (), ()
    source_ids = [
        name for wants, names, _, _ in answers if wants == "source_ids" for name in names
    ]
    datazones = [zone for _, _, zones, _ in answers for zone in zones]
    unread = [one for _, _, _, computed in answers for one in computed]
    return _audienced(
        tuple(dict.fromkeys(source_ids)),
        tuple(dict.fromkeys(datazones)),
        tuple(dict.fromkeys(unread)),
    )


#: What a copy of one directory leaves out of it: each name, with the `Skipped` it is
#: reported as, or `None` for debris.
_LeftOut = Callable[[Path, list[str]], dict[str, Skipped | None]]


def _left_out(
    root: Path, kind: str, directory: Path, names: list[str], *, everything: bool
) -> dict[str, Skipped | None]:
    """Debris, and the deployment-specific definitions seed holds back."""
    left: dict[str, Skipped | None] = dict.fromkeys(filter(importing.skipped, names))
    # Every kind that can say who reaches it, not only the two written as documents: a
    # tool says it in Python, which is the one place it can, and seeding a locked one
    # switches on access control for every call the deployment takes.
    if everything or (kind not in DOCUMENT_KINDS and kind not in AUDIENCED_KINDS):
        return left
    for name in names:
        wanted = None if name in left else _deployment_specific(directory / name, kind=kind)
        if wanted is not None:
            label = f"{kind}/{(directory / name).relative_to(root)}"
            left[name] = Skipped(
                label, wanted[1], wants=wanted[0], datazones=wanted[2], unread=wanted[3]
            )
    return left


def _ignoring(left_out: _LeftOut, found: list[Skipped]) -> Callable[[str, list[str]], set[str]]:
    """`copytree(ignore=...)` that drops what `left_out` names, recording what it held back."""

    def ignore(directory: str, names: list[str]) -> set[str]:
        left = left_out(Path(directory), names)
        found.extend(one for one in left.values() if one is not None)
        return set(left)

    return ignore


def _overwritten(source: Path, target: Path, label: str, left_out: _LeftOut) -> list[str]:
    """Files under `target` this copy is about to change, by content."""
    if source.is_file():
        changed = target.is_file() and target.read_bytes() != source.read_bytes()
        return [label] if changed else []

    found = []
    for directory, dirs, files in os.walk(source):
        here = Path(directory)
        # What the copy leaves out, asked the copy's own way: a file it does not write
        # is one it cannot have overwritten.
        left = left_out(here, [*dirs, *files])
        dirs[:] = [name for name in dirs if name not in left]
        for name in files:
            path = here / name
            landing = target / path.relative_to(source)
            if name not in left and landing.is_file() and landing.read_bytes() != path.read_bytes():
                found.append(f"{label}/{path.relative_to(source)}")
    return sorted(found)


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
    # Before anything is copied, and not left to the caller: a folder never laid out
    # would look seeded and lack the `models.yaml.example` it needs to start.
    ensure_definitions(into.definitions)

    if not source.is_dir():
        msg = f"nothing to seed from: {source} is not a directory"
        raise ConfigError(msg)
    written, overwritten, skipped = _copy(into, source, everything=everything)

    return Seeded(tuple(written), tuple(overwritten), tuple(skipped), source=source)


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
            # A source holding only some of the kinds is ordinary, not a refusal.
            continue
        left_out = partial(_left_out, source, kind, everything=everything)
        ignore = _ignoring(left_out, skipped)
        for item in sorted(source.iterdir()):
            if importing.skipped(item.name):
                continue
            target = destination / item.name
            label = f"{kind}/{item.name}"
            # `ignore` asks this of nested files; `copytree` never shows it a top-level one.
            if not everything and (
                wanted := _deployment_specific(item, kind=kind)
            ) is not None:
                wants, names, datazones, unread = wanted
                skipped.append(
                    Skipped(label, names, wants=wants, datazones=datazones, unread=unread)
                )
                continue
            # Before the copy: afterwards there is nothing left to compare.
            overwritten += _overwritten(item, target, label, left_out)
            target.parent.mkdir(parents=True, exist_ok=True)
            if item.is_dir():
                # `ignore` too: a packaged tool is a directory, and can hold bytecode of its own.
                shutil.copytree(item, target, dirs_exist_ok=True, ignore=ignore)
            else:
                shutil.copy(item, target)
            written.append(label)

    return written, overwritten, skipped
