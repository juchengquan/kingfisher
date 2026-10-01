"""What a workspace tool's arguments mean against one session."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from kingfisher.infrastructure.harness.host_paths import HOST_ROOTS, HostPathError

#: Which arguments name a file. The convention this repository already keeps --
#: `test_every_shipped_tool_taking_a_path_says_it_is_a_session_path` walks the shipped
#: tools looking for exactly this parameter name -- so widening it is a line here and a
#: test, rather than a design question.
PATH_ARGUMENTS: frozenset[str] = frozenset({"path"})

#: Where a workspace tool's *other* arguments may not point: the prefixes the file tools
#: refuse, and the four a process reads its host and itself through --
#: `/proc/self/environ` holds this process's API keys. Refused rather than translated,
#: because nothing says such an argument names a file, and it reaches the tool as
#: written: a tool calling its file `input_file` was handed another session's secret.
#: Not a boundary -- a tool runs in kingfisher's own process, unfenced, and can open
#: anything it likes -- but it takes away the obvious way for a model to ask one to.
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

    Apart from both of its users because two places have to answer identically and
    only one of them has a call to rewrite: `WorkspaceToolPaths`, and `GuardedTool`,
    which travels on the tools themselves because a compiled delegate has no
    middleware to attach. A second `real` written for the second place is the copy
    that drifts, and the escape it stops resolving would not be visible in either
    file.

    Holds the turn's backend under the turn's rules, not a session directory. The
    directory was a second copy of the backend's own path mapping, and it drifted:
    it handed a tool `/skills` files that do not exist, `/.harness` files the file
    tools are refused, and on a backend keeping sessions elsewhere a local path to
    nothing.
    """

    def __init__(self, permitted: Any, sessions: Path) -> None:
        self._permitted = permitted
        # Where every session is, spelled both ways. In a container the workspace is
        # `/workspace`, which no host root names, and another session is one
        # directory over from this one.
        self.roots = tuple({f"{root}/" for root in (str(sessions), str(Path(sessions).resolve()))})
        self._refused = (*NOT_FOR_TOOLS, *self.roots)

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


def refusal_text(escaped: ValueError) -> str:
    """What the model is told when a path is refused, in one place.

    Both refusing paths say it: the middleware returns it as a failed result, and
    `GuardedTool` raises it as one. A model that is told the rule can correct itself
    mid-turn, and it can only do that if the rule reads the same either way.
    """
    return (
        f"Error: {escaped}. Tool paths are the same virtual paths the file "
        "tools take, rooted at this session -- `/data/<name>`, "
        "`/derived/<name>` -- and cannot climb out of it."
    )
