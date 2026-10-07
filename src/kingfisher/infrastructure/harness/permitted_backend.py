"""The turn's backend as a caller's tool is handed it: under the rules the file tools keep."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from deepagents.backends import CompositeBackend, FilesystemBackend
from deepagents.backends.protocol import (
    INVALID_PATH,
    PERMISSION_DENIED,
    BackendProtocol,
    DeleteResult,
    EditResult,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)
from deepagents.backends.utils import validate_path
from deepagents.middleware.filesystem import (
    _adelete_target_may_have_descendants,
    _agrep_backend,
    _check_fs_permission,
    _delete_target_may_have_descendants,
    _filter_file_infos_by_permission,
    _filter_grep_matches_by_permission,
    _find_delete_deny_patterns,
    _grep_backend,
)

from kingfisher.domain.references import UnsafeReferenceError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from deepagents import FilesystemPermission
    from deepagents.middleware.filesystem import FilesystemOperation


class PermittedBackend(BackendProtocol):
    """A backend that refuses what this turn's file tools would refuse.

    deepagents applies `permissions=` inside each file tool's body and nowhere else, so
    the backend underneath keeps none of them. Handed to a tool bare, it is a way for
    the model to write into `/skills`, which every session shares, or to read the run
    log under `/.harness`, where `write_file` and `read_file` refuse the same path.

    **deepagents' own helpers, private as they are.** A rule has to mean here what it
    means to `read_file`, and a second matcher written for this class is the copy that
    drifts -- into a path one of the two refuses and the other reads.

    **`BackendProtocol`, not the sandbox one, so there is no `execute`.** No rule
    reaches the shell, and a tool holding it would run commands for a request that was
    refused the `execute` tool.

    What this bounds is where the model can point a tool, not the tool: a tool is code
    in kingfisher's own process and can open whatever it likes.
    """

    def __init__(
        self, backend: BackendProtocol, permissions: Sequence[FilesystemPermission]
    ) -> None:
        self._inner = backend
        # Held, not copied: `build_agent` hands this over before it has finished adding
        # the turn's rules -- memory declined, skills narrowed -- and a copy taken here
        # would check a tool's path against a turn with fewer rules than it has.
        self._permissions = permissions

    @property
    def _rules(self) -> list[FilesystemPermission]:
        # `interrupt` means stop and ask a person, and the asking is the graph pausing
        # before a file tool runs. Nothing pauses for a call made from inside a tool's
        # body, so there is nobody to ask and the rule is a refusal. Rewritten rather
        # than read as written because deepagents' result filters pass `interrupt`
        # entries through -- for them the person has already said yes.
        return [
            replace(rule, mode="deny") if rule.mode == "interrupt" else rule
            for rule in self._permissions
        ]

    def on_this_host(self, path: str) -> Path:
        """Where a tool may be handed `path` as a real file, or `UnsafeReferenceError`.

        The turn's rules first, as `read_file` applies them: a path the file tools may
        not read -- `/.harness`, `/memory` where it was declined -- is refused before
        anything is looked up. Then the backend's own answer to where the path lives,
        so a tool is handed the file the backend would read, and nothing where the
        backend keeps it somewhere this host cannot open.
        """
        where, refused = self._where("read", path)
        if refused:
            raise UnsafeReferenceError(refused)
        mapped = host_path(self._inner, where)
        if mapped is None:
            msg = (
                f"{where} is not kept on this host by this session's backend, so it "
                "cannot be handed to a tool as a file; a tool taking "
                "`runtime: ToolRuntime[ToolContext]` reads it through "
                "`runtime.context.backend` instead"
            )
            raise UnsafeReferenceError(msg)
        root, candidate = mapped
        # Resolved before it is compared, and against the root that answered rather
        # than the session: the agent can make a link, and a link inside a route
        # pointing out of it must not widen what a tool is handed.
        real = candidate.resolve()
        if root is not None and not real.is_relative_to(root.resolve()):
            msg = (
                f"reference {path!r} resolves outside this session; a link inside it "
                "does not widen it"
            )
            raise UnsafeReferenceError(msg)
        return real

    def _where(self, operation: FilesystemOperation, path: str) -> tuple[str, str | None]:
        """The spelling the rules read, and the refusal if there is one.

        The backend is handed that spelling and never the one written. It routes on the
        leading slash, so `skills/x` passes the rules as `/skills/x` and, handed on as
        written, is looked for under the session instead of in the catalogue -- a
        different file from the one the rules were asked about.
        """
        try:
            where = validate_path(path)
        except ValueError as malformed:
            return path, str(malformed)
        if _check_fs_permission(self._rules, operation, where) != "allow":
            return where, f"permission denied for {operation} on {where}"
        return where, None

    def _listed(self, result: LsResult) -> LsResult:
        if result.entries is None:
            return result
        kept = _filter_file_infos_by_permission(self._rules, result.entries, operation="read")
        return replace(result, entries=kept)

    def _globbed(self, result: GlobResult) -> GlobResult:
        if result.matches is None:
            return result
        kept = _filter_file_infos_by_permission(self._rules, result.matches, operation="read")
        return replace(result, matches=kept)

    def _grepped(self, result: GrepResult) -> GrepResult:
        if result.matches is None:
            return result
        kept = _filter_grep_matches_by_permission(self._rules, result.matches, operation="read")
        return replace(result, matches=kept)

    def ls(self, path: str) -> LsResult:
        where, refused = self._where("read", path)
        if refused:
            return LsResult(error=refused)
        return self._listed(self._inner.ls(where))

    async def als(self, path: str) -> LsResult:
        where, refused = self._where("read", path)
        if refused:
            return LsResult(error=refused)
        return self._listed(await self._inner.als(where))

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        where, refused = self._where("read", file_path)
        if refused:
            return ReadResult(error=refused)
        return self._inner.read(where, offset, limit)

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        where, refused = self._where("read", file_path)
        if refused:
            return ReadResult(error=refused)
        return await self._inner.aread(where, offset, limit)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        if path is not None:
            path, refused = self._where("read", path)
            if refused:
                return GrepResult(error=refused)
        return self._grepped(_grep_backend(self._inner, pattern, path, glob, max_count))

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        if path is not None:
            path, refused = self._where("read", path)
            if refused:
                return GrepResult(error=refused)
        return self._grepped(await _agrep_backend(self._inner, pattern, path, glob, max_count))

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        # The root stands in for an unnamed path when the rules are asked, and is not
        # passed on: `None` leaves the backend to choose where it searches from, and
        # `/` would choose for it.
        where, refused = self._where("read", "/" if path is None else path)
        if refused:
            return GlobResult(error=refused)
        return self._globbed(self._inner.glob(pattern, None if path is None else where))

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        where, refused = self._where("read", "/" if path is None else path)
        if refused:
            return GlobResult(error=refused)
        return self._globbed(await self._inner.aglob(pattern, None if path is None else where))

    def write(self, file_path: str, content: str) -> WriteResult:
        where, refused = self._where("write", file_path)
        if refused:
            return WriteResult(error=refused)
        return self._inner.write(where, content)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        where, refused = self._where("write", file_path)
        if refused:
            return WriteResult(error=refused)
        return await self._inner.awrite(where, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        where, refused = self._where("write", file_path)
        if refused:
            return EditResult(error=refused)
        return self._inner.edit(where, old_string, new_string, replace_all)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        where, refused = self._where("write", file_path)
        if refused:
            return EditResult(error=refused)
        return await self._inner.aedit(where, old_string, new_string, replace_all)

    def _undeletable(self, where: str, *, nested: bool) -> str | None:
        """Why a delete is refused, or `None`.

        Not `_where`: a delete takes everything under its target with it, so the
        question is whether a rule could match anywhere in that subtree, in any order,
        rather than whether the first one to match the path allows it.
        """
        denying = _find_delete_deny_patterns(self._rules, where, has_descendants=nested)
        if not denying:
            return None
        return (
            f"permission denied for write on {where} "
            f"(matches deny rule(s): {', '.join(denying)})"
        )

    def delete(self, file_path: str) -> DeleteResult:
        try:
            where = validate_path(file_path)
        except ValueError as malformed:
            return DeleteResult(error=str(malformed))
        nested = _delete_target_may_have_descendants(
            self._inner, where, permissions_configured=bool(self._rules)
        )
        if refused := self._undeletable(where, nested=nested):
            return DeleteResult(error=refused)
        return self._inner.delete(where)

    async def adelete(self, file_path: str) -> DeleteResult:
        try:
            where = validate_path(file_path)
        except ValueError as malformed:
            return DeleteResult(error=str(malformed))
        nested = await _adelete_target_may_have_descendants(
            self._inner, where, permissions_configured=bool(self._rules)
        )
        if refused := self._undeletable(where, nested=nested):
            return DeleteResult(error=refused)
        return await self._inner.adelete(where)

    def _sorted(
        self, operation: FilesystemOperation, asked: Sequence[str]
    ) -> tuple[list[tuple[int, str]], dict[int, str]]:
        """A batch split in two: what goes through, and why each of the rest does not.

        Both keyed by position, because a batch is answered in the order it was asked
        and a refused path in the middle must not shift the answers after it.
        """
        through: list[tuple[int, str]] = []
        refused: dict[int, str] = {}
        for at, path in enumerate(asked):
            try:
                where = validate_path(path)
            except ValueError:
                refused[at] = INVALID_PATH
                continue
            if _check_fs_permission(self._rules, operation, where) != "allow":
                refused[at] = PERMISSION_DENIED
            else:
                through.append((at, where))
        return through, refused

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        asked = [path for path, _ in files]
        through, refused = self._sorted("write", asked)
        sent = [(where, files[at][1]) for at, where in through]
        answers = self._inner.upload_files(sent) if sent else []
        return _in_order(asked, through, refused, answers, FileUploadResponse)

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        asked = [path for path, _ in files]
        through, refused = self._sorted("write", asked)
        sent = [(where, files[at][1]) for at, where in through]
        answers = await self._inner.aupload_files(sent) if sent else []
        return _in_order(asked, through, refused, answers, FileUploadResponse)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        through, refused = self._sorted("read", paths)
        sent = [where for _, where in through]
        answers = self._inner.download_files(sent) if sent else []
        return _in_order(paths, through, refused, answers, FileDownloadResponse)

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        through, refused = self._sorted("read", paths)
        sent = [where for _, where in through]
        answers = await self._inner.adownload_files(sent) if sent else []
        return _in_order(paths, through, refused, answers, FileDownloadResponse)


def _in_order[Response: (FileUploadResponse, FileDownloadResponse)](
    asked: Sequence[str],
    through: Sequence[tuple[int, str]],
    refused: dict[int, str],
    answers: Sequence[Response],
    refusal: type[Response],
) -> list[Response]:
    """One response per path asked, in the order asked.

    Under the spelling asked, too. The backend answers with the one it was handed,
    which is the normalised path, and a tool matching answers to what it sent would
    find `inputs/a.csv` missing from a batch that uploaded it.
    """
    landed = {
        at: replace(answer, path=asked[at])
        for (at, _), answer in zip(through, answers, strict=True)
    }
    return [
        landed[at] if at in landed else refusal(path=path, error=refused[at])
        for at, path in enumerate(asked)
    ]


def host_path(backend: Any, where: str) -> tuple[Path | None, Path] | None:
    """Where `where` lives on this host, as `(root it must stay in, path)`, or `None`.

    The backend's own mapping, not a second one: a route is followed as the composite
    follows it for a read, and a filesystem answers from its root. A backend that
    says for itself -- `host_path(virtual)` -- is taken at its word, root and all.
    Anything else keeps its files somewhere this host cannot open, as far as can be
    told, and answers `None`.
    """
    own = getattr(backend, "host_path", None)
    if callable(own):
        answered = own(where)
        return None if answered is None else (None, Path(answered))
    if isinstance(backend, CompositeBackend):
        for prefix in sorted(backend.routes, key=len, reverse=True):
            if where.startswith(prefix) or where == prefix.rstrip("/"):
                return host_path(backend.routes[prefix], "/" + where[len(prefix) :])
        return host_path(backend.default, where)
    if isinstance(backend, FilesystemBackend) and backend.virtual_mode:
        root = Path(backend.cwd)
        return root, root / where.lstrip("/")
    return None
