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

from kingfisher import SKILL_LAYOUT, Inventory, offered, split_reference


def _from(source: str | None, expected: str) -> str:
    """Name the file a definition came from, when it is not the obvious one.

    Silent for anything where the name already tells you the file -- `reviewer`
    in `reviewer.yaml` is not worth a line of output. Everything else gets said,
    which is more than "is it in a folder": a package contributes tools under
    names that are not its own, so `csv_columns` comes from `csv_profile/` with
    no slash in sight and is exactly the case someone would go looking for.
    """
    return "" if source in (None, expected) else f"  ({source})"


def _agents(found: Inventory) -> Iterator[str]:
    """The agents section, which comes first because it is what a request names.

    Its own function rather than eighteen more lines inside `render`, which was
    already at the branch ceiling -- and a section that prints a nested thing is
    the one worth lifting out, since the nesting is the only part of this
    listing a reader has to follow rather than scan.
    """
    yield "agents"
    if found.agents_error is not None:
        yield f"  cannot load: {found.agents_error}"
        yield ""
        return
    for name, described in found.agents.items():
        source = found.agent_sources.get(name)
        yield f"  {name}{_from(source, f'{name}.yaml')} — {described}"
        # The delegates it ends up with: its own, and the ones those bring.
        # Printed rather than left to be worked out, because an agent file names
        # only what it calls -- so this is the one place the whole tree is
        # visible without opening every definition it reaches.
        if reached := found.agent_delegates.get(name):
            yield f"      delegates: {', '.join(reached)}"
    if not found.agents:
        yield "  (none)  — a request must name one; try `kingfisher seed`"
    yield ""


def render(found: Inventory, workspace: Path | None = None) -> Iterator[str]:
    """The listing, line by line.

    `workspace` overrides the record's own, for a driver that resolved one
    itself and wants to print what it actually used rather than what the
    configuration says.
    """
    yield f"workspace : {workspace or found.workspace}"
    # Named rather than assumed: the catalogues may be deployed outside the
    # workspace and shared by every deployment that points at them.
    yield f"agents    : {found.agents_source}"
    yield f"skills    : {found.skills_source}"
    yield f"subagents : {found.subagents_source}\n"

    yield from _agents(found)

    if found.tools_error is not None:
        yield "tools"
        yield f"  cannot load: {found.tools_error}"
        # And on to the rest. This used to return, so one unparseable `.py` in
        # `tools/` hid the skills and subagents listings entirely -- against
        # this record's own rule that "one unloadable catalogue must not take
        # the other two down with it", and worst for the person most likely to
        # be running the command, who is looking at a broken workspace.
        yield ""
        yield from _catalogue(found)
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

    yield from _catalogue(found)


def _catalogue(found: Inventory) -> Iterator[str]:
    """The skills and subagents sections, which are the same whether or not
    the tools catalogue loaded.

    Its own generator so the tools failure can fall through to it. That path
    used to `return`, so one unparseable `.py` hid these two entirely -- and
    the person seeing it is by definition looking at a broken workspace, which
    is the worst moment to be shown less of it.
    """
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

    # Loaded, offered, and under a name that is not in the tree. deepagents
    # files a skill by its header and logs a warning nobody reads, so this is
    # the only place a reader learns why `--skills company-lookup` comes back
    # unknown for a directory they are looking straight at.
    for directory, name in found.skills_misfiled:
        yield f"  ! {directory}/ is offered as {name} — its own header names that instead"
        yield f"    (grant it as {name}, or rename the directory to match)"

    yield "\nsubagents"
    if found.subagents_error is not None:
        yield f"  cannot load: {found.subagents_error}"
        return
    for name, described in found.subagents.items():
        # A reference already names the file, so the trailing annotation is
        # dropped for it -- `team/surveyor.yaml::surveyor (team/surveyor.yaml)`
        # says one thing twice in a listing whose job is to be scannable. Two
        # folders may each define a `surveyor`, and then the key is the
        # reference; where a name is its own, the two are the same string.
        claimed, plain = split_reference(name)
        source = None if claimed else found.subagent_sources.get(name)
        # Two spellings now, so the obvious filename depends on which kind this
        # is. Getting it wrong is not cosmetic: `_from` stays silent exactly
        # when the name already tells you the file, and comparing a `.py`
        # definition against `<name>.yaml` would annotate every one of them
        # with the file a reader could already see.
        compiled = name in found.compiled_subagents
        obvious = f"{plain}.py" if compiled else f"{plain}.yaml"
        marker = "  [compiled]" if compiled else ""
        yield f"  {name}{_from(source, obvious)}{marker} — {described}"
        # Indented under their owner rather than listed with the catalogue's,
        # because that is the fact: nothing else holds them. A reader scanning
        # the `tools` section above has seen everything the *agent* can call,
        # and these are the ones it cannot.
        for kind, held in (("tools", found.bundled_tools), ("skills", found.bundled_skills)):
            for own in held.get(name, ()):
                yield f"      {own}  [private {kind[:-1]}]"
        for hidden in found.shadowed.get(name, ()):
            # Said out loud because shadowing is only acceptable while it is
            # visible. The delegate answers `fetch` with its own; the
            # catalogue's never reaches it, and no other line here would say so.
            yield f"      {hidden}  [private tool, shadowing the catalogue's]"
    if found.compiled_subagents:
        # The thing a reader would otherwise assume. deepagents runs a compiled
        # graph as given and never applies our allowlist to it, so a tool grant
        # is a suggestion there rather than a limit -- and nothing else in this
        # output would say so.
        yield "  (a compiled delegate brings its own graph: --tools and"
        yield "   --builtin-tools do not restrict what it can call)"
    if not found.subagents:
        yield "  (none)  — try `kingfisher seed`"


def as_json(found: Inventory) -> dict[str, object]:
    """The same answer, in the shape a script can read.

    Field for field what `Inventory` carries, so there is nothing to keep in
    step: a name here the record does not have would be inventing an answer, and
    one it has that is missing here would be hiding one. A test holds the two
    together, because "field for field" is a claim and not a mechanism.

    Mapped here rather than by the record, for the same reason `render` is here
    -- a serialisation is a format, and formats are the driver's business.
    `Path` becomes a string because JSON has no other option; the mapping
    proxies become plain dicts because `json` will not encode them.
    """
    return {
        "workspace": str(found.workspace),
        "skills_source": found.skills_source,
        "subagents_source": found.subagents_source,
        "agents_source": found.agents_source,
        "agents": dict(found.agents),
        "agent_sources": dict(found.agent_sources),
        "agent_delegates": {name: list(v) for name, v in found.agent_delegates.items()},
        "agents_error": found.agents_error,
        "builtin_tools": list(found.builtin_tools),
        "tools": list(found.tools),
        "tool_sources": dict(found.tool_sources),
        "tools_error": found.tools_error,
        "skills": dict(found.skills),
        "skills_unloadable": list(found.skills_unloadable),
        "skills_misplaced": list(found.skills_misplaced),
        "skills_misfiled": [list(pair) for pair in found.skills_misfiled],
        "skills_enabled": found.skills_enabled,
        "subagents": dict(found.subagents),
        "subagent_sources": dict(found.subagent_sources),
        "compiled_subagents": list(found.compiled_subagents),
        "subagents_error": found.subagents_error,
        "bundled_tools": {k: list(v) for k, v in found.bundled_tools.items()},
        "bundled_skills": {k: list(v) for k, v in found.bundled_skills.items()},
        "shadowed": {k: list(v) for k, v in found.shadowed.items()},
        "bundles_error": found.bundles_error,
    }


def failed(found: Inventory) -> bool:
    """Whether the listing described a workspace that will not load.

    The exit code, decided in one place. Printed and returned apart, a caller
    could report a broken catalogue and exit 0 -- which is how a listing gets
    read by a script that then carries on.

    That is exactly what happened to `agents` for a while: the field was added,
    the section printed "cannot load", and this predicate still named the two
    kinds that existed when it was written. Read as "any of them" now, so the
    next kind is a line in the tuple rather than a silent exit 0.

    A skill that will not load is deliberately not here. One bad directory is
    reported inline and the run still works without it, which is "worth
    knowing" rather than "will not run" -- the split the exit codes are for.
    """
    return any(
        error is not None
        for error in (
            found.agents_error,
            found.tools_error,
            found.subagents_error,
            # A bundle's tools are tools: Python that has to import, and a
            # deployment that starts and fails on the first request activating
            # that delegate is the shape this predicate already exists to stop.
            found.bundles_error,
        )
    )
