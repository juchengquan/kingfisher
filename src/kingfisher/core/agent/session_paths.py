"""What an offered tool's arguments mean against one session."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from kingfisher.core.layout import INPUTS_ROUTE, OUTPUTS_ROUTE, SCRATCH_ROUTE, Layout

from .host_paths import HOST_PATH_SAID, HOST_ROOTS, HostPathError
from .prompting import what_you_make_goes

#: Which arguments name a file: a convention `tools.md` documents, so widening it
#: means a sentence there too.
PATH_ARGUMENTS: frozenset[str] = frozenset({"path"})

#: Where an offered tool's other arguments may not point: the file tools' refused
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

    def __init__(self, permitted: Any, sessions: Path, layout: Layout) -> None:
        self._permitted = permitted
        #: The example routes a refusal offers, from this layout so a renamed session's
        #: paths are the ones named. `refusal_text` offers the first two, `where_to_write`
        #: the last two, which are the folders a write may actually land in.
        self.inputs_route = layout.inputs_route
        self.outputs_route = layout.outputs_route
        self.scratch_route = layout.scratch_route
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
            msg = f"{host!r} {HOST_PATH_SAID}, and a tool is handed this session's own paths"
            raise HostPathError(msg)
        wanted = {key: args[key] for key in args if key in PATH_ARGUMENTS}
        return {**args, **{key: self.real(value) for key, value in wanted.items()}}

    def real(self, value: Any) -> Any:
        """One argument, resolved where the session's backend keeps it."""
        if not isinstance(value, str) or not value.strip():
            return value
        return str(self._permitted.on_this_host(value))


#: A path a failure quotes. Every backend quotes one the same way, and quoting is what
#: tells a path in a message from the prose around it.
_QUOTED = re.compile(r"'(/[^']*)'")


def as_asked(text: str, args: Mapping[str, Any]) -> str:
    """`text` with every path it quotes spelled the way the call spelled it.

    What a failure says about a path has to be the path the model wrote, and twice over
    it was not. The composite strips a route before calling the backend that holds it,
    and that backend reports its own root: a miss under `/inputs/nope.txt` came back as
    `File '/nope.txt' not found`, a path in a different place, which a model is left to
    retry or write. And an `OSError` carries the host path it failed on, so a refused
    write answered with `/private/.../sessions/<id>/scratchpad/...` -- the spelling the
    file tools exist not to have, and `reject_host_path` refuses when a model writes one.

    One rule for both, because both are the asked-for path with a prefix taken off or
    put on: a quoted path that ends with one the call named, or that one ends with, is
    that path said another way. Longest first, so a call naming two paths rewrites each
    against the one it is really a tail of.
    """
    if HOST_PATH_SAID in text:
        return text
    asked = sorted(
        (one for one in _strings_in(args) if one.startswith("/")), key=len, reverse=True
    )

    def _said(match: re.Match[str]) -> str:
        named = match.group(1)
        for one in asked:
            if named != one and (named.endswith(one) or one.endswith(named)):
                return f"'{one}'"
        return match.group(0)

    return _QUOTED.sub(_said, text)


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


#: How deepagents reports a write its own deny rules refused, which is the only sign
#: kingfisher gets: the check runs inside each file tool's body and the helpers around it
#: are private. `test_a_refused_write_says_where_a_write_may_go` drives a real refusal, so
#: a deepagents that rewords this goes red rather than quietly dropping the sentence.
_REFUSED_WRITE = "permission denied for write on "


def where_to_write(text: str, paths: SessionPaths | None = None) -> str:
    """`text` with the folders a write may land in, where a deny rule refused one.

    One sentence for every scope in `denied_scopes`, naming neither the rule nor what the
    folder holds: `/.harness` is denied too and the agent is shown nothing of it, and why
    a folder is kept is the prompt's to say before the call rather than this message's
    after it. Beside `refusal_text` so the pair offers the model the same folders.

    The default is unreached rather than a fallback -- a deny rule comes from a session's
    layout, so a build with none raises no refusal to rewrite -- and is here only so the
    pair is called the same way. Measured; not worth a case of its own.
    """
    if _REFUSED_WRITE not in text:
        return text
    outputs_route = paths.outputs_route if paths is not None else OUTPUTS_ROUTE
    scratch_route = paths.scratch_route if paths is not None else SCRATCH_ROUTE
    return (
        f"{text} -- that path is not yours to write. "
        f"{what_you_make_goes(outputs_route, scratch_route)}"
    )
