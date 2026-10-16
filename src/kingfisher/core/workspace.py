"""The workspace tree, and the furniture that ships inside it."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from kingfisher.core.config import definition_roots_for
from kingfisher.core.layout import LAYOUT_DIRS, MARKER, MARKER_TEXT

#: Where the shipped templates sit, as an import path rather than a filesystem one -- an
#: installed package is not in this repository's directory tree.
TEMPLATES = "kingfisher.core.templates"


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
    for name in LAYOUT_DIRS:
        (workspace / name).mkdir(parents=True, exist_ok=True)

    marker = workspace / MARKER
    if not marker.exists():
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
