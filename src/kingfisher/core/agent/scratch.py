"""An offered tool's runs, bound to the one call it was handed them for."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import TYPE_CHECKING, Any

from kingfisher.core.interfaces import MAX_SCRATCH_BYTES, MAX_SCRATCH_OUTPUTS, ScratchRun
from kingfisher.core.session.threads import finished, off_loop, thread_pool

from .permitted_backend import scratch_runs_of
from .prompting import piece

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

    def __init__(self, backend: Any, *, seconds: int, until: float | None = None) -> None:
        self._backend = backend
        # One clock for the call: ten runs of the longest command would hold the session
        # past the claim that counts on none outliving `execution_timeout_s`.
        own = time.monotonic() + seconds
        # And never past the turn's end: a call from `eval`'s code is no step of its own,
        # so a program of them is one step the turn's deadline is never read inside.
        self._deadline = own if until is None else min(own, until)
        self._turn_ends = self._deadline != own
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
            return ScratchRun(error=piece(_NO_METHOD))
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
        method = scratch_runs_of(self._backend)
        if method is None:
            return ScratchRun(error=piece(_NO_METHOD))
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
            return piece("refusals/scratch_call_ended")
        if isinstance(argv, str) or not argv or not all(isinstance(a, str) for a in argv):
            return piece("refusals/scratch_argv")
        named = [*files, *outputs]
        if bad := [name for name in named if unfit(name)]:
            return piece("refusals/scratch_names", names=", ".join(map(repr, bad)))
        carried = sum(len(data) for data in files.values())
        if carried > MAX_SCRATCH_BYTES:
            return piece(
                "refusals/scratch_too_large", carried=str(carried), most=str(MAX_SCRATCH_BYTES)
            )
        if len(outputs) > MAX_SCRATCH_OUTPUTS:
            return piece(
                "refusals/scratch_too_many_outputs",
                named=str(len(outputs)),
                most=str(MAX_SCRATCH_OUTPUTS),
            )
        return None

    def _out_of_time(self) -> str | None:
        if self._left(None) < 1:
            return piece(
                "refusals/scratch_turn_out_of_time"
                if self._turn_ends
                else "refusals/scratch_call_out_of_time"
            )
        return None

    def _left(self, asked: int | None) -> int:
        left = int(self._deadline - time.monotonic())
        return left if asked is None else min(asked, left)


_NO_METHOD = "refusals/scratch_no_method"
