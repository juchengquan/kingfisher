"""Mounting a skills repository the agent can read, wherever it is backed.

Skills are the one kind kingfisher never parses. deepagents opens them itself,
through `backend.ls` and `backend.download_files` on whatever is mounted at `/skills`
— so a catalogue held somewhere that is not a filesystem was unmountable, not because
the route demanded a *path* but because `SkillRepository` could only answer with
names. `files` fixed the port; this mounts what it returns.

Built on deepagents' own `StoreBackend` rather than hand-written against
`BackendProtocol`. That protocol is 18 members and is beta, and every one we
implemented would be ours to keep in step with an upstream that is still moving --
this codebase has already paid that bill once, when overriding both `execute` and
`aexecute` on `LocalShellBackend` nested the sandbox twice and thirteen tests still
passed. `StoreBackend` already implements all 18 against a `BaseStore`, so what is
left here is filling a store and refusing the four operations that would write to it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from deepagents.backends.protocol import (
    DeleteResult,
    EditResult,
    FileUploadResponse,
    WriteResult,
)
from deepagents.backends.store import StoreBackend
from langgraph.store.memory import InMemoryStore

if TYPE_CHECKING:
    from kingfisher.domain.ports import SkillRepository

#: Where the skills live inside the store. One namespace, not one per skill:
#: the paths already carry the skill name, and `StoreBackend` searches a
#: namespace by prefix.
NAMESPACE = ("skills",)


class ReadOnlyStoreBackend(StoreBackend):
    """A `StoreBackend` that refuses every operation that would change it.

    The missing fourth is `aupload_files`, and its absence is deliberate rather than
    an oversight: upstream implements it as `await
    asyncio.to_thread(self.upload_files, files)`, so the sync refusal below already
    catches it. `awrite`, `aedit` and `adelete` do *not* delegate -- they have their
    own async implementations, 16, 25 and 10 lines of them -- so those three are
    load-bearing, which mutation testing confirms by failing when any one is removed.
    """

    #: Said once, so the four refusals cannot drift apart.
    REFUSAL = "Error: the skills catalogue is read-only; '{path}' was not changed."

    def _why(self, path: str) -> str:
        return self.REFUSAL.format(path=path)

    def write(self, file_path: str, content: str) -> Any:  # noqa: ARG002
        return WriteResult(error=self._why(file_path), path=None)

    def edit(
        self,
        file_path: str,
        old_string: str,  # noqa: ARG002 -- the signature is upstream's; nothing is edited
        new_string: str,  # noqa: ARG002
        replace_all: bool = False,  # noqa: ARG002
    ) -> Any:
        return EditResult(error=self._why(file_path), path=None, occurrences=0)

    def delete(self, file_path: str) -> Any:
        return DeleteResult(error=self._why(file_path), path=None)

    def upload_files(self, files: list[tuple[str, bytes]]) -> Any:
        return [FileUploadResponse(path=path, error=self._why(path)) for path, _ in files]

    async def awrite(self, file_path: str, content: str) -> Any:  # noqa: ARG002
        return WriteResult(error=self._why(file_path), path=None)

    async def aedit(
        self,
        file_path: str,
        old_string: str,  # noqa: ARG002 -- the signature is upstream's; nothing is edited
        new_string: str,  # noqa: ARG002
        replace_all: bool = False,  # noqa: ARG002
    ) -> Any:
        return EditResult(error=self._why(file_path), path=None, occurrences=0)

    async def adelete(self, file_path: str) -> Any:
        return DeleteResult(error=self._why(file_path), path=None)


def skills_backend(repository: SkillRepository) -> ReadOnlyStoreBackend:
    """Mount a skills repository for the agent to read."""
    store = InMemoryStore()
    for name in repository.names:
        for relative, content in repository.files(name).items():
            store.put(
                NAMESPACE,
                f"/{name}/{relative}",
                {"content": content, "encoding": "utf-8"},
            )
    return ReadOnlyStoreBackend(namespace=lambda _runtime: NAMESPACE, store=store)
