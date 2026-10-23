"""doctor's checks on the definitions, read off one `Inventory`."""

from __future__ import annotations

from collections.abc import Iterator

from kingfisher import Config, Inventory

from .check import Check


def _catalogues(found: Inventory) -> Iterator[Check]:
    """Each definition directory, every one of which can fail on its own."""
    for kind in found.by_kind():
        # Skills load one at a time and have no single error, so their row comes from
        # `_subagent_tools` instead.
        if kind.name == "skills":
            continue
        if kind.error is not None:
            yield Check(
                kind.name, "fail", kind.error, f"fix or remove the {kind.written_as} it names"
            )
            continue
        # Built-ins on the tools row: they are not a catalogue, and a reader counting
        # grants wants both numbers in one line. "unknown" where a broken middleware
        # took the probe down, since `0 built in` beside an `ok` would claim none.
        built_in = (
            f"{len(found.builtin_tools)} built in"
            if found.builtin_tools_checked
            else "built-in set unknown -- see middlewares"
        )
        detail = (
            f"{kind.defined} offered, {built_in}"
            if kind.name == "tools"
            else f"{kind.defined} defined"
        )
        yield Check(kind.name, "ok", detail)


def _tool_references(found: Inventory) -> Iterator[Check]:
    """A definition naming a tool by a path it no longer lives at.

    A failure, because an agent's is refused nowhere: it runs without the granted tool and
    nothing else says so. A subagent's is refused at startup, and this says it earlier.
    """
    if not found.moved_tools_checked:
        yield Check(
            "tool references",
            "warn",
            "not checked -- a catalogue it reads did not load, which is above",
        )
        return
    if not found.moved_tools:
        yield Check("tool references", "ok", "every definition names tools where they are")
        return
    for subject, names in sorted(found.moved_tools.items()):
        yield Check(
            "tool references",
            "fail",
            f"{subject} names {', '.join(names)} by a path they have moved from",
            "update the definition, or drop the path and write the name alone",
        )


def _subagent_references(found: Inventory) -> Iterator[Check]:
    """A definition naming a subagent the catalogue has no key for.

    A failure though startup refuses nothing: every run reaching the name is
    refused, which for an agent is every run the request does not narrow it out of.
    """
    if not found.unresolved_subagents_checked:
        yield Check(
            "subagent references",
            "warn",
            "not checked -- a catalogue it reads did not load, which is above",
        )
        return
    if not found.unresolved_subagents:
        yield Check("subagent references", "ok", "every definition names subagents there are")
        return
    for subject, names in sorted(found.unresolved_subagents.items()):
        yield Check(
            "subagent references",
            "fail",
            f"{subject} names {', '.join(names)}, which matches no subagent here",
            "write each as `kingfisher list` lists it under subagents, where a name two "
            "files share is spelt with its file",
        )


def _tool_names(found: Inventory) -> Iterator[Check]:
    """A definition naming a tool its line does not offer.

    A failure though startup refuses nothing, as for a subagent: every run reaching it is
    refused, whichever caller is asking.
    """
    if not found.unresolved_tools_checked:
        yield Check(
            "tool names",
            "warn",
            "not checked -- the tools or the built-in set did not load, which is above",
        )
        return
    if not found.unresolved_tools:
        yield Check("tool names", "ok", "every definition names tools there are")
        return
    for subject, names in sorted(found.unresolved_tools.items()):
        yield Check(
            "tool names",
            "fail",
            f"{subject} names {', '.join(names)}, which nothing offers on that line",
            "write each as `kingfisher list` lists it under builtin tools or offered "
            "tools, on the line that heading names, or add the tool it means",
        )


def _skill_entries(found: Inventory) -> Iterator[Check]:
    """A definition naming one skill twice, bare and qualified.

    A failure because `warm` refuses it: a deployment carrying one does not start.
    """
    if not found.doubled_skills_checked:
        yield Check(
            "skill entries",
            "warn",
            "not checked -- a catalogue it reads did not load, which is above",
        )
        return
    if not found.doubled_skills:
        yield Check("skill entries", "ok", "every definition names each skill once")
        return
    for subject, complaint in sorted(found.doubled_skills.items()):
        yield Check(
            "skill entries",
            "fail",
            f"{subject} {complaint}",
            "keep one entry, the one carrying the audience you mean",
        )


def _bundled_entries(found: Inventory) -> Iterator[Check]:
    """A definition's `source: bundled` entries, against the folder they come from.

    A failure because `warm` refuses it: a deployment carrying one does not start.
    """
    if not found.miscounted_bundles_checked:
        yield Check(
            "bundled entries",
            "warn",
            "not checked -- the subagents or a subagent's own folder did not load; its "
            "own row says why",
        )
        return
    if not found.miscounted_bundles:
        yield Check("bundled entries", "ok", "every subagent lists exactly its own folder")
        return
    for name, complaint in sorted(found.miscounted_bundles.items()):
        yield Check(
            "bundled entries",
            "fail",
            f"subagent {name!r}: {complaint}",
            "list each file in the folder as source: bundled, or move it out",
        )


def _subagent_tools(found: Inventory) -> Iterator[Check]:
    """What a subagent brings itself, which is not a definition directory."""
    # A bundle that will not import stops the whole deployment -- `warm` reads every
    # bundle before a caller is named -- so it is no leak to filter from a scoped
    # caller. Its own row, not folded into `tools`, so it names the subagent to open.
    if found.bundles_error is not None:
        yield Check(
            "subagent tools", "fail", found.bundles_error, "fix or remove the module it names"
        )

    # `ok`: installing a package was a decision, with nothing to fix. Said because a
    # carried bundle's tools run on this deployment's credentials, unreviewed here.
    if found.carried_bundles:
        yield Check(
            "subagent tools",
            "ok",
            f"carried with the definition rather than kept in the shared catalogue: "
            f"{', '.join(found.carried_bundles)}",
        )

    # A warning: nothing is broken, but shadowing is only acceptable while it is visible.
    if not found.shadowed_checked:
        # Said, or a broken `tools/` would read as no shadowing rather than not compared.
        yield Check(
            "subagent tools",
            "warn",
            "shadowing not checked -- the offered tool catalogue did not load; its "
            "own row says why",
        )
    elif found.shadowed:
        answering = ", ".join(
            f"{name} ({', '.join(names)})" for name, names in sorted(found.shadowed.items())
        )
        yield Check(
            "subagent tools",
            "warn",
            f"answered by a subagent's own instead of the catalogue's: {answering}",
            "rename one side if the subagent was meant to get the catalogue's",
        )

    # A warning: a folder naming no definition is legal organisation, but the likeliest
    # cause is a renamed definition whose subagent then silently holds none of it. Not
    # `subagent tools`, because the folder may hold only `skills/`.
    if found.orphaned_assets:
        yield Check(
            "subagent bundles",
            "warn",
            # No verb for the count to agree with: one orphan is usual, a rename makes more.
            f"{', '.join(f'{one}/' for one in found.orphaned_assets)}: "
            f"tools/ or skills/ that reach no subagent",
            "rename the folder, or the `name:` inside it, so the two match",
        )

    # A warning: everything runs without the skill, and nothing else says it is missing.
    if found.bundled_misplaced:
        deep = "; ".join(
            f"{name}: {', '.join(f'{one}/' for one in ones)}"
            for name, ones in sorted(found.bundled_misplaced.items())
        )
        yield Check(
            "subagent skills",
            "warn",
            f"too deep in a subagent's own folder to load: {deep}",
            "move each to sit directly in the subagent's skills folder",
        )

    detail = f"{len(found.skills)} loadable"
    if not found.skills_enabled:
        detail += ", and KINGFISHER_SKILLS_ENABLED is off so none will be offered"
    # `misfiled` is not hidden -- the agent has it -- but a caller granting it by its
    # directory name is told there is no such skill.
    hidden = found.skills_unloadable + found.skills_misplaced
    if found.skills_misfiled:
        yield Check(
            "skill names",
            "warn",
            f"{len(found.skills_misfiled)} offered under a name their directory "
            f"does not have: "
            + ", ".join(f"{d}/ as {n}" for d, n in found.skills_misfiled),
            "grant them by the name shown, or rename the directory to match",
        )
    if hidden:
        yield Check(
            "skills",
            "warn",
            f"{detail}; {len(hidden)} present and invisible to the agent: {', '.join(hidden)}",
            "run `kingfisher list` for why each one is not loadable",
        )
    else:
        yield Check("skills", "ok", detail)


def _definitions(cfg: Config, found: Inventory) -> Iterator[Check]:
    """Which definitions this deployment cannot actually run."""
    # Only when both catalogues parsed: these read the same files, so a broken one would
    # raise out of here and end the diagnosis. Its own row already reports it.
    unread = [
        kind
        for kind, failed in (("agent", found.agents_error), ("subagent", found.subagents_error))
        if failed is not None
    ]
    if unread:
        yield Check(
            "definitions run",
            "warn",
            f"not checked -- the {' and '.join(unread)} "
            f"{'catalogue' if len(unread) == 1 else 'catalogues'} did not load, "
            "which is above",
        )
        return

    # Here, not at module scope: it loads deepagents, which every other verb would pay.
    from kingfisher.core.agent.activation import (  # noqa: PLC0415
        unrunnable_agents,
        unrunnable_subagents,
    )

    # Agents too: one that cannot start is what `doctor --help` says this catches.
    unrunnable = (*unrunnable_agents(cfg), *unrunnable_subagents(cfg))
    if not unrunnable:
        yield Check("definitions run", "ok", "every definition resolves to a model")
        return
    for name, why in unrunnable:
        yield Check(
            f"definition {name!r}",
            "fail",
            why,
            "set the credential it needs, or name a model you can run",
        )


def _buildable(found: Inventory) -> Iterator[Check]:
    """Whether a graph could be assembled at all, and why not."""
    if found.build_error is None:
        return
    yield Check(
        "agent build",
        "fail",
        found.build_error,
        "fix what it names -- a models.yaml row, a backend, or a mount",
    )


def checks(cfg: Config, found: Inventory) -> Iterator[Check]:
    """These, in the order `examine` reports them."""
    # Before the catalogues: without a graph the built-in set is unknown, not empty.
    yield from _buildable(found)
    yield from _catalogues(found)
    yield from _tool_references(found)
    yield from _tool_names(found)
    yield from _subagent_references(found)
    yield from _skill_entries(found)
    yield from _bundled_entries(found)
    yield from _subagent_tools(found)
    yield from _definitions(cfg, found)
