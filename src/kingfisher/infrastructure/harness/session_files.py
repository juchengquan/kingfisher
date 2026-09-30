"""A session's files as kingfisher reaches them: through the backend the agent runs on.

Never through the session directory. A backend that runs somewhere else holds the
session there, and a directory on this host is then a place the agent never sees --
a caller's files placed where the agent cannot read them, and a turn that wrote a
report handing back nothing.
"""

from __future__ import annotations

import logging
from pathlib import Path, PurePosixPath
from typing import Any

from deepagents.backends import CompositeBackend, FilesystemBackend

from kingfisher.config import Config, SessionKey
from kingfisher.domain.result import ArtifactError
from kingfisher.infrastructure.harness.backend import DataBackend, SessionClaims, default_backend
from kingfisher.infrastructure.sandbox import confinement
from kingfisher.infrastructure.signing import SIGNATURE, sign, verify
from kingfisher.infrastructure.workspace.placement import DataError, DataPlacement, checked
from kingfisher.layout import ARTIFACT_DIRS, DATA_ROUTE, HARNESS_ROUTE

_log = logging.getLogger(__name__)


class LocalFiles(SessionClaims, CompositeBackend):
    """A session's directory on this host, for a graph kingfisher did not build.

    Such a graph carries its own backend and nothing hands it over, so the session
    directory is the only place kingfisher knows to look. `/data` keeps its own
    route because it is read-only on disk and only `DataBackend` opens it.
    """

    def __init__(self, session_dir: Path) -> None:
        super().__init__(
            default=FilesystemBackend(root_dir=str(session_dir)),
            routes={DATA_ROUTE: DataBackend(session_dir)},
        )
        self._session_dir = Path(session_dir)
        #: What `protect_data` could not harden, for the turn to report.
        self.unprotected: tuple[str, ...] = ()


def local_files(session_dir: Path) -> LocalFiles:
    """See `LocalFiles`."""
    return LocalFiles(session_dir)


def place_data(sources: tuple[Path, ...], backend: Any) -> DataPlacement:
    """Copy caller-supplied files into a session's `/data`, through its backend."""
    if not sources:
        return DataPlacement()
    # Before anything is read or sent: a request naming a file that is not there
    # must fail without having placed the ones that were.
    seen = checked(sources)
    listing = backend.ls(DATA_ROUTE)
    existing = {PurePosixPath(entry["path"]).name for entry in listing.entries or ()}
    answers = backend.upload_files(
        [(f"{DATA_ROUTE}{name}", source.read_bytes()) for name, source in seen.items()]
    )
    if refused := [f"{answer.path}: {answer.error}" for answer in answers if answer.error]:
        msg = f"the session's backend refused {', '.join(refused)}"
        raise DataError(msg)
    return DataPlacement(
        placed=tuple(seen),
        replaced=tuple(name for name in seen if name in existing),
    )


def collect_artifacts(backend: Any) -> tuple[str, ...]:
    """What this session holds that is worth keeping, relative to the session.

    A walk of what is there rather than a record of tool calls: `execute`
    bypasses the file tools, and running a script is how most of `/derived` gets
    produced.
    """
    found: list[str] = []
    for name in ARTIFACT_DIRS:
        result = backend.glob("**", path=f"/{name}/")
        if result.error or result.truncated:
            # Reported rather than raised. The turn has already run, and failing it
            # here would lose its answer over a listing of what it left behind.
            _log.warning(
                "artifacts under /%s/ may be incomplete: %s",
                name,
                result.error or "the listing was cut short",
            )
        found.extend(
            match["path"].lstrip("/")
            for match in result.matches or ()
            if not match.get("is_dir")
        )
    return tuple(sorted(found))


def read_artifact(backend: Any, name: str) -> bytes:
    """One file a turn produced, as bytes, by the name `collect_artifacts` gave it.

    Only under `ARTIFACT_DIRS`. The same backend reaches `/.harness` and `/data`,
    and a caller who may read what a turn produced may not read what kingfisher
    keeps about the session through the same door.
    """
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or ".." in path.parts
        or not path.parent.parts
        or path.parts[0] not in ARTIFACT_DIRS
    ):
        msg = f"{name!r} is not an artifact: name a file under {', '.join(ARTIFACT_DIRS)}"
        raise ArtifactError(msg)
    (answer,) = backend.download_files([f"/{path}"])
    if answer.error or answer.content is None:
        msg = f"{name}: {answer.error or 'no content'}"
        raise ArtifactError(msg)
    return answer.content


def key_needed(cfg: Config, *, backend: Any, graph: Any, runner: Any) -> str | None:
    """Why this deployment must sign what it keeps about a session, or `None`.

    `None` only where kingfisher itself keeps the shell away from `.harness`, and
    only where it can see that it does. Anything it cannot see into counts as
    needing a key: being unsure is the case a signature is for.
    """
    if graph is not None:
        return "it supplies a pre-built graph, and kingfisher cannot see the backend inside it"
    if backend is not default_backend:
        # Identity, not the type of what it returns: a factory wrapping
        # `default_backend` can hand back its result with anything changed, and
        # kingfisher cannot tell that without trusting the wrapper.
        return "its backend factory is not default_backend itself"
    if runner is not None:
        return "it supplies a CommandRunner, which may run commands outside kingfisher's sandbox"
    return confinement.harness_unfenced(cfg)


class HarnessFiles:
    """What kingfisher keeps about one session under `/.harness`, through its backend.

    Signed wherever a key is set, and verified on every read. The agent's shell
    reaches the same backend, and on one kingfisher does not fence nothing else
    stops it rewriting its own pinned agent or conversation.
    """

    def __init__(self, backend: Any, session_id: str, key: SessionKey | None) -> None:
        self._backend = backend
        self._session_id = session_id
        self._key = key

    def read(self, name: str) -> bytes | None:
        """`name`'s content, or `None` where the session has none."""
        content, signature = self._download(name, f"{name}{SIGNATURE}")
        if content is not None and self._key is not None:
            verify(self._key, self._session_id, name, content, signature)
        return content

    def write(self, name: str, content: bytes) -> None:
        """Replace `name`, and its signature with it."""
        files = [(f"{HARNESS_ROUTE}{name}", content)]
        if self._key is not None:
            files.append(
                (
                    f"{HARNESS_ROUTE}{name}{SIGNATURE}",
                    sign(self._key, self._session_id, name, content),
                )
            )
        refused = [a for a in self._backend.upload_files(files) if a.error]
        if refused:
            kept = ", ".join(f"{a.path}: {a.error}" for a in refused)
            msg = f"the session's backend would not keep {kept}"
            raise OSError(msg)

    def delete(self, *names: str) -> None:
        """Drop these and their signatures. Safe where they were never written."""
        paths = [
            f"{HARNESS_ROUTE}{one}" for name in names for one in (name, f"{name}{SIGNATURE}")
        ]
        for path in paths:
            self._backend.delete(path)
        # Asked again rather than trusting each answer: a backend reports a file that
        # was never there as an error, and telling that from a delete that failed
        # would mean reading its wording.
        left = [a.path for a in self._backend.download_files(paths) if a.error is None]
        if left:
            msg = f"the session's backend kept {', '.join(left)} after deleting it"
            raise OSError(msg)

    def _download(self, *names: str) -> list[bytes | None]:
        answers = self._backend.download_files([f"{HARNESS_ROUTE}{name}" for name in names])
        found: list[bytes | None] = []
        for answer in answers:
            if answer.error == "file_not_found":
                found.append(None)
            elif answer.error is not None:
                msg = f"the session's backend could not read {answer.path}: {answer.error}"
                raise OSError(msg)
            else:
                found.append(answer.content)
        return found
