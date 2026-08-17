"""Turning an `Inventory` into the block someone reads.

The only place either driver formats a listing. `main.py` prints through this
too, so `kingfisher list` and `main.py --list` cannot come apart -- which is
what makes keeping both doors safe: two entry points, one implementation.

Lines rather than prints, so a caller decides where they go. A library that
writes to stdout cannot be used by a server, and this one is reached by both.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from kingfisher import SKILL_LAYOUT, Inventory, offered


def _from(source: str | None, expected: str) -> str:
    """Name the file a definition came from, when it is not the obvious one.

    Silent for anything where the name already tells you the file -- `reviewer`
    in `reviewer.yaml` is not worth a line of output. Everything else gets said,
    which is more than "is it in a folder": a package contributes tools under
    names that are not its own, so `csv_columns` comes from `csv_profile/` with
    no slash in sight and is exactly the case someone would go looking for.
    """
    return "" if source in (None, expected) else f"  ({source})"


def render(found: Inventory, workspace: Path | None = None) -> Iterator[str]:
    """The listing, line by line.

    `workspace` overrides the record's own, for a driver that resolved one
    itself and wants to print what it actually used rather than what the
    configuration says.
    """
    yield f"workspace : {workspace or found.workspace}"
    # Named rather than assumed: the catalogues may be deployed outside the
    # workspace and shared by every deployment that points at them.
    yield f"skills    : {found.skills_source}"
    yield f"subagents : {found.subagents_source}\n"

    if found.tools_error is not None:
        yield "tools"
        yield f"  cannot load: {found.tools_error}"
        return

    # Two headings, because they are two grants. Printed as one pile, this
    # listing advertised `read_file` beside `csv_profile` and left a reader to
    # guess which flag took which -- and guessing wrong is the "that is a
    # builtin tool" refusal.
    yield "builtin tools — grant with --builtin-tools"
    for name in found.builtin_tools or ("(could not introspect)",):
        yield f"  {name}"

    # Through the same renderer a refusal uses, so the listing and the refusal
    # cannot drift: a name two files define is printed as the reference, because
    # that is what a `--tools` line then has to say.
    yield "\nworkspace tools — grant with --tools"
    yield offered(dict(found.tool_sources), found.tools)

    yield "\nskills" if found.skills_enabled else "\nskills (KINGFISHER_SKILLS is off)"
    # A description each, which subagents have always had here and skills never
    # did -- it is what deepagents will actually put in front of the model.
    for name, described in found.skills.items():
        yield f"  {name}{f' — {described}' if described else ''}"
    if not found.skills:
        yield "  (none)"

    # Present on disk, and the agent will never see it. Reported rather than
    # refused, so one malformed skill does not stop a deployment starting --
    # and a caller who *names* one is refused outright.
    for name in found.skills_unloadable:
        yield f"  ! {name}/ is not loadable — the agent will not be told about it"
    # One folder of grouping works and a second does not, so the obvious next
    # thing to try yields nothing at all. Saying so is the only difference
    # between a catalogue that looks empty and one that is -- and it needs the
    # reason, because tools and subagents nest as deep as anyone likes.
    for name in found.skills_misplaced:
        yield f"  ! {name}/ sits too deep to load — skills live at {SKILL_LAYOUT}"
        yield "    (a folder under the root is its own source, and a source is read"
        yield "     one level down; tools and subagents are read by kingfisher, so"
        yield "     those may nest as deep as you like)"

    yield "\nsubagents"
    if found.subagents_error is not None:
        yield f"  cannot load: {found.subagents_error}"
        return
    for name, described in found.subagents.items():
        yield f"  {name}{_from(found.subagent_sources.get(name), f'{name}.yaml')} — {described}"
    if not found.subagents:
        yield "  (none)  — try `kingfisher seed`"


def failed(found: Inventory) -> bool:
    """Whether the listing described a workspace that will not load.

    The exit code, decided in one place. Printed and returned apart, a caller
    could report a broken catalogue and exit 0 -- which is how a listing gets
    read by a script that then carries on.
    """
    return found.tools_error is not None or found.subagents_error is not None
