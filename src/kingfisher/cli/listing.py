"""Turning an `Inventory` into the block someone reads."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from pathlib import PurePath

from kingfisher import Inventory, Origins

# Taken where they live, not from the front door: nobody outside this wheel asks for them.
from kingfisher.core.access import ALL, Audience, Stated, Vocabulary, spell
from kingfisher.core.definitions.seeding import SEED_HINT
from kingfisher.core.definitions.skills.catalogue import SKILL_LAYOUT
from kingfisher.core.definitions.tools.spec import offered, split_reference
from kingfisher.core.inventory import Grant


def _from(source: str | None, expected: str) -> str:
    """Name the file a definition came from, when it is not the obvious one."""
    return "" if source in (None, expected) else f"  ({source})"


def _granted(label: str, grant: Grant | None, star: Callable[[str], str]) -> Iterator[str]:
    """One grant line under its definition, and none for a field granting nothing."""
    if grant is None or not (grant.every or grant.names):
        return
    names = ", ".join(grant.names)
    yield f"      {label}: {star(names) if grant.every else names}"


def _today(names: str, *, unknown: str = "none") -> str:
    """An agent's star beside what it covers now, so a reader sees it will cover more."""
    return f"* — today {names or unknown}"


def _callers(names: str) -> str:
    """A subagent's star, which covers whatever the agent calling it holds."""
    return "* — whatever the calling agent holds" + (f", plus {names}" if names else "")


def _agents(found: Inventory) -> Iterator[str]:
    """The agents section, which comes first because it is what a request names."""
    yield "agents"
    if found.agents_error is not None:
        yield f"  cannot load: {found.agents_error}"
        yield ""
        return
    for name, described in found.agents.items():
        source = found.agent_sources.get(name)
        yield f"  {name}{_from(source, f'{name}.yaml')} — {described}"
        yield from _granted("tools", found.agent_tools.get(name), _today)
        # An empty built-in set is one that could not be read, as the section below says.
        yield from _granted(
            "builtin tools",
            found.agent_builtin_tools.get(name),
            lambda names: _today(names, unknown="(could not introspect)"),
        )
        # Only what it names, since a helper one of them consults runs only if named here too.
        yield from _granted("subagents", found.agent_subagents.get(name), _today)
        if gates := found.agent_gates.get(name):
            yield f"      interrupt_on: {', '.join(gates)}"
    if not found.agents:
        yield f"  (none)  — a request must name one; try {SEED_HINT}"
    yield ""


def render(found: Inventory) -> Iterator[str]:
    """The listing, line by line.

    No early return here: a section skipped on a failure path goes missing exactly when
    something is broken, which is when the listing is read.
    """
    yield from found.origins.block()
    yield ""

    yield from _agents(found)
    yield from _tools(found)
    yield from _skills(found)
    yield from _subagents(found)
    yield from _middlewares(found)
    yield from _bundles(found)
    yield from _build(found)
    yield from _misnamed_tools(found)
    yield from _unresolved_subagents(found)
    yield from _access(found)


def _tools(found: Inventory) -> Iterator[str]:
    """What a request may grant, in two headings because they are two grants."""
    if found.tools_error is not None:
        yield "tools"
        yield f"  cannot load: {found.tools_error}"
        yield ""
        return

    yield "builtin tools — grant with builtin_tools: in an agent file"
    for name in found.builtin_tools or ("(could not introspect)",):
        yield f"  {name}"

    # The renderer a refusal uses, so the two cannot drift: a name two files define
    # prints as the reference a `tools:` line has to say.
    yield "\noffered tools — grant with tools: in an agent file"
    yield offered(dict(found.tool_sources), found.tools)


def _middlewares(found: Inventory) -> Iterator[str]:
    """What this deployment registers, and why the directory could not be read."""
    yield "\nmiddlewares"
    if found.middlewares_error is not None:
        yield f"  cannot load: {found.middlewares_error}"
        return
    for name, module in sorted(found.middlewares.items()):
        # The package, or one installed by pip prints like one in this workspace.
        yield f"  {name}" + (f"  (from {module})" if module else "")
    if not found.middlewares:
        yield "  (none)"


def _bundles(found: Inventory) -> Iterator[str]:
    """Why a subagent's own folder could not be read: not a kind, so no walk of them prints it."""
    if found.bundles_error is None:
        return
    yield "\nbundles"
    yield f"  cannot load: {found.bundles_error}"


def _build(found: Inventory) -> Iterator[str]:
    """Why no graph could be assembled, which is a fault no directory owns."""
    if found.build_error is None:
        return
    yield "\nthe build"
    yield f"  cannot build an agent: {found.build_error}"


def _misnamed_tools(found: Inventory) -> Iterator[str]:
    """Definitions naming a tool by a path it has moved from.

    A section of its own: a subagent doing this already fails to load, while an agent
    loads and runs without the tool, and nothing else here says so.
    """
    if not found.moved_tools:
        return
    yield "\ntools named by a path they have moved from"
    for subject, names in sorted(found.moved_tools.items()):
        yield f"  {subject}: {', '.join(sorted(names))}"


def _unresolved_subagents(found: Inventory) -> Iterator[str]:
    """Definitions naming a subagent the catalogue has no key for.

    Said here or nowhere: an agent's `subagents:` line above prints what a run
    activates, so the name it could not resolve is simply not on it.
    """
    if not found.unresolved_subagents:
        return
    yield "\nsubagent names matching nothing under subagents"
    for subject, names in sorted(found.unresolved_subagents.items()):
        yield f"  {subject}: {', '.join(sorted(names))}"
    yield "  (a run reaching one is refused: write each as listed under subagents,"
    yield "   where a name two files share is spelt with its file)"


def origins_document(origins: Origins) -> dict[str, object]:
    """Where everything was read from, for a script rather than a person."""
    return {
        "workspace": str(origins.workspace),
        "definitions": str(origins.definitions),
        **{
            name: {"kind": origin.kind, "path": None if origin.path is None else str(origin.path)}
            for name, origin in origins.entries()
        },
    }


def _who(audience: Audience) -> str:
    """One audience, as a reader sees it."""
    return f"[{spell(audience)}]"


def _said(stated: Stated) -> str:
    """What a definition says of who reaches it, its datazone included."""
    # Beside the audience rather than folded into it: `[*]` alone reads as everyone.
    zone = "" if stated.datazone is None else f"  datazone: {stated.datazone}"
    return f"{_who(stated.source_ids)}{zone}"


def _access(found: Inventory) -> Iterator[str]:
    """Who reaches what, twice: as the files say it, and rolled up by skill."""
    # `vocabulary` alone decides, as in `as_json`, so a scoped caller's text and JSON agree.
    if found.vocabulary is None:
        return
    yield "\naccess — by definition"
    said = False
    for kind, definitions in found.audiences.items():
        for name, stated in definitions.items():
            said = True
            yield f"  {kind[:-1]} {name}  {_said(stated)}"
            for entry, audience in stated.skills.items():
                yield f"      skill {entry}  {_who(audience)}"
    if not said:
        yield "  (nothing says who reaches it, so everyone reaches everything)"

    yield from _in_use(found.vocabulary)
    yield from _rollup(found)
    yield from found.access_report.lines()


def _in_use(vocabulary: Vocabulary) -> Iterator[str]:
    """Each name a lock uses, beside the definitions using it.

    What catches a misspelt lock, since nothing refuses one: `sales_bd` written once sits
    under `sales_db` written five times.
    """
    rows = [
        *((name, who) for name, who in vocabulary.source_ids.items()),
        *((f"datazone {zone}", who) for zone, who in vocabulary.datazones.items()),
    ]
    width = max(len(name) for name, _ in rows)
    yield "\naccess — names in use"
    for name, who in rows:
        yield f"  {name.ljust(width)}  {', '.join(who)}"


def _rollup(found: Inventory) -> Iterator[str]:
    """Every skill an entry gives an audience, under it each definition that does.

    Skills alone: a tool's lock and a subagent's audience are each one line in its own
    file, printed above, so there is no second place for a use of one to differ.
    """
    where: dict[str, list[str]] = {}
    for definitions in found.audiences.values():
        for name, stated in definitions.items():
            for entry, audience in stated.skills.items():
                where.setdefault(entry, []).append(f"{name}  {_who(audience)}")
    if not where:
        return
    yield "\naccess — by skill"
    for entry, sites in sorted(where.items()):
        yield f"  {entry}"
        for site in sites:
            yield f"      {site}"


def _skills(found: Inventory) -> Iterator[str]:
    """The skills section: what loaded, and each way one does not reach the agent."""
    yield (
        "\nskills" if found.skills_enabled else "\nskills (KINGFISHER_SKILLS_ENABLED is off)"
    )
    # With its description, which is what deepagents puts in front of the model.
    for name, described in found.skills.items():
        yield f"  {name}{f' — {described}' if described else ''}"
    if not found.skills:
        yield "  (none)"

    # Reported, not refused, so one malformed skill does not stop a deployment; a
    # caller naming one is refused outright.
    for name in found.skills_unloadable:
        yield f"  ! {name}/ is not loadable — the agent will not be told about it"
    # With the reason: one folder of grouping works and a second does not, unlike
    # tools and subagents.
    for name in found.skills_misplaced:
        yield f"  ! {name}/ sits too deep to load — skills live at {SKILL_LAYOUT}"
        yield "    (a folder under the root is its own source, and a source is read"
        yield "     one level down; tools and subagents are read by kingfisher, so"
        yield "     those may nest as deep as you like)"

    # deepagents files a skill by its header and logs a warning nobody reads, so only
    # this says why a directory's own name comes back unknown.
    for directory, name in found.skills_misfiled:
        yield f"  ! {directory}/ is offered as {name} — its own header names that instead"
        yield f"    (grant it as {name}, or rename the directory to match)"


def _subagents(found: Inventory) -> Iterator[str]:
    """The subagents section, with what each one brings that nobody else holds.

    The early `return` drops anything after it when subagents will not load, so nothing
    another section needs belongs in here.
    """
    yield "\nsubagents"
    if found.subagents_error is not None:
        yield f"  cannot load: {found.subagents_error}"
        return
    for name, described in found.subagents.items():
        # A reference already names its file, so it gets no `(source)` annotation.
        claimed, plain = split_reference(name)
        source = None if claimed else found.subagent_sources.get(name)
        # The obvious file depends on the kind: against `<name>.yaml`, every `.py`
        # definition would be annotated with the file its name already shows.
        compiled = name in found.compiled_subagents
        obvious = f"{plain}.py" if compiled else f"{plain}.yaml"
        marker = "  [compiled]" if compiled else ""
        yield f"  {name}{_from(source, obvious)}{marker} — {described}"
        # A compiled one never prints the second: it is handed no built-in.
        yield from _granted("tools", found.subagent_tools.get(name), _callers)
        yield from _granted("builtin tools", found.subagent_builtin_tools.get(name), _callers)
        # Private tools and skills sit under their owner, because nothing else holds them.
        stranded = found.stranded_skills.get(name, ())
        # Marked, or a bundle installed by pip prints like one a reader can open here.
        carried = ", carried" if name in found.carried_bundles else ""
        # A shadowing tool gets one row, from the shadowing loop: `shadowed` is a
        # subset of `bundled_tools`.
        shadowing = found.shadowed.get(name, ())
        for kind, held in (("tools", found.bundled_tools), ("skills", found.bundled_skills)):
            for own in held.get(name, ()):
                if kind == "tools" and own in shadowing:
                    continue
                # On the line itself, or a compiled subagent's private skill prints
                # like one that arrives.
                reaches = "  (a compiled graph is told about no skills)" if own in stranded else ""
                yield f"      {own}  [private {kind[:-1]}{carried}]{reaches}"
        if (miscounted := found.miscounted_bundles.get(name)) is not None:
            # Under the subagent, beside the folder contents its list disagrees with.
            yield f"      ! {miscounted}"
        for deep in found.bundled_misplaced.get(name, ()):
            # The catalogue allows one folder of grouping; a subagent's own folder is
            # one source, as a mount is, and allows none.
            yield (
                f"      ! {deep}/ sits too deep to load — a subagent's skills sit "
                f"directly in its folder"
            )
        for hidden in found.shadowed.get(name, ()):
            # Shadowing is only acceptable while it is visible.
            yield f"      {hidden}  [private tool, shadowing the catalogue's]"
    if found.compiled_subagents:
        # deepagents runs a compiled graph as given, without our allowlist, so a
        # grant there is not a limit.
        yield "  (a compiled subagent brings its own graph: a tools: grant narrows"
        yield "   what it is handed, not what it can call)"
    if not found.subagents:
        yield f"  (none)  — try {SEED_HINT}"
    for folder in found.orphaned_assets:
        # Its subagent still lists and runs, holding none of this, and nothing else says so.
        owner = PurePath(folder).name
        yield (
            f"  ! {folder}/ holds tools/ or skills/ that reach no subagent "
            f"— nothing in it is named {owner}"
        )
        yield "    (rename the folder, or the `name:` inside it, so the two match)"


def _audience_json(audience: Audience) -> list[object]:
    """One audience as JSON: names, and a conjunction as a nested list."""
    if audience == ALL:
        return [ALL]
    return [sorted(one) if isinstance(one, frozenset) else one for one in audience]


def _grants_json(by_name: Mapping[str, Grant]) -> dict[str, dict[str, object]]:
    """Each definition's grant as JSON, its star a flag beside the names."""
    return {
        name: {"every": grant.every, "names": list(grant.names)}
        for name, grant in by_name.items()
    }


def as_json(found: Inventory) -> dict[str, object]:
    """The same answer, in the shape a script can read."""
    return {
        "origins": origins_document(found.origins),
        "agents": dict(found.agents),
        "agent_sources": dict(found.agent_sources),
        "agent_subagents": _grants_json(found.agent_subagents),
        "agent_tools": _grants_json(found.agent_tools),
        "agent_builtin_tools": _grants_json(found.agent_builtin_tools),
        "agent_gates": {name: list(gates) for name, gates in found.agent_gates.items()},
        "agents_error": found.agents_error,
        "builtin_tools": list(found.builtin_tools),
        "tools": list(found.tools),
        "tool_sources": dict(found.tool_sources),
        "tools_error": found.tools_error,
        "build_error": found.build_error,
        "skills": dict(found.skills),
        "skills_unloadable": list(found.skills_unloadable),
        "skills_misplaced": list(found.skills_misplaced),
        "skills_misfiled": [list(pair) for pair in found.skills_misfiled],
        "skills_enabled": found.skills_enabled,
        "subagents": dict(found.subagents),
        "subagent_sources": dict(found.subagent_sources),
        "subagent_tools": _grants_json(found.subagent_tools),
        "subagent_builtin_tools": _grants_json(found.subagent_builtin_tools),
        "compiled_subagents": list(found.compiled_subagents),
        "subagents_error": found.subagents_error,
        "middlewares": dict(found.middlewares),
        "middlewares_error": found.middlewares_error,
        "moved_tools": {k: list(v) for k, v in found.moved_tools.items()},
        "moved_tools_checked": found.moved_tools_checked,
        "unresolved_subagents": {k: list(v) for k, v in found.unresolved_subagents.items()},
        "unresolved_subagents_checked": found.unresolved_subagents_checked,
        "doubled_skills": dict(found.doubled_skills),
        "doubled_skills_checked": found.doubled_skills_checked,
        "bundled_tools": {k: list(v) for k, v in found.bundled_tools.items()},
        "bundled_skills": {k: list(v) for k, v in found.bundled_skills.items()},
        "bundled_misplaced": {k: list(v) for k, v in found.bundled_misplaced.items()},
        "carried_bundles": list(found.carried_bundles),
        "stranded_skills": {k: list(v) for k, v in found.stranded_skills.items()},
        "shadowed": {k: list(v) for k, v in found.shadowed.items()},
        "shadowed_checked": found.shadowed_checked,
        "builtin_tools_checked": found.builtin_tools_checked,
        "bundles_error": found.bundles_error,
        "orphaned_assets": list(found.orphaned_assets),
        "miscounted_bundles": dict(found.miscounted_bundles),
        "miscounted_bundles_checked": found.miscounted_bundles_checked,
        # Each name a lock uses and the definitions using it, or `null` where nothing is
        # locked; who reaches what is `audiences`.
        "vocabulary": None
        if found.vocabulary is None
        else {
            "source_ids": {name: list(who) for name, who in found.vocabulary.source_ids.items()},
            "datazones": {zone: list(who) for zone, who in found.vocabulary.datazones.items()},
        },
        "audiences": {
            kind: {
                name: {
                    "source_ids": _audience_json(stated.source_ids),
                    **({} if stated.datazone is None else {"datazone": stated.datazone}),
                    **(
                        {"skills": {e: _audience_json(who) for e, who in stated.skills.items()}}
                        if stated.skills
                        else {}
                    ),
                }
                for name, stated in definitions.items()
            }
            for kind, definitions in found.audiences.items()
        },
        "access_report": {
            "unrestricted": [list(pair) for pair in found.access_report.unrestricted],
            "narrowed": [list(pair) for pair in found.access_report.narrowed],
        },
        "held": None if found.held is None else sorted(found.held),
    }


def failed(found: Inventory) -> bool:
    """Whether the listing described a workspace that does not mean what it says.

    Not only one that will not load: an agent naming a moved tool loads and runs without
    it, and one naming an unknown subagent loads and refuses every run reaching it.
    """
    if found.moved_tools or found.miscounted_bundles or found.unresolved_subagents:
        return True
    # Not a kind, so the walk below misses it; without a graph nothing runs.
    if found.build_error is not None:
        return True
    if any(kind.error is not None for kind in found.by_kind()):
        return True
    # Not a kind either, and its tools are Python that has to import.
    return found.bundles_error is not None
