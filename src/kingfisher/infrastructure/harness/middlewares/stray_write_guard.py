"""Refusing a save outside every folder the session names, and saying where it goes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from deepagents.backends.utils import validate_path
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from kingfisher.infrastructure.harness.host_paths import HOST_ROOTS
from kingfisher.layout import DERIVED_ROUTE, ROUTES, SCRATCH_ROUTE

#: The tools that save a file the model names, and the argument naming it.
SAVES = frozenset({"write_file", "edit_file"})


def _is_host_path(path: str, session_dir: Path | None) -> bool:
    """Whether `reject_host_path` will refuse this, with a correction of its own."""
    if not path.startswith("/"):
        return False
    return path.startswith(HOST_ROOTS) or (
        session_dir is not None and path.startswith(f"{session_dir}/")
    )


# On the model's save calls rather than in the backend, and that is the whole of why it
# is a middleware. deepagents writes files of its own at the top of the session --
# oversized tool results under `/large_tool_results/`, summarised history under
# `/conversation_history/` -- through the same backend, and a backend refusing
# everything outside the session's folders would break both.
class StrayWriteGuard(AgentMiddleware):
    """Refuse a save outside every folder the session names, and say where it goes.

    A bare `result.json` is the top of the session to a file tool, and nothing there is
    returned or kept: measured, a run wrote both its outputs there, reported them
    written, and handed its caller nothing.
    """

    def __init__(self, session_dir: Path | None) -> None:
        self._session_dir = session_dir
        super().__init__()

    def _refusal(self, request: Any) -> ToolMessage | None:
        call = request.tool_call
        if call.get("name") not in SAVES:
            return None
        raw = (call.get("args") or {}).get("file_path")
        if not isinstance(raw, str) or _is_host_path(raw, self._session_dir):
            return None
        try:
            path = validate_path(raw)
        except ValueError:
            return None  # the tool refuses it itself, and says why
        if path.startswith(tuple(route.path for route in ROUTES)):
            return None
        return ToolMessage(
            content=(
                f"Error: {raw!r} would be saved outside the session's folders, where "
                f"nothing is kept or returned. Save it as "
                f"{DERIVED_ROUTE + path.lstrip('/')!r} instead, or under {SCRATCH_ROUTE} "
                f"if it is a working file."
            ),
            tool_call_id=call.get("id", ""),
            name=call.get("name"),
            status="error",
        )

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return self._refusal(request) or handler(request)

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        refusal = self._refusal(request)
        return refusal if refusal is not None else await handler(request)
