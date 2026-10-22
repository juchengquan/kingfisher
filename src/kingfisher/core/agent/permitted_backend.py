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

from .backend import folders_of, strays
from .prompting import stray_save
from .references import UnsafeReferenceError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from deepagents import FilesystemPermission
    from deepagents.middleware.filesystem import FilesystemOperation

    from kingfisher.core.layout import Layout


class PermittedBackend(BackendProtocol):
    """A backend that refuses what this turn's file tools would refuse.

    deepagents applies `permissions=` inside each file tool and nowhere else, so a bare
    backend handed to a tool would let the model write into the shared `/skills` or
    read `/.harness`.

    Built on deepagents' own private helpers, so a rule means here what it means to
    `read_file`; a second matcher is the copy that drifts. `BackendProtocol`, not the
    sandbox one, so there is no `execute`: no rule reaches the shell.

    It bounds where the model can point a tool, not the tool, which runs in
    kingfisher's own process.
    """

    def __init__(
        self,
        backend: BackendProtocol,
        permissions: Sequence[FilesystemPermission],
        layout: Layout,
    ) -> None:
        self._inner = backend
        # Held, not copied: `build_agent` adds the turn's rules after building this.
        self._permissions = permissions
        # A tool saves where the model pointed it, so it strays as `write_file` would.
        self._layout = layout
        self._folders = folders_of(layout, backend)

    @property
    def _rules(self) -> list[FilesystemPermission]:
        # Nothing pauses for a call from inside a tool, so `interrupt` is a refusal here.
        # Rewritten because deepagents' result filters pass `interrupt` entries through.
        return [
            replace(rule, mode="deny") if rule.mode == "interrupt" else rule
            for rule in self._permissions
        ]

    def on_this_host(self, path: str) -> Path:
        """Where a tool may be handed `path` as a real file, or `UnsafeReferenceError`.

        The turn's rules first, as `read_file` applies them, so a refused path is never
        looked up; then the backend's own answer to where the path lives.
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
        real = candidate.resolve()
        # Refused, not resolved and judged again: the rules judged the path typed, and a
        # link the shell planted at `/scratchpad/x` reaches `.harness` without leaving
        # the session. A root that answered is already resolved, as upstream roots one.
        if root is not None and real != candidate:
            msg = f"{where} is or runs through a link, and a tool is handed none"
            raise UnsafeReferenceError(msg)
        return real

    def _where(self, operation: FilesystemOperation, path: str) -> tuple[str, str | None]:
        """The spelling the rules read, and the refusal if there is one.

        The backend must be handed that spelling, not the one written: it routes on the
        leading slash, so `skills/x` would be a different file from the one judged.
        """
        try:
            where = validate_path(path)
        except ValueError as malformed:
            return path, str(malformed)
        if _check_fs_permission(self._rules, operation, where) != "allow":
            return where, f"permission denied for {operation} on {where}"
        if operation == "write" and strays(where, self._folders):
            return where, stray_save(where, self._layout)
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
        # The root stands in for no path when the rules are asked, but `None` is passed
        # on, leaving the backend to choose where it searches.
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

        Not `_where`: a delete takes its whole subtree, so any rule matching anywhere
        in it refuses, not only the first to match the path.
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

        Keyed by position, so a refused path mid-batch does not shift the answers after it.
        """
        through: list[tuple[int, str]] = []
        refused: dict[int, str] = {}
        for at, path in enumerate(asked):
            try:
                where = validate_path(path)
            except ValueError:
                refused[at] = INVALID_PATH
                continue
            if _check_fs_permission(self._rules, operation, where) != "allow" or (
                operation == "write" and strays(where, self._folders)
            ):
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

    Under the spelling asked, too: the backend answers with the normalised path, and a
    tool matching answers to what it sent would find them missing.
    """
    landed = {
        at: replace(answer, path=asked[at])
        for (at, _), answer in zip(through, answers, strict=True)
    }
    return [
        landed[at] if at in landed else refusal(path=path, error=refused[at])
        for at, path in enumerate(asked)
    ]


def scratch_runs_of(backend: Any, method: str = "run_in_scratch") -> Any:
    """The backend's own `run_in_scratch` (or `method`), else its composite default's.

    Followed as `host_path` is: a composite forwards no method of its own, so a backend
    asked alone would refuse kingfisher's own. `None` where neither has it.
    """
    own = getattr(backend, method, None)
    if callable(own):
        return own
    if isinstance(backend, CompositeBackend):
        return scratch_runs_of(backend.default, method)
    return None


def host_path(backend: Any, where: str) -> tuple[Path | None, Path] | None:
    """Where `where` lives on this host, as `(root it must stay in, path)`, or `None`.

    The backend's own mapping, not a second one: a route is followed as the composite
    follows it, a filesystem answers from its root, and a backend with its own
    `host_path` is taken at its word. Anything else answers `None`.
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
