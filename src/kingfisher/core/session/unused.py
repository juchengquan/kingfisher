"""The saved tool results a turn found nothing naming, as it hands them to an
`UnusedResults`, and kingfisher's own, which removes them."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from kingfisher.core.interfaces import UnusedResults

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class UnusedFile:
    """One saved result no ledger row names."""

    #: Where it is in the session, as the agent's file tools name it.
    path: str
    #: Its length in bytes as the session's backend listed it, or `None` where the
    #: listing did not say.
    size: int | None
    _found: UnusedFiles = field(repr=False, compare=False)

    def read(self) -> bytes:
        """Its contents. `FileNotFoundError` where the backend has none to give."""
        return self._found._read(self.path)

    def remove(self) -> None:
        """Ask the session's backend to remove it. Already gone is not an error, and one
        the backend would not remove is handed over again by the next turn."""
        self._found._remove(self.path)


class UnusedFiles:
    """The saved results one turn judged safe to drop, and nothing else of the session.

    Iterate it for each `UnusedFile`. The backend stays private and every path is checked
    against the set: a plugin handed this is handed those files, not the session.
    """

    def __init__(
        self, files: Any, listed: Iterable[tuple[str, int | None]], *, session_id: str
    ) -> None:
        self._files = files
        self._session_id = session_id
        self._each = tuple(UnusedFile(path, size, self) for path, size in listed)
        self._paths = frozenset(one.path for one in self._each)
        self._removed: set[str] = set()

    def __iter__(self) -> Iterator[UnusedFile]:
        return iter(self._each)

    def __len__(self) -> int:
        return len(self._each)

    def __repr__(self) -> str:
        return f"UnusedFiles({self._session_id!r}, {[one.path for one in self._each]!r})"

    @property
    def session_id(self) -> str:
        """The session they are in. A path is unique within a session, not across them."""
        return self._session_id

    @property
    def _left(self) -> int:
        """How many of them nothing has asked to remove."""
        return len(self._paths - self._removed)

    def _one_of_them(self, path: str) -> str:
        # Checked here, not trusted to the `UnusedFile`: `dataclasses.replace` makes one
        # naming any path at all, and the backend reaches the whole session.
        if path not in self._paths:
            msg = f"{path!r} is not one of the unused results this turn found"
            raise ValueError(msg)
        return path

    def _read(self, path: str) -> bytes:
        (answer,) = self._files.download_files([self._one_of_them(path)])
        if answer.error or answer.content is None:
            msg = f"{path}: {answer.error or 'no content'}"
            raise FileNotFoundError(msg)
        return answer.content

    def _remove(self, path: str) -> None:
        # Its answer is not checked: a file left behind is handed over by the next turn.
        self._files.delete(self._one_of_them(path))
        self._removed.add(path)


class DeleteUnusedResults:
    """`UnusedResults` that removes every file it is handed."""

    def settle(self, found: UnusedFiles) -> None:
        for one in found:
            one.remove()


def settle_unused(plugin: UnusedResults, found: UnusedFiles) -> None:
    """Hand `found` to `plugin`, logging rather than raising what it raises.

    The turn has run and its answer is kept: failing it now would lose that answer over
    files the next turn offers again.
    """
    try:
        plugin.settle(found)
    except Exception:  # noqa: BLE001 -- a turn outlives a failing plugin, as a sink
        _log.warning(
            "session %s: the unused results plugin raised; %d of %d file(s) left for "
            "the next turn",
            found.session_id,
            found._left,
            len(found),
            exc_info=True,
        )
