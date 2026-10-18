"""What a workspace tool's arguments mean against one session."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from kingfisher.core.agent.host_paths import HOST_ROOTS, HostPathError
from kingfisher.core.layout import DEFAULT_LAYOUT, INPUTS_ROUTE, OUTPUTS_ROUTE, Layout

#: Which arguments name a file: a convention `tools.md` documents, so widening it
#: means a sentence there too.
PATH_ARGUMENTS: frozenset[str] = frozenset({"path"})

#: Where a workspace tool's other arguments may not point: the file tools' refused
#: prefixes, and those a process reads its host and itself through -- `/proc/self/environ`
#: holds this process's API keys. Refused, not translated, since nothing says such an
#: argument names a file. Not a boundary, as a tool can open anything, but it removes
#: the obvious way for a model to ask one to.
NOT_FOR_TOOLS: tuple[str, ...] = (*HOST_ROOTS, "/root/", "/proc/", "/sys/", "/dev/")


def _strings_in(value: Any) -> Iterator[str]:
    """Every string an argument carries, however deeply a list or a mapping holds it."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for inner in value.values():
            yield from _strings_in(inner)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for inner in value:
            yield from _strings_in(inner)


class SessionPaths:
    """One session, and what a tool call's arguments mean against it.

    Shared by `GuardedCallsMiddleware` and `GuardedTool`, which must answer identically; a
    second copy of `real` is the one that drifts. Holds the turn's backend under its
    rules, not a session directory, so paths follow the backend's own mapping.
    """

    def __init__(self, permitted: Any, sessions: Path, layout: Layout = DEFAULT_LAYOUT) -> None:
        self._permitted = permitted
        #: The example routes `refusal_text` offers, from this layout so a renamed
        #: session's paths are the ones named.
        self.inputs_route = layout.inputs_route
        self.outputs_route = layout.outputs_route
        # Every session's folder, spelled both ways: in a container the workspace is
        # `/workspace`, which no host root names.
        self._refused = (
            *NOT_FOR_TOOLS,
            *{f"{root}/" for root in (str(sessions), str(Path(sessions).resolve()))},
        )

    def host_path_in(self, args: Mapping[str, Any]) -> str | None:
        """The first host path in an argument that is not `path`, or `None`."""
        for key, value in args.items():
            if key in PATH_ARGUMENTS:
                continue
            for text in _strings_in(value):
                if text.startswith(self._refused) or f"{text}/" in self._refused:
                    return text
        return None

    def translated(self, args: Mapping[str, Any]) -> dict[str, Any]:
        """The same arguments with their paths made real, refusing what escapes."""
        if (host := self.host_path_in(args)) is not None:
            msg = f"{host!r} is a host path, and a tool is handed this session's own paths"
            raise HostPathError(msg)
        wanted = {key: args[key] for key in args if key in PATH_ARGUMENTS}
        return {**args, **{key: self.real(value) for key, value in wanted.items()}}

    def real(self, value: Any) -> Any:
        """One argument, resolved where the session's backend keeps it."""
        if not isinstance(value, str) or not value.strip():
            return value
        return str(self._permitted.on_this_host(value))


def refusal_text(escaped: ValueError, paths: SessionPaths | None = None) -> str:
    """What the model is told when a path is refused, in one place.

    The middleware and `GuardedTool` both say it, so the rule reads the same either way
    and the model can correct itself. Without `paths`, the default spellings are used.
    """
    inputs_route = paths.inputs_route if paths is not None else INPUTS_ROUTE
    outputs_route = paths.outputs_route if paths is not None else OUTPUTS_ROUTE
    return (
        f"Error: {escaped}. Tool paths are the same virtual paths the file "
        f"tools take, rooted at this session -- `{inputs_route}<name>`, "
        f"`{outputs_route}<name>` -- and cannot climb out of it."
    )
