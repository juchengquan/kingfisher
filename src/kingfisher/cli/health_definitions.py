"""doctor's checks on what the workspace defines, read off one `Inventory`."""

from __future__ import annotations

from collections.abc import Iterator

from kingfisher import Config, Inventory
from kingfisher.cli.check import Check


def _catalogues(found: Inventory) -> Iterator[Check]:
    """Each definition directory, every one of which can fail on its own.

    It read three of the five for some time, and `Inventory` had the answer for a
    fourth the whole while: `agents_error` and `bundles_error` are computed on every
    walk and were read by `kingfisher list` and by nothing here. A broken agent file
    passed `doctor` and stopped `Kingfisher(...)` dead.

    The fifth was worse: `middlewares` was computed nowhere at all, because nothing
    read that directory until a definition named one. A module that would not import
    was invisible to every check a deployment has, and surfaced on the first request
    activating an agent that named it.
    """
    for kind in found.by_kind():
        # Skills are the one kind with no single error to report, so `_skills` gives
        # them a check of their own -- see `Kind.error`. Here that would be a line
        # saying the catalogue loaded, about a catalogue that loads one skill at a
        # time.
        if kind.name == "skills":
            continue
        if kind.error is not None:
            yield Check(
                kind.name, "fail", kind.error, f"fix or remove the {kind.written_as} it names"
            )
            continue
        # The built-ins beside the kind rather than as one of their own: they are
        # not a catalogue, they come with deepagents, and a reader counting what a
        # request may grant wants both numbers in one line.
        # "unknown" rather than a count, where the built-in set could not be read: a
        # middleware that will not import takes the probe down with it, and `0 built
        # in` beside an `ok` said this deployment had none where it has ten. The
        # `FAIL` on the middlewares row above is the fault; this is the consequence.
        built_in = (
            f"{len(found.builtin_tools)} built in"
            if found.builtin_tools_checked
            else "built-in set unknown -- see middlewares"
        )
        detail = (
            f"{kind.defined} in the workspace, {built_in}"
            if kind.name == "tools"
            else f"{kind.defined} defined"
        )
        yield Check(kind.name, "ok", detail)


def _tool_references(found: Inventory) -> Iterator[Check]:
    """A definition naming a tool by a path it no longer lives at.

    A failure for both kinds, and they arrive here by different roads. A subagent's
    is refused at startup, so this only ever says early what the deployment would say
    late. An agent's is refused nowhere at all: it starts, runs, and quietly does not
    have the tool its author granted it -- no error, no warning, and no other symptom.
    That second one is why this is a failure rather than a warning.
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


def _bundled_entries(found: Inventory) -> Iterator[Check]:
    """A definition's `source: bundled` entries, against the folder they come from.

    Beside `_tool_references` rather than in `_subagent_tools`, because it is the same
    kind of thing: a definition saying where something is, and the check being whether
    it is still there. A failure because `warm` refuses it -- a deployment carrying one
    does not start, and this check exists to say that before the deployment does.
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
    """What a subagent brings itself, which is not a definition directory.

    Split from `_catalogues` when the fifth kind arrived and took it past its branch
    budget. The seam was already there: above is one check per directory a deployment
    authors, and this is what one subagent carries inside its own folder.
    """
    # A subagent's private tools are Python like any other, and a bundle that will not
    # import stops the whole deployment: `warm` reads every bundle and the service
    # constructor calls it, before any request has named a caller. This said it stopped
    # "the request that activates that subagent rather than the deployment", which is
    # the shape `warm` was written to prevent -- and a reader believing it would then
    # read the error reaching a scoped caller as a leak and filter it away, taking with
    # it the reason that caller's own run refuses to start.
    #
    # Its own check rather than folded into `tools`, for the reason `bundles_error` is
    # its own field: a reader has to be told which subagent to go and open. That is
    # what the message says now; it used to be a bare `probe.py`.
    if found.bundles_error is not None:
        yield Check(
            "subagent tools", "fail", found.bundles_error, "fix or remove the module it names"
        )

    # An `ok` and not a warning: installing a package is a decision somebody made,
    # and there is nothing to fix. It is said at all because the names alone do not
    # say where the code is -- a carried bundle's tools run in this sandbox on this
    # deployment's credentials and are not files anybody here reviewed.
    if found.carried_bundles:
        yield Check(
            "subagent tools",
            "ok",
            f"carried with the definition rather than kept in this workspace: "
            f"{', '.join(found.carried_bundles)}",
        )

    # A warning and never a failure, because nothing is broken: the subagent
    # answers with its own and the catalogue's never reaches it. That is a
    # decision somebody made, and it is only acceptable while it is visible --
    # which is the same reason `kingfisher list` prints it.
    if not found.shadowed_checked:
        # Said rather than passed over: without this row a broken `tools/` reads as
        # "no subagent shadows the catalogue", which is a different claim from "the
        # catalogue this would be compared against did not load".
        yield Check(
            "subagent tools",
            "warn",
            "shadowing not checked -- the workspace tool catalogue did not load; its "
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

    # A warning rather than a failure for the reason the catalogue refuses
    # nothing here: a folder naming no definition is legal organisation, and a
    # folder grouping definitions by theme is one. It is worth saying anyway
    # because the likeliest cause is the other thing entirely -- a definition
    # renamed out from under its own folder, whose subagent then holds none of
    # what is in it and says nothing about that at any point in a run.
    #
    # Its own name rather than `subagent tools`: the folder may hold only
    # `skills/`, and a doctor row that names the wrong half sends a reader to
    # look for a tool that was never there.
    if found.orphaned_assets:
        yield Check(
            "subagent bundles",
            "warn",
            # Phrased without a verb the count has to agree with: one orphan is
            # the common case and a rename can produce several.
            f"{', '.join(f'{one}/' for one in found.orphaned_assets)}: "
            f"tools/ or skills/ that reach no subagent",
            "rename the folder, or the `name:` inside it, so the two match",
        )

    # A warning, as the catalogue's own misplaced skills are: everything starts and the
    # subagent works without the skill. Said because nothing else would -- the skill is
    # simply not among the ones the subagent is shown.
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
    # `misfiled` is not hidden -- the agent has it. It is a warning of its own
    # because the failure is a caller typing the directory name and being told
    # there is no such skill.
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


def _where(cfg: Config, found: Inventory) -> Iterator[Check]:
    """A catalogue read from somewhere other than the configuration says."""
    for kind in found.by_kind():
        origin = kind.origin
        if origin.kind == "overridden":
            yield Check(
                f"{kind.name} directory",
                "warn",
                f"read from {origin.path}, while the configuration names "
                f"{cfg.catalogue_roots[kind.name]} — a catalogue was supplied when this "
                f"kingfisher was built, and KINGFISHER_DEFINITIONS_DIR does nothing for it",
                "point KINGFISHER_DEFINITIONS_DIR at what is actually read",
            )


def _definitions(cfg: Config, found: Inventory) -> Iterator[Check]:
    """Which definitions this deployment cannot actually run.

    Imported inside the function. `unrunnable_subagents` reaches deepagents as it
    loads -- 868ms and 3,137 modules, measured -- and at module scope every other
    verb would pay it, so `kingfisher help` would cost a second to print text. The
    CLI starts in 40ms and should keep doing so.
    """
    # Asked only when the catalogues parsed. These read the same files, so a definition
    # that will not load raises out of here instead of being reported -- and a
    # diagnosis that stops at the first problem is what this command exists to replace.
    # The checks above already said so, so this one says nothing rather than twice.
    #
    # Both kinds, since the agent half of the question is asked now: with only the
    # subagent catalogue guarded, a workspace holding one unreadable agent file raised
    # `AgentError` out of `doctor` itself, which
    # `test_a_broken_agent_catalogue_is_a_failure_too` is what noticed.
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

    from kingfisher.core.agent.activation import (  # noqa: PLC0415
        unrunnable_agents,
        unrunnable_subagents,
    )

    # Both kinds. This asked only about subagents, so the row said "every definition
    # resolves to a model" and exited 0 while the workspace's one agent could not
    # start -- `run --agent` refusing with the configuration error the row had just
    # promised was absent, which is the one thing `--help` says this verb catches.
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
    """Whether a graph could be assembled at all, and why not.

    A row only on failure, like `subagent tools`: the things a reader needs listed
    every time are what the deployment *has*, and this is the absence of all of it.

    It exists because the refusal used to escape `inventory` instead. One
    `models.yaml` row that leaves no room for a conversation took `doctor`, `list`,
    `sessions` and `reap` down together -- exit 2 with no output from any of them --
    so the deployment's own diagnostics were the first thing it broke.
    """
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
    # Before the catalogues, because it explains their shape: without a graph the
    # built-in set is unknown rather than empty, and the row below says so.
    yield from _buildable(found)
    yield from _catalogues(found)
    yield from _tool_references(found)
    yield from _subagent_references(found)
    yield from _bundled_entries(found)
    yield from _subagent_tools(found)
    # After the counts, because it explains one: a zero that is ordinary and
    # a zero that means the path is wrong print the same number.
    yield from _where(cfg, found)
    yield from _definitions(cfg, found)
