"""The workspace tree, and the furniture that ships inside it."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from kingfisher.core.config import definition_roots_for
from kingfisher.core.layout import LAYOUT_DIRS, MARKER, MARKER_TEXT

#: An import path rather than a filesystem one: an installed package is not in this
#: repository's tree.
TEMPLATES = "kingfisher.core.templates"


#: The worked example of the one file a deployment *must* write. In the package, not at
#: the repository root, because the wheel holds only `src/kingfisher`.
EXAMPLE = "models.yaml.example"


#: What the keys file `KINGFISHER_MODEL_KEYS_FILE` names looks like. It holds no live
#: entry and says to copy it out first: the definitions folder is shared, and `doctor`
#: warns when the keys file sits where the shell can read it.
KEYS_EXAMPLE = "keys.yaml.example"

#: The examples this package places.
EXAMPLES = (EXAMPLE, KEYS_EXAMPLE)


def ensure_layout(workspace: Path) -> Path:
    """Create the workspace layout. Idempotent.

    No `.gitignore` is written: a workspace is runtime state, and what is authored is in
    `KINGFISHER_DEFINITIONS_DIR`, where versioning belongs.
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

    For seeding alone: several deployments may read this folder, and a start that wrote
    into it would change theirs, or by making it hide a failed mount behind an empty one.
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
