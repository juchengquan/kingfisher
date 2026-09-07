"""The workspace tree, and the furniture that ships inside it.

What `kingfisher.layout` describes as data, made on disk: the directories a
workspace holds, the marker saying it has been used before, and the worked
example of the one file a deployment must write for itself.

All of it is about the tree a *deployment* runs out of. One session's directory
is `sessions`, and the lifetimes are the reason they are apart -- a workspace is
laid out once and outlives every session inside it.
"""

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
    """True when this path has never been used as a workspace.

    Surfaced by callers so a silently relocated workspace — an unstable `~`,
    a changed env var — reads as "created new" rather than as a first run.
    """
    return not (Path(workspace) / MARKER).exists()


def ensure_layout(workspace: Path, *, authored: Mapping[str, Path] | None = None) -> Path:
    """Create the workspace layout. Idempotent.

    `authored` says where the two files a deployment writes itself are read
    from -- `Config.authored_files` and `WorkspacePaths.authored_files` are it,
    keyed by filename. Omitted means the workspace, which is where both default
    and where every caller holding only a directory should put them.

    What the workspace still owns is what sessions share — the skill and
    subagent definitions — plus the directory sessions live in. Everything the
    agent addresses belongs to a session and is made by
    `sessions.ensure_session_layout`.

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
    """Put each worked example where the file it is an example of is read from.

    Here rather than in `seed`, because seeding can *refuse* -- a deployment that
    names no definitions has nothing to copy -- and this file must arrive anyway:
    `models.yaml` is required, has no fallback, and the error a deployment without
    one hits names this file as the place to look. It is furniture rather than
    content: nothing chooses it, and a workspace without it is missing a part of
    itself.

    Written when absent *or different*. Always writing would touch the disk on
    every run for nothing; only when absent would mean an upgrade never refreshed
    the example, so a deployment would keep reading last year's annotations for a
    file that had grown fields.

    As `.example`, never as `models.yaml` itself: the one file that must not be
    overwritten is the one naming every endpoint this deployment reaches and whose
    credentials pay.

    "Where it is read from" is not always the workspace. Both files relocate --
    `KINGFISHER_MODELS_FILE` points a fleet at one reviewed catalogue, the
    arrangement `compose.yaml` ships -- and an example written into the workspace
    regardless leaves a container deployment with an annotated catalogue in a
    directory nothing reads.

    Best-effort where the destination will not take it: a shared catalogue is often
    mounted read-only, and failing the whole layout over furniture would take
    `kingfisher seed` down for exactly the deployment that relocated. The fallback
    is the workspace.
    """
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
    """Where to try writing one example, in order, without trying twice.

    One entry for the ordinary deployment, which relocated nothing: the two are
    the same path there, and a fallback that repeats the attempt that just
    failed would write the same `OSError` off twice and say nothing new.
    """
    return (wanted,) if wanted == fallback else (wanted, fallback)
