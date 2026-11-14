"""Keeping the saved results of the turns a rewind left behind out of the agent's reach, and
bringing them back when a rewind returns to those turns."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from kingfisher.core.layout import HARNESS_ROUTE, OFF_PATH_RESULTS, RESULTS_MOVING
from kingfisher.core.logged import warn

from .copying import copied_through
from .steps import Steps, changing, reading

if TYPE_CHECKING:
    from kingfisher.core.layout import Layout

    from .store import HarnessFiles, KeptLedger

#: Where a saved result of a turn off the session's path is kept, as the backend names it.
OFF_PATH_RESULTS_ROUTE = f"{HARNESS_ROUTE}{OFF_PATH_RESULTS}/"


def results_moving(harness: HarnessFiles) -> Steps[None]:
    """Say that the results folders may not match the turns off the path until
    `results_placed` has run to the end."""
    yield from harness.store(RESULTS_MOVING, b"true")


def results_unsettled(harness: HarnessFiles) -> Steps[bool]:
    """Whether a move of results was begun and never finished."""
    return (yield from harness.fetch(RESULTS_MOVING)) is not None


def results_placed(
    files: Any,
    harness: HarnessFiles,
    layout: Layout,
    ledger: KeptLedger,
    off_path: frozenset[str],
) -> Steps[str | None]:
    """Move each saved result whose row's turn is in `off_path` out of the tool results
    folder, and each whose turn is not back into it; `None` once every one is where it
    belongs, else why one is not.

    Every row of every branch, so a rewind forward finds what an earlier one moved. A file
    no row names is the sweep's, and stays where it is.
    """
    shown_route = layout.tool_results_route
    hide: dict[str, bool] = {}
    for exchange in (*ledger.all_exchanges, *ledger.all_unclosed):
        for entry in exchange:
            if entry.path is not None and entry.path.startswith(shown_route):
                hide[entry.path.removeprefix(shown_route)] = entry.turn in off_path
    shown = yield from _names_in(files, shown_route)
    hidden = yield from _names_in(files, OFF_PATH_RESULTS_ROUTE)
    if shown is None or hidden is None:
        return "a results folder could not be listed"
    failed: str | None = None
    for name, hiding in sorted(hide.items()):
        source, target, found = (
            (shown_route, OFF_PATH_RESULTS_ROUTE, shown)
            if hiding
            else (OFF_PATH_RESULTS_ROUTE, shown_route, hidden)
        )
        if name not in found:
            continue
        # Copied whole even where the target is there already: a move that stopped partway
        # left it cut, and the source, removed only after a whole copy, is the one to trust.
        _, refused, _ = yield from copied_through(files, f"{source}{name}", f"{target}{name}", None)
        if refused is None:
            removed = yield changing(files, "delete", f"{source}{name}")
            refused = getattr(removed, "error", None)
        if refused is not None:
            warn(
                "result_not_moved",
                f"saved result {name} could not be moved to {target}: {refused}",
                path=f"{source}{name}",
                reason=str(refused),
            )
            failed = failed or str(refused)
    if failed is None:
        yield from harness.drop(RESULTS_MOVING)
    return failed


def _names_in(files: Any, route: str) -> Steps[set[str] | None]:
    """The names of the files in `route`, none where it is not there, or `None` where it
    could not be listed."""
    listing = yield reading(files, "ls", route)
    if listing.entries is None:
        # deepagents' code for a folder that is not there, which the hidden one is until a
        # rewind first leaves a result behind.
        return set() if "path_not_found" in str(listing.error) else None
    return {PurePosixPath(one["path"]).name for one in listing.entries if not one.get("is_dir")}
