"""The workspace tree, and the furniture that ships inside it."""

from __future__ import annotations

from collections.abc import Mapping
from importlib import resources
from pathlib import Path

from kingfisher.config import ConfigError, definition_roots_for
from kingfisher.domain.session import sessions_root
from kingfisher.layout import (
    HARNESS_OWNED,
    LAYOUT_DIRS,
    LAYOUT_VERSION,
    MARKER,
    MARKER_TEXT,
)

#: Where the shipped templates sit, as an import path rather than a filesystem one -- an
#: installed package is not in this repository's directory tree.
TEMPLATES = "kingfisher.templates"


#: The worked example of the one file a deployment *must* write. It lived at the
#: repository root once, which meant it existed only in a checkout: `packages =
#: ["src/kingfisher"]`, so anything one level up is not in the wheel. That is the
#: mistake `test_the_package_ships_the_catalogue_example` guards against, made for the
#: file a new deployment needs first. Moving it into `templates/` is the same file one
#: directory in, and that guard asserts both halves of it -- inside the package, and
#: reachable the way an install reaches it -- so the tidying cannot quietly repeat the
#: mistake.
EXAMPLE = "models.yaml.example"


#: The same furniture for the other file a deployment may be told to write. The one
#: example this package places, and the reason it is the only one.
EXAMPLES = (EXAMPLE,)


def is_new_workspace(workspace: Path) -> bool:
    """True when this path has never been used as a workspace."""
    return not (Path(workspace) / MARKER).exists()


def _layout_of(marker: Path) -> int:
    """The layout version a workspace was made under. `1` for one that predates
    the marker carrying a number at all."""
    for line in marker.read_text(encoding="utf-8").splitlines():
        if line.startswith("layout "):
            return int(line.removeprefix("layout ").strip() or 0)
    return 1


#: Where layout 1 kept a session's agent, conversation, turn lock and run log, under
#: `HARNESS_OWNED`. The refusal names the ones still there, and what it names is what
#: is looked for: an operator who deletes exactly what the message says has to be
#: let through.
OLD_STATE = ("agents", "runs", "claims", "tmp")


def _left_from_before(workspace: Path) -> tuple[str, ...]:
    """What an older layout wrote that is still where it wrote it, as the refusal
    names it. Empty when nothing is."""
    left = []
    sessions = sessions_root(workspace)
    if sessions.is_dir() and any(sessions.iterdir()):
        left.append(f"everything in {sessions.name}/")
    left.extend(
        f"{HARNESS_OWNED}/{name}"
        for name in OLD_STATE
        if (workspace / HARNESS_OWNED / name).exists()
    )
    return tuple(left)


def check_layout(workspace: Path) -> None:
    """Refuse a workspace laid out by a version that arranged it differently.

    Loudly, because the failure without it is silent: code looking for a
    session's files where this layout keeps them does not error when they are
    somewhere else. A pin it cannot find re-pins, possibly under a different
    agent; a transcript it cannot find starts the conversation from nothing; a
    caller's files in a folder no route reaches are never shown to the agent.

    There is no migration and no fallback reader on purpose. A fallback would
    have to stay for as long as anyone might have an old workspace, which is
    forever, and nothing would ever say the day had come to remove it. Sessions
    are disposable here; the authored tier that is not is what
    `KINGFISHER_SKILLS_DIR` keeps somewhere else.
    """
    marker = Path(workspace) / MARKER
    if not marker.is_file():
        return
    found = _layout_of(marker)
    if found == LAYOUT_VERSION:
        return
    left = _left_from_before(Path(workspace))
    # Nothing left to lose, so nothing to refuse, and `ensure_layout` brings the
    # marker up to date. Deleting the marker is not the way through: a workspace
    # without one reads as new, and the integration driver seeds over a new one.
    if not left:
        return
    msg = (
        f"{workspace} was laid out by kingfisher layout {found}, and this is layout "
        f"{LAYOUT_VERSION}. Its sessions keep files where this layout does not look, "
        f"and one opened here would carry on without them; there is no migration. "
        f"Delete what is left from layout {found} -- {', '.join(left)} -- and the "
        f"marker is brought up to date on the next start; or point "
        f"KINGFISHER_WORKSPACE at a new workspace. Definitions relocate with "
        f"KINGFISHER_SKILLS_DIR and its siblings and do not have to move."
    )
    raise ConfigError(msg)


def ensure_layout(
    workspace: Path,
    *,
    authored: Mapping[str, Path] | None = None,
    catalogue_roots: Mapping[str, Path] | None = None,
) -> Path:
    """Create the workspace layout, and each kind's folder where it is read from. Idempotent.

    No `.gitignore` is written, and nothing here runs git. A shipped one listed two
    of the five things a workspace holds, so it read as complete while being wrong:
    `Library/` fell outside it, and a `git add -A` in a real workspace offered to
    commit a 21MB pip cache. An operator who wants version control is better served
    writing the rules they actually want.

    A workspace is runtime state. The 132KB of authored content in it -- `skills`,
    `subagents`, `tools` against 256MB of sessions and harness state -- is what
    `KINGFISHER_SKILLS_DIR` and its four siblings exist to relocate, and versioning
    belongs there rather than around the sessions.
    """
    workspace = Path(workspace).expanduser().resolve()
    # Before anything is created in it, so a workspace this version cannot serve
    # is refused rather than half-relaid. An empty path has no marker and is not
    # an old workspace; `check_layout` says nothing about it.
    check_layout(workspace)
    for name in LAYOUT_DIRS:
        (workspace / name).mkdir(parents=True, exist_ok=True)
    # Where the catalogue is read from and nowhere else: a kind that was moved gets no
    # folder in the workspace, because an empty one there is where a reader puts a
    # definition that is then never loaded. A caller holding only a directory is
    # saying the definitions are read from it, which for most deployments is true.
    # An empty mapping makes none, for a catalogue its caller staged.
    #
    # Nothing a moved kind left in the workspace is removed, empty or not: it is the
    # operator's folder to delete, and this runs on every start.
    roots = definition_roots_for(workspace) if catalogue_roots is None else catalogue_roots
    for root in roots.values():
        Path(root).mkdir(parents=True, exist_ok=True)

    marker = workspace / MARKER
    if not marker.exists() or _layout_of(marker) != LAYOUT_VERSION:
        marker.write_text(MARKER_TEXT, encoding="utf-8")

    _place_example(workspace, authored)
    return workspace


def _place_example(workspace: Path, authored: Mapping[str, Path] | None = None) -> None:
    """Put each worked example where the file it is an example of is read from."""
    beside = dict(authored or {})
    for name in EXAMPLES:
        source = resources.files(TEMPLATES).joinpath(name)
        if not source.is_file():  # a packaging fault, caught by a test
            continue
        text = source.read_text(encoding="utf-8")
        # By filename, so the two halves of the pair cannot be mapped to each
        # other anywhere else: `authored` is keyed by the name of the real file,
        # and this is that name with `.example` on the end.
        wanted = beside.get(name.removesuffix(".example"), workspace / name).parent
        for target in _candidates(wanted / name, workspace / name):
            if target.is_file() and target.read_text(encoding="utf-8") == text:
                break
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
            except OSError:
                continue
            break


def _candidates(wanted: Path, fallback: Path) -> tuple[Path, ...]:
    """Where to try writing one example, in order, without trying twice."""
    return (wanted,) if wanted == fallback else (wanted, fallback)
