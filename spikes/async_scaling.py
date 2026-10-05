"""Does kingfisher's async path scale against session backends that do real async I/O?

The case for awaiting the ports rather than handing each call to a thread was measured
on `asyncio.sleep` against `time.sleep` alone. This asks it of kingfisher's own path:

    uv run python spikes/async_scaling.py            # the default pool
    uv run python spikes/async_scaling.py 64         # a pool of 64

Every port call on the fake remote below takes `LATENCY` -- `time.sleep` in the sync
method, `asyncio.sleep` in its async twin, as a remote backend's client offering both
would. At each count of concurrent callers it times the sync method handed to a
thread each, which is what an async caller had before the twins, against the twin
awaited. Both a read (`pending`) and a whole turn on a pre-built graph, which reaches
the backend in setup and again in its ending.

No model is called and nothing leaves the machine. The graph is the unit tests' stub,
so what is timed is kingfisher and the fake backend's latency, and nothing else.
"""

# The fake below implements two protocols whose every argument it is handed and most
# of which it has no use for.
# ruff: noqa: ARG002

from __future__ import annotations

import asyncio
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

# The repository root, so `tests` imports. A spike is run as a script, so `sys.path`
# starts at `spikes/`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepagents.backends.protocol import FileDownloadResponse, FileUploadResponse

from kingfisher import Kingfisher, Request
from kingfisher.config import DEFAULT_THREAD_POOL_SIZE, Config
from kingfisher.infrastructure.harness.backend import SessionBackends
from tests.conftest import FAKE_CATALOGUE, StubCheckpointer
from tests.unit.test_run import StubAgent

#: One round trip to the remote backend.
LATENCY = 0.05
#: Concurrent callers at each step. Past 12 the loop's default executor queues.
COUNTS = (1, 10, 50, 200)
MADE = {"sync": 0, "async": 0}


def _blocking() -> None:
    MADE["sync"] += 1
    time.sleep(LATENCY)


async def _awaited() -> None:
    MADE["async"] += 1
    await asyncio.sleep(LATENCY)


class RemoteFiles:
    """One session's files, a round trip away."""

    def __init__(self, remote: Remote, session_id: str) -> None:
        self.remote, self.session_id = remote, session_id
        self.held = remote.files.setdefault(session_id, {})

    def _answers_for(self, paths):
        return [
            FileDownloadResponse(
                path=p,
                content=self.held.get(p),
                error=None if p in self.held else "file_not_found",
            )
            for p in paths
        ]

    def download_files(self, paths):
        _blocking()
        return self._answers_for(paths)

    async def adownload_files(self, paths):
        await _awaited()
        return self._answers_for(paths)

    def _upload(self, files):
        for path, content in files:
            self.held[path] = content
        return [FileUploadResponse(path=path, error=None) for path, _ in files]

    def upload_files(self, files):
        _blocking()
        return self._upload(files)

    async def aupload_files(self, files):
        await _awaited()
        return self._upload(files)

    def delete(self, path):
        _blocking()
        self.held.pop(path, None)

    async def adelete(self, path):
        await _awaited()
        self.held.pop(path, None)

    def _glob(self, path):
        under = [p for p in self.held if p.startswith(path or "/")]
        return SimpleNamespace(error=None, truncated=False, matches=[{"path": p} for p in under])

    def glob(self, pattern, path=None):
        _blocking()
        return self._glob(path)

    async def aglob(self, pattern, path=None):
        await _awaited()
        return self._glob(path)

    def _take(self, name):
        with self.remote.lock:
            key = (self.session_id, name)
            if key in self.remote.claims:
                return False
            self.remote.claims.add(key)
            return True

    def claim(self, name, *, stale_after, now=None):
        _blocking()
        return self._take(name)

    async def aclaim(self, name, *, stale_after, now=None):
        await _awaited()
        return self._take(name)

    def release(self, name):
        _blocking()
        with self.remote.lock:
            self.remote.claims.discard((self.session_id, name))


class Remote(SessionBackends):
    """Session backends a round trip away, with an async client as well as a sync one."""

    def __init__(self, sessions) -> None:
        self.known = dict.fromkeys(sessions, time.time())
        self.files: dict[str, dict] = {}
        self.claims: set = set()
        self.lock = threading.Lock()

    def open(self, cfg, session_id, /, *, catalogue=None, runner=None):
        _blocking()
        return RemoteFiles(self, session_id)

    async def aopen(self, cfg, session_id, /, *, catalogue=None, runner=None):
        await _awaited()
        return RemoteFiles(self, session_id)

    def sessions(self, cfg):
        _blocking()
        return tuple(self.known.items())

    async def asessions(self, cfg):
        await _awaited()
        return tuple(self.known.items())

    def mark_used(self, cfg, session_id):
        _blocking()

    async def amark_used(self, cfg, session_id):
        await _awaited()

    def size(self, cfg, session_id):
        _blocking()
        return 0

    async def asize(self, cfg, session_id):
        await _awaited()
        return 0

    def delete(self, cfg, session_id):
        _blocking()

    async def adelete(self, cfg, session_id):
        await _awaited()


async def _timed(calls) -> float:
    started = time.perf_counter()
    await asyncio.gather(*calls)
    return (time.perf_counter() - started) * 1000


def main() -> int:
    pool = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_THREAD_POOL_SIZE
    sessions = [f"s{i}" for i in range(max(COUNTS))]
    with tempfile.TemporaryDirectory() as workspace:
        cfg = Config(workspace=Path(workspace), models=FAKE_CATALOGUE, thread_pool_size=pool)
        kf = Kingfisher(
            cfg, graph=StubAgent("ok"), backends=Remote(sessions), threads=StubCheckpointer()
        )
        print(f"{LATENCY * 1000:.0f}ms a round trip; kingfisher's pool holds {pool}")
        calls = {
            "pending": (kf.pending, kf.apending),
            "turn": (
                lambda s: kf.run(Request("go", session_id=s)),
                lambda s: kf.arun(Request("go", session_id=s)),
            ),
        }
        for name, (sync_call, async_call) in calls.items():
            MADE.update({"sync": 0, "async": 0})
            sync_call(sessions[0])
            blocking = MADE["sync"]
            MADE.update({"sync": 0, "async": 0})
            asyncio.run(async_call(sessions[0]))
            print(
                f"\n{name}: {blocking} round trips; the async path awaits {MADE['async']} "
                f"and hands {MADE['sync']} to the pool"
            )
            print(f"  {'callers':>7}  {'thread per call':>15}  {'awaited':>8}")
            for count in COUNTS:

                async def both(count: int = count, sync_call=sync_call, async_call=async_call):
                    threaded = await _timed(
                        [asyncio.to_thread(sync_call, s) for s in sessions[:count]]
                    )
                    awaited = await _timed([async_call(s) for s in sessions[:count]])
                    return threaded, awaited

                threaded, awaited = asyncio.run(both())
                print(f"  {count:>7}  {threaded:>13.0f}ms  {awaited:>6.0f}ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
