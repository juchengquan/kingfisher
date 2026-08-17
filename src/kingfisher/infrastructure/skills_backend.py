"""Mounting a skills repository the agent can read, wherever it is backed.

Skills are the one kind kingfisher never parses. deepagents opens them itself,
through `backend.ls` and `backend.download_files` on whatever is mounted at
`/skills` — so a catalogue held somewhere that is not a filesystem was
unmountable, not because the route demanded a *path* but because
`SkillRepository` could only answer with names. `files` fixed the port; this
mounts what it returns.

Built on deepagents' own `StoreBackend` rather than hand-written against
`BackendProtocol`. That protocol is 18 members and is beta, and every one we
implemented would be ours to keep in step with an upstream that is still moving
-- this codebase has already paid that bill once, when overriding both `execute`
and `aexecute` on `LocalShellBackend` nested the sandbox twice and thirteen
tests still passed. `StoreBackend` already implements all 18 against a
`BaseStore`, so what is left here is filling a store and refusing the four
operations that would write to it.

Read-only by construction, then, rather than by a rule someone remembers to add.
That matters more here than anywhere else in the process: a skill is the text
the model is told to follow, so a writable skills route is a route by which one
request edits the instructions of every later one.

The cost, stated because it is real and because it decides where this is used:
the store holds every skill's bytes for the life of the deployment. A directory
already on disk should stay a `FilesystemBackend` -- it is cheaper, it needs no
copy, and it is the only shape whose skills can also be *executed*, since a
skill's scripts are run by the shell against `$KINGFISHER_SKILLS` and a store
has no path for the shell to reach. `build_backend` picks on exactly that basis.
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

    Four overridden, plus their async twins, and no more -- everything that
    *reads* is upstream's and stays upstream's. `delete` is included because a
    route the agent can empty is a route the agent can silence.

    Refusing by return value rather than by raising, because that is how this
    protocol reports a refused operation: the agent sees the message and can act
    on it, where an exception would end the turn.
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

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> Any:
        return [FileUploadResponse(path=path, error=self._why(path)) for path, _ in files]


def skills_backend(repository: SkillRepository) -> ReadOnlyStoreBackend:
    """Mount a skills repository for the agent to read.

    Every skill is read once, here, when the deployment is wired -- which is the
    same moment the catalogue's names are read, and for the same reason: a
    definition that cannot be fetched is a wiring mistake, and this is the last
    point at which saying so is cheap.

    Text, not bytes, because that is what `StoreBackend` stores and what a skill
    is. A skill shipping something genuinely binary is decoded with `replace`
    rather than refused: the agent reads a skill's definition and runs the rest,
    and failing the whole catalogue over one stray image would be a worse trade
    than one unreadable file.
    """
    store = InMemoryStore()
    for name in repository.names:
        for relative, content in repository.files(name).items():
            store.put(
                NAMESPACE,
                f"/{name}/{relative}",
                {"content": content.decode("utf-8", errors="replace"), "encoding": "utf-8"},
            )
    return ReadOnlyStoreBackend(namespace=lambda _runtime: NAMESPACE, store=store)
