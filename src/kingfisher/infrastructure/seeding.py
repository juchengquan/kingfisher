"""Copying a set of definitions into a workspace.

Kingfisher does not *read* the definitions a deployment runs. Its job is to
find, validate and compose definitions held as static files, and it does all
three against files it did not write -- content a workspace rewrites on first
contact with a real task, which is a different kind of thing from the code that
reads it.

Nothing ships. The definitions used to live inside this wheel, and before that
they were their own distribution found through a `kingfisher.assets` entry
point so anyone could publish a pack. Both are gone: where a deployment gets
its definitions is a setting now, `KINGFISHER_ASSETS`, and a directory needs no
wheel, no metadata and no publish step. This repository keeps a worked set in
`examples/` for the same reason it keeps documentation.

What that costs is written down rather than glossed: `pip install kingfisher`
followed by `kingfisher seed` no longer produces a working workspace, and since
a request must name an agent, it produces a library that cannot run. See
*Packaging: where the definitions live* in docs/decisions.md, which also records
the two arrangements this one reversed.

`models.yaml.example` used to be seeded here too, apart from the definitions,
because it is the one thing that was never content. `ensure_layout` writes it
now -- it must arrive whether or not a deployment has definitions, and this
module can refuse.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from kingfisher.config import ConfigError
from kingfisher.infrastructure.catalogue import DEFINITION_KINDS
from kingfisher.infrastructure.catalogue.documents import groups_named, middleware_named
from kingfisher.infrastructure.workspace_fs import ensure_layout

#: Named in the refusal below, and only when it is really there.
#:
#: A path inside this repository is true for someone standing in a checkout and
#: false for everyone else, and the reader most likely to hit that refusal is the
#: one who installed the package -- who has no `examples/` anywhere. Advice that
#: fails the way the thing it is advising about failed is the fault four other
#: messages here were rewritten to stop making.
SUGGESTION = Path("examples")

#: How the four "this workspace is empty" messages tell a reader to fill it.
#:
#: One string, because four wordings drift and the one seen daily is the one
#: nobody reviews. It said `kingfisher seed`, which was true while a set shipped
#: and became a dead end when it stopped: a reader with an empty workspace would
#: follow it and hit a refusal for a source that was never configured.
#:
#: `--from DIR` rather than the bare verb, because that form works whether or
#: not `KINGFISHER_ASSETS` is set -- which is the whole property these messages
#: need and the bare verb no longer has.
SEED_HINT = "`kingfisher seed --from DIR`"


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


@runtime_checkable
class Source(Protocol):
    """Where definitions are copied *from*, as a deployment configured it.

    A second protocol rather than a field on `Destination`, and the names are
    the argument: a destination that also knew its source would have stopped
    being one thing. `Destination` is narrow on purpose -- that narrowness is
    what lets seeding run before a workspace has a model catalogue to read --
    and widening it for the first thing that asked would have undone it.

    `WorkspacePaths` and `Config` both satisfy this by shape, which is the same
    arrangement `Destination` has and for the same reason: a first run can
    answer "which directories?" long before it can answer "which models?".
    """

    @property
    def assets(self) -> Path | None: ...


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


#: The kinds whose definitions are YAML documents with a `middleware:` field.
#:
#: `skills` is markdown and `tools` is Python, so neither has one to read. Named
#: rather than "try to parse everything and see", because a `.yaml` under
#: `tools/` would be a tool's data file and reading it as a definition would be
#: this module inventing a meaning for somebody else's file.
DOCUMENT_KINDS = ("agents", "subagents")


@dataclass(frozen=True)
class Skipped:
    """A definition left behind, and the names that decided it.

    The names travel with the label because the message needs them: "names
    middleware" sends a reader looking, and "names call-cap-strict" tells them
    what to register. Formatted by the caller rather than here -- what a CLI
    prints is the CLI's business, and a service seeding a workspace has its own
    way of reporting.
    """

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
    """What `seed` did. `overwritten` names files, where `written` names entries.

    The two are deliberately different granularities. An entry is what you asked
    for -- `skills/code-review` -- and a file is what you might have lost, which
    is the thing worth being exact about.

    `skipped` is the third answer, and it is neither of those: a definition that
    was found, understood, and deliberately not copied. Reported rather than
    silent, because a workspace missing a definition somebody can see in the
    source directory is a bug report waiting to be filed.
    """

    written: tuple[str, ...] = ()
    overwritten: tuple[str, ...] = ()
    skipped: tuple[Skipped, ...] = ()


def _is_debris(name: str) -> bool:
    """Bytecode and dotfiles: present in a source tree, never part of a definition."""
    return name == "__pycache__" or name.startswith(".")


def _deployment_specific(path: Path) -> tuple[str, tuple[str, ...]] | None:
    """What this file needs that a workspace may not have, or `None` for nothing.

    The question `seed` has to answer about one definition: does this belong in
    a workspace that has registered and declared nothing?

    Two ways to fail that, and they fail at different moments. A definition
    naming middleware is refused when it is *built* -- `names unregistered
    middleware` -- so seeding it hands somebody a file that cannot run. A
    definition naming a group is refused when the catalogue is *read*, which is
    worse: it does not break that definition, it stops the deployment. Both are
    a file arriving somewhere it cannot be honoured, which is the one question
    this answers.

    Middleware first where a file names both, because it is the older rule and
    the message can only carry one remedy. A reader who registers the names
    seeds again and hears about the groups.

    Only YAML, and only where such a field exists at all. A directory, a
    Python-defined subagent and a skill body all answer `None`, which is the
    same answer as "reads fine and names nothing".
    """
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
    """`copytree(ignore=...)` that drops debris and deployment-specific definitions.

    Through the ignore callback rather than a check at the top level, for the
    reason the debris rule went the same way: it has to hold at every depth. A
    delegate in `subagents/analysis/` names middleware exactly as easily as one
    beside it, and a rule that only saw the top level would be a rule with a
    hole the catalogue's own layout walks straight through.

    Both rules in one callback because `copytree` takes one, and they were two
    functions only while debris was the only thing being dropped.

    `found` is appended to rather than returned, because `copytree` decides what
    to call this and how often. The labels come out relative to the source tree,
    so they read like the `written` entries beside them.
    """

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


def kinds_at(source: Path) -> tuple[str, ...]:
    """Which of the four kinds a directory actually provides.

    For `kingfisher doctor`. It asked whether the definitions had arrived inside
    the install, which its own comment admitted was "only ever wrong if an
    install is damaged" -- a check that could realistically only pass. A
    configured directory can be unset, mistyped, deleted, or named one level too
    high, so there is something to answer now.

    Empty for a directory that is missing as well as for one holding none of
    them. `doctor` tells those two apart before asking, because the remedies
    differ: a path that is wrong, against a path that points one level off.
    """
    return tuple(kind for kind in DEFINITION_KINDS if (source / kind).is_dir())


def definitions_source(paths: Source, override: str | Path | None = None) -> Path:
    """The directory `seed` should copy from, or a refusal saying how to name one.

    Two callers need this and must agree: `kingfisher seed`, and the driver's
    auto-seed on a fresh workspace. Written once because the alternative is two
    messages, of which the one seen daily is the one nobody reviews -- `main.py`
    printed an instruction naming `--seed-assets` long after that had become the
    wrong advice.

    Public for the same reason `seed` is. The shipped command is held to being a
    consumer of the library rather than an insider, so a private helper would be
    unreachable from the one place that most needs it.

    `override` wins over the environment, which is the ordinary shape of a flag
    against a variable and the one `__main__` already documents for `.env`: an
    explicit argument must not be quietly replaced by something a caller may not
    have known was set.

    Neither given is a refusal rather than a guess. Nothing ships definitions
    any more, so there is no set to fall back to, and inventing one would mean
    seeding a workspace from somewhere the caller never named.

    The refusal names `./examples` only when there is one. See `SUGGESTION`.
    """
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
    raise ConfigError(msg)


def seed(into: Destination, source: Path, *, everything: bool = False) -> Seeded:
    """Copy definitions into this deployment's catalogues, and say what changed.

    `source` is a directory holding `agents/`, `tools/`, `skills/` and
    `subagents/`. Required, with no default: seeding cannot invent where
    definitions come from, and a signature that let it try is one a caller can
    get wrong at runtime rather than at check time. `definitions_source` is what
    turns a flag and a variable into one of these.

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

    `everything` copies definitions that name middleware, which are left behind
    by default. The default is the safe one because the common case is a fresh
    workspace with an empty registry, where such a definition is refused when it
    is built; the flag is for the deployment that has already registered the
    names and wants its own examples. Neither is a judgement about the file --
    it is a fact about the workspace it is going into.
    """
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
    # It does not cover every route to the same state. A caller whose
    # `definitions_source` raises never reaches this line, and gets an
    # unlaid-out workspace with an exception to explain it -- which is the
    # loud version of the same thing, and why the CLI still lays out first.
    ensure_layout(into.workspace)

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
        if not source.is_dir():  # pragma: no cover -- all three ship
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
                # `ignore` rather than the check above, because that one
                # only ever saw the top level. A packaged tool used to be a
                # single file, so a directory could not hold bytecode of
                # its own; a package can, and `copytree` would take the lot.
                shutil.copytree(item, target, dirs_exist_ok=True, ignore=ignore)
            else:
                shutil.copy(item, target)
            written.append(label)

    return written, overwritten, skipped
