"""The workspace tree, and the furniture that ships inside it."""

from __future__ import annotations

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


#: What the keys file `KINGFISHER_MODEL_KEYS_FILE` names looks like. Placed beside
#: `models.yaml`, whose `key:` entries name its lines, holding no live entry and saying
#: to copy it out first: the definitions folder is shared, and `doctor` warns when the
#: keys file sits where the shell can read it.
KEYS_EXAMPLE = "keys.yaml.example"

#: The examples this package places.
EXAMPLES = (EXAMPLE, KEYS_EXAMPLE)


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
    are disposable here; the authored tier that is not is in
    `KINGFISHER_DEFINITIONS_DIR`, apart from them.
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
        f"KINGFISHER_WORKSPACE at a new workspace. Definitions are in "
        f"KINGFISHER_DEFINITIONS_DIR and do not have to move."
    )
    raise ConfigError(msg)


def ensure_layout(workspace: Path) -> Path:
    """Create the workspace layout. Idempotent.

    No `.gitignore` is written, and nothing here runs git. A shipped one listed two
    of the five things a workspace holds, so it read as complete while being wrong:
    `Library/` fell outside it, and a `git add -A` in a real workspace offered to
    commit a 21MB pip cache. An operator who wants version control is better served
    writing the rules they actually want.

    A workspace is runtime state, and nothing in it is authored: the definitions are
    in `KINGFISHER_DEFINITIONS_DIR`, which is where versioning belongs, and
    `ensure_definitions` lays that out.
    """
    workspace = Path(workspace).expanduser().resolve()
    # Before anything is created in it, so a workspace this version cannot serve
    # is refused rather than half-relaid. An empty path has no marker and is not
    # an old workspace; `check_layout` says nothing about it.
    check_layout(workspace)
    for name in LAYOUT_DIRS:
        (workspace / name).mkdir(parents=True, exist_ok=True)

    marker = workspace / MARKER
    if not marker.exists() or _layout_of(marker) != LAYOUT_VERSION:
        marker.write_text(MARKER_TEXT, encoding="utf-8")
    return workspace


def ensure_definitions(definitions: Path) -> Path:
    """Create a definitions folder: a folder per kind, and the worked examples. Idempotent.

    Seeding's to call, and nothing else's. A start only reads this folder, because
    several deployments may read it too, and one that wrote into it would be changing
    theirs; one that made it would hide a mount that failed behind an empty folder.
    """
    definitions = Path(definitions).expanduser().resolve()
    for root in definition_roots_for(definitions).values():
        root.mkdir(parents=True, exist_ok=True)
    for name in EXAMPLES:
        source = resources.files(TEMPLATES).joinpath(name)
        if not source.is_file():  # a packaging fault, caught by a test
            continue
        text = source.read_text(encoding="utf-8")
        target = definitions / name
        if not (target.is_file() and target.read_text(encoding="utf-8") == text):
            target.write_text(text, encoding="utf-8")
    return definitions
