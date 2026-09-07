"""The workspace tree, and the furniture that ships inside it."""

from __future__ import annotations

from collections.abc import Mapping
from importlib import resources
from pathlib import Path

from kingfisher.layout import LAYOUT_DIRS, MARKER

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


def ensure_layout(workspace: Path, *, authored: Mapping[str, Path] | None = None) -> Path:
    """Create the workspace layout. Idempotent.

    No `.gitignore` is written, and nothing here runs git. A shipped one listed two
    of the five things a workspace holds, so it read as complete while being wrong:
    `Library/` fell outside it, and a `git add -A` in a real workspace offered to
    commit a 21MB pip cache. An operator who wants version control is better served
    writing the rules they actually want.

    A workspace is runtime state. The 132KB of authored content in it -- `skills`,
    `subagents`, `tools` against 256MB of sessions and harness state -- is what
    `KINGFISHER_SKILLS_DIR` and its two siblings exist to relocate, and versioning
    belongs there rather than around the sessions.
    """
    workspace = Path(workspace).expanduser().resolve()
    for name in LAYOUT_DIRS:
        (workspace / name).mkdir(parents=True, exist_ok=True)

    marker = workspace / MARKER
    if not marker.exists():
        marker.write_text("kingfisher workspace\n", encoding="utf-8")

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
