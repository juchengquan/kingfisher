"""An offered tool's runs, bound to the one call it was handed them for."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import TYPE_CHECKING, Any

from kingfisher.core.interfaces import MAX_SCRATCH_BYTES, MAX_SCRATCH_OUTPUTS, ScratchRun
from kingfisher.core.session.threads import finished, off_loop, thread_pool

from .permitted_backend import scratch_runs_of

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


def unfit(name: str) -> bool:
    """Whether `name` is not one plain file name: no folder, nothing hidden, nothing above."""
    return not name or name.startswith(".") or "/" in name or "\0" in name


class ScratchRunner:
    """One tool call's runs on the session's backend, under one deadline for them all.

    Closed when the call ends, so a runner a tool keeps runs nothing later, for this
    agent or another session's.
    """

    def __init__(self, backend: Any, *, seconds: int) -> None:
        self._backend = backend
        # One clock for the call: ten runs of the longest command would hold the session
        # past the claim that counts on none outliving `execution_timeout_s`.
        self._deadline = time.monotonic() + seconds
        self._closed = False

    def close(self) -> None:
        self._closed = True

    def run(
        self,
        argv: Sequence[str],
        files: Mapping[str, bytes],
        *,
        outputs: Sequence[str],
        timeout: int | None,
    ) -> ScratchRun:
        refused = self._refused(argv, files, outputs) or self._out_of_time()
        if refused is not None:
            return ScratchRun(error=refused)
        method = scratch_runs_of(self._backend)
        if method is None:
            return ScratchRun(error=_NO_METHOD)
        return method(
            list(argv), dict(files), outputs=tuple(outputs), timeout=self._left(timeout)
        )

    async def arun(
        self,
        argv: Sequence[str],
        files: Mapping[str, bytes],
        *,
        outputs: Sequence[str],
        timeout: int | None,
    ) -> ScratchRun:
        refused = self._refused(argv, files, outputs) or self._out_of_time()
        if refused is not None:
            return ScratchRun(error=refused)
        args = (list(argv), dict(files))
        native = scratch_runs_of(self._backend, "arun_in_scratch")
        if native is not None:
            return await native(*args, outputs=tuple(outputs), timeout=self._left(timeout))
        method = scratch_runs_of(self._backend)
        if method is None:
            return ScratchRun(error=_NO_METHOD)
        stop = threading.Event()
        pending = off_loop(
            thread_pool(),
            method,
            *args,
            outputs=tuple(outputs),
            timeout=self._left(timeout),
            stop=stop,
        )
        try:
            return await asyncio.shield(pending)
        except asyncio.CancelledError:
            stop.set()
            # Until the command is dead and its folder gone: until then the session is
            # still in use by a turn its caller has let go of.
            await finished(pending)
            raise

    def _refused(
        self, argv: Sequence[str], files: Mapping[str, bytes], outputs: Sequence[str]
    ) -> str | None:
        if self._closed:
            return "refused: this tool call has ended, and its runs with it"
        if isinstance(argv, str) or not argv or not all(isinstance(a, str) for a in argv):
            return "refused: argv is the program and its arguments as a list of strings"
        named = [*files, *outputs]
        if bad := [name for name in named if unfit(name)]:
            return (
                f"refused: {', '.join(map(repr, bad))} -- a file here is one plain name, "
                "with no folder and no leading dot"
            )
        carried = sum(len(data) for data in files.values())
        if carried > MAX_SCRATCH_BYTES:
            return (
                f"refused: the files come to {carried} bytes, past the {MAX_SCRATCH_BYTES} "
                "one run may carry"
            )
        if len(outputs) > MAX_SCRATCH_OUTPUTS:
            return (
                f"refused: {len(outputs)} outputs named, past the {MAX_SCRATCH_OUTPUTS} one "
                "run may bring back"
            )
        return None

    def _out_of_time(self) -> str | None:
        if self._left(None) < 1:
            return "refused: this tool call has no time left to run anything in"
        return None

    def _left(self, asked: int | None) -> int:
        left = int(self._deadline - time.monotonic())
        return left if asked is None else min(asked, left)


_NO_METHOD = (
    "refused: this session's backend offers no run_in_scratch, so there is nowhere to run it"
)
