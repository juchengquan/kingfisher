"""A deepagents sandbox whose `glob` says what each file holds, worked out where the file is,
so a turn tells an unchanged file without reading it back."""

from __future__ import annotations

import base64
import json
from dataclasses import replace
from typing import TYPE_CHECKING, Any, cast

from deepagents.backends.sandbox import BaseSandbox

from kingfisher.core.layout import DEFAULT_LAYOUT
from kingfisher.core.session.files import CHECKSUM

if TYPE_CHECKING:
    from collections.abc import Iterator

    from deepagents.backends.protocol import FileInfo, GlobResult

#: Opened without blocking and checked to be a plain file before it is read: a file swapped
#: for a pipe since the listing would wait for a writer that never comes.
_HASH_COMMAND = """python3 -c "
import base64, hashlib, json, os, stat
for path in json.loads(base64.b64decode('{paths}').decode('utf-8')):
    try:
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK), 'rb') as held:
            if not stat.S_ISREG(os.fstat(held.fileno()).st_mode):
                continue
            digest = hashlib.sha256()
            for block in iter(lambda: held.read(1048576), b''):
                digest.update(block)
    except OSError:
        continue
    print(json.dumps({{'path': path, 'sha256': digest.hexdigest()}}), flush=True)
"
"""

#: Paths per command, by the length of their JSON: a command is one argument to the shell,
#: and Linux refuses one past 128 KB.
_PATHS_PER_COMMAND = 48_000

class GlobWithChecksums(BaseSandbox):
    """Put before your sandbox's own class, `class MySandbox(GlobWithChecksums, Theirs)`, and
    each file `glob` lists under `checksummed` comes back with a `checksum`.

    One more command, run where `execute` runs, reads each listed file and hashes it, so
    nothing crosses the network. A file it gives no answer for -- no `python3`, a command
    that fails or is cut short, a file gone since the listing -- is listed by name alone,
    as `BaseSandbox` lists it.
    """

    #: The folders kingfisher lists at every turn. Under a renamed `Layout`, set it from
    #: `cfg.layout`. A `glob` elsewhere is `BaseSandbox`'s: the agent's glob tool gives up
    #: after 10 seconds, and hashing a large tree would take it past that.
    checksummed: tuple[str, ...] = (
        DEFAULT_LAYOUT.inputs_route,
        DEFAULT_LAYOUT.outputs_route,
        DEFAULT_LAYOUT.memory_route,
    )

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        listed = super().glob(pattern, path)
        found: dict[str, str] = {}
        for command in _commands(listed, path, self.checksummed):
            try:
                answered = self.execute(command).output
            except Exception:  # noqa: BLE001 -- those files are listed by name alone
                answered = ""
            found |= _checksums(answered)
        return _with(listed, found)

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        listed = await super().aglob(pattern, path)
        found: dict[str, str] = {}
        for command in _commands(listed, path, self.checksummed):
            try:
                answered = (await self.aexecute(command)).output
            except Exception:  # noqa: BLE001 -- those files are listed by name alone
                answered = ""
            found |= _checksums(answered)
        return _with(listed, found)


def _commands(
    listed: GlobResult, path: str | None, folders: tuple[str, ...]
) -> Iterator[str]:
    """Each command to hash the files `listed` names; none where `path` is under none of
    `folders`."""
    if path is None or listed.error or listed.truncated:
        return
    if not any(_under(path, one) for one in folders):
        return
    paths = [one["path"] for one in listed.matches or () if not one.get("is_dir")]
    batch: list[str] = []
    size = 0
    for one in paths:
        cost = len(json.dumps(one)) + 2
        if batch and size + cost > _PATHS_PER_COMMAND:
            yield _command(batch)
            batch, size = [], 0
        batch.append(one)
        size += cost
    if batch:
        yield _command(batch)


def _under(path: str, folder: str) -> bool:
    return f"{path.rstrip('/')}/".startswith(folder)


def _command(paths: list[str]) -> str:
    encoded = base64.b64encode(json.dumps(paths).encode("utf-8")).decode("ascii")
    return _HASH_COMMAND.format(paths=encoded)


def _checksums(output: str) -> dict[str, str]:
    """Each path the output answers, with its checksum. Read line by line, since the shell
    mixes in what it says on stderr and a cut-short output ends partway through one."""
    found: dict[str, str] = {}
    for line in output.splitlines():
        try:
            answer: Any = json.loads(line)
        except ValueError:
            continue
        if not isinstance(answer, dict):
            continue
        where, digest = answer.get("path"), answer.get("sha256")
        if isinstance(where, str) and isinstance(digest, str):
            found[where] = f"sha256:{digest}"
    return found


def _with(listed: GlobResult, found: dict[str, str]) -> GlobResult:
    if not found:
        return listed
    matches = [
        cast("FileInfo", {**one, CHECKSUM: found[one["path"]]}) if one["path"] in found else one
        for one in listed.matches or ()
    ]
    return replace(listed, matches=matches)
