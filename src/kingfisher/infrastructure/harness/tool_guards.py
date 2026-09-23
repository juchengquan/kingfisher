"""The guards every graph holding workspace tools is wrapped in."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, StructuredTool, ToolException

from kingfisher.domain.references import UnsafeReferenceError, within
from kingfisher.infrastructure.harness.host_paths import HOST_ROOTS, HostPathError, HostPathGuard

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

    Apart from the middleware because two places have to answer identically and only
    one of them has a call to rewrite: the middleware below, and `GuardedTool`, which
    travels on the tools themselves because a compiled delegate has no middleware to
    attach. A second `_real` written for the second place is the copy that drifts,
    and the escape it stops resolving would not be visible in either file.
    """

    def __init__(self, session_dir: Path) -> None:
        self.session_dir = Path(session_dir)
        # The directory this session's siblings are in, spelled both ways. In a
        # container the workspace is `/workspace`, which no host root names, and
        # another session is one directory over from this one.
        siblings = self.session_dir.parent
        self._refused = (
            *NOT_FOR_TOOLS,
            *{f"{root}/" for root in (str(siblings), str(siblings.resolve()))},
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
        """One argument, resolved against the session the way a file tool would."""
        if not isinstance(value, str) or not value.strip():
            return value
        landed = within(self.session_dir, value.lstrip("/"))
        # The second check `within` tells adapters to do, and it is not optional here:
        # that one is lexical, on purpose, because the domain may not touch the
        # filesystem -- and a session directory is one the agent can write to. `execute`
        # is rooted there, so it can make a symlink pointing out, hand a tool the
        # virtual path to it, and be read the target.
        #
        # Measured before this existed: a link at `/derived/link.txt` pointing at
        # another session returned `TENANT-A-PRIVATE` through a tool, while `read_file`
        # refused the same path. deepagents resolves and compares; this had only half of
        # that.
        real = landed.resolve()
        if not real.is_relative_to(self.session_dir.resolve()):
            msg = (
                f"reference {value!r} resolves outside this session; a link inside it "
                "does not widen it"
            )
            raise UnsafeReferenceError(msg)
        return str(real)


def _refusal_text(escaped: ValueError) -> str:
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


class WorkspaceToolPaths(AgentMiddleware):
    """Translate the agent's own paths into real ones, per session.

    **It closes a leak and a usability bug with one change, and the second is how the
    first was found.** `system.md` teaches virtual paths and says the two views do
    not mix; the tools wanted host paths and the agent is never told one. Measured in
    a real run: the model passed `/data/config.ini` and the tool raised
    `FileNotFoundError`. The only way it could succeed was to go looking -- `pwd` in
    the shell, learn the layout -- and from there it can name *any* session:
    `line_count('/workspace/sessions/<other>/secret.txt')` returned an answer.

    Rewriting the call rather than wrapping each tool, because a graph this is
    attached to holds tools that are not alike: some are `BaseTool`s from `@tool` and
    some are plain functions. The call is the one shape they share, and langgraph
    documents the rewrite -- `{**request.tool_call, "args": {...}}`. Where there is
    no middleware to attach, `GuardedTool` pays the cost of wrapping instead.
    """

    def __init__(self, names: frozenset[str], session_dir: Path) -> None:
        self.names = names
        self.paths = SessionPaths(session_dir)
        super().__init__()

    def _translated(self, request: Any) -> Any:
        """The same call with its path arguments made real, or the request
        unchanged when it names no tool of ours."""
        call = request.tool_call
        if call.get("name") not in self.names:
            return request
        args = call.get("args") or {}
        return replace(request, tool_call={**call, "args": self.paths.translated(args)})

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        try:
            return handler(self._translated(request))
        except (UnsafeReferenceError, HostPathError) as escaped:
            return self._refusal(request, escaped)

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        try:
            return await handler(self._translated(request))
        except (UnsafeReferenceError, HostPathError) as escaped:
            return self._refusal(request, escaped)

    def _refusal(self, request: Any, escaped: ValueError) -> ToolMessage:
        """A path that climbs out, reported the way `reject_host_path` reports one."""
        call = request.tool_call
        return ToolMessage(
            content=_refusal_text(escaped),
            tool_call_id=call.get("id", ""),
            name=call.get("name"),
            status="error",
        )


def tool_guards(names: frozenset[str], root: Path | None) -> list[AgentMiddleware]:
    """What every graph holding workspace tools gets wrapped in, built once.

    Three graphs hold them -- the agent, a delegate, and the `general-purpose` delegate
    deepagents supplies, which is handed the agent's tools and so holds the same objects.
    Composed here because each of the three was composed where it was built, and the one
    built last got none of this: a tool call through `general-purpose` reached the tool
    with its paths untranslated and a host path unrefused, which is the leak
    `WorkspaceToolPaths` exists to close.

    `HostPathGuard` is unconditional, for the reason it is unconditional above: the
    backend refuses a host path on every run, so the thing that turns that refusal into
    a correction the model can read has to be there whether or not this graph holds a
    workspace tool. The other two are about the tools themselves, so they need names --
    and translation needs somewhere to translate against, which a build with no session
    does not have.
    """
    guards: list[AgentMiddleware] = [HostPathGuard()]
    if names:
        # Beside each other and in this order, which the delegate's stack is pinned to:
        # both are about a workspace tool call, and the translation rewrites the
        # arguments before anything below it decides anything about them.
        guards.append(WorkspaceToolErrors(names))
        if root is not None:
            guards.append(WorkspaceToolPaths(names, root))
    return guards


class GuardedTool(BaseTool):
    """One workspace tool, carrying the guards a compiled delegate cannot be given.

    There is a fourth graph holding workspace tools, and `tool_guards` cannot reach
    it: a delegate the workspace compiled itself. deepagents runs that graph as
    given, so no middleware of kingfisher's is in front of its tools. Measured:
    `show-your-work` called `log_levels('/data/api.log')` and the tool raised
    `FileNotFoundError` on a path nothing had translated, ending the run. What
    kingfisher still owns is the list of objects handed to `build`, so the guards
    travel on the tools.

    **A refusal is returned, never raised.** Inside a graph kingfisher did not build
    nothing catches an exception -- measured for `ToolException`, `FileNotFoundError`
    and `ValueError` alike, each of which ended the run. `handle_tool_error` turns
    what this raises into a failed result before it leaves the tool, which also keeps
    `ToolMessage.status` true: `show_your_work` reports a call as failed by reading
    that field, so a refusal reported as success would be a worse answer than a
    crash.
    """

    #: Typed `Any` rather than `BaseTool` and `SessionPaths`: this is a pydantic
    #: model, and naming those would make the wrapper refuse a tool it can carry.
    inner: Any
    #: `None` where a build has no session to translate against, which is the
    #: condition `tool_guards` puts on `WorkspaceToolPaths` for the same reason.
    #: The error half still applies.
    paths: Any = None

    def _translated(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        if self.paths is None:
            return kwargs
        try:
            return self.paths.translated(kwargs)
        except (UnsafeReferenceError, HostPathError) as escaped:
            raise ToolException(_refusal_text(escaped)) from escaped

    def _as_tool_call(self, args: dict[str, Any]) -> dict[str, Any]:
        """The call form, which is the only one that carries an artifact back.

        A tool declaring `content_and_artifact` returns the pair through a
        `ToolMessage` and the plain form drops it, so a wrapper that invoked the
        plain way would quietly lose every artifact it passed on.
        """
        return {"type": "tool_call", "id": "guarded", "name": self.inner.name, "args": args}

    def _failed(self, exc: Exception) -> ToolException:
        # The type as well as the message, for the reason `WorkspaceToolErrors`
        # gives: a workspace tool's exceptions were not written to be read by a
        # model, and `FileNotFoundError: /data/x.csv` reads far better than the path.
        msg = f"Error: {type(exc).__name__}: {exc}"
        return ToolException(msg)

    def _run(self, **kwargs: Any) -> Any:
        args = self._translated(kwargs)
        try:
            if self.response_format == "content_and_artifact":
                answered = self.inner.invoke(self._as_tool_call(args))
                return answered.content, answered.artifact
            return self.inner.invoke(args)
        except ToolException:
            raise
        except Exception as exc:
            raise self._failed(exc) from exc

    async def _arun(self, **kwargs: Any) -> Any:
        args = self._translated(kwargs)
        try:
            if self.response_format == "content_and_artifact":
                answered = await self.inner.ainvoke(self._as_tool_call(args))
                return answered.content, answered.artifact
            return await self.inner.ainvoke(args)
        except ToolException:
            raise
        except Exception as exc:
            raise self._failed(exc) from exc


def guarded_tools(tools: Sequence[Any], root: Path | None) -> list[Any]:
    """The workspace tools a compiled delegate is handed, each one wrapped.

    The names are not needed here the way `tool_guards` needs them: everything in
    this list is a workspace tool already, chosen by the grant this delegate was
    resolved against.
    """
    paths = SessionPaths(root) if root is not None else None
    return [_guarded(one, paths) for one in tools]


def _guarded(one: Any, paths: SessionPaths | None) -> BaseTool:
    """One tool, wrapped without changing what it advertises.

    A plain function is made into the tool the graph would have made of it anyway --
    `create_agent` converts callables on the way in, and refuses one with no
    docstring exactly as this does. Normalising first is what lets a plain function
    report a refusal at all, since the reporting is `BaseTool` machinery.
    """
    inner = one if isinstance(one, BaseTool) else _as_tool(one)
    # `get_input_schema()` where a tool declares none: a `BaseTool` subclass carries
    # its arguments on `_run` instead, and a wrapper taking `**kwargs` would otherwise
    # advertise `kwargs` to the model and then be called with none of them.
    declared = inner.args_schema if inner.args_schema is not None else inner.get_input_schema()
    return GuardedTool(
        inner=inner,
        paths=paths,
        name=inner.name,
        description=inner.description,
        args_schema=declared,
        response_format=inner.response_format,
        return_direct=inner.return_direct,
        handle_tool_error=True,
    )


def _as_tool(one: Any) -> BaseTool:
    """A plain function as a tool, async or not."""
    if inspect.iscoroutinefunction(one):
        return StructuredTool.from_function(coroutine=one)
    return StructuredTool.from_function(one)


class WorkspaceToolErrors(AgentMiddleware):
    """Turn a workspace tool's exception into a failed tool result.

    A built-in reports its failures through `_tool_error` and the model carries on;
    `HostPathGuard` gives a rejected host path the same treatment. A workspace
    tool had neither, so upstream's default applied -- bad *arguments* are converted,
    everything else is re-raised -- and one wrong path killed a sixteen-call run.
    Measured, on one deployment: the same mistake through `read_file` cost nothing,
    and through `csv_profile` cost the run. Which of the two happened depended on the
    tool the model reached for, which the deployment cannot predict.
    """

    def __init__(self, names: frozenset[str]) -> None:
        super().__init__()
        self.names = names

    def _mine(self, request: Any) -> str | None:
        """The tool's name if this middleware speaks for it, else `None`."""
        name = request.tool_call.get("name")
        return name if name in self.names else None

    def _as_tool_error(self, request: Any, exc: Exception) -> ToolMessage:
        call = request.tool_call
        # The type as well as the message. A workspace tool is somebody else's
        # code and its exceptions were not written to be read by a model, so
        # `FileNotFoundError: /data/x.csv` reads far better than the path alone.
        return ToolMessage(
            content=f"Error: {type(exc).__name__}: {exc}",
            tool_call_id=call.get("id", ""),
            name=call.get("name"),
            status="error",
        )

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        try:
            return handler(request)
        except Exception as exc:
            if self._mine(request) is None:
                raise
            return self._as_tool_error(request, exc)

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        try:
            return await handler(request)
        except Exception as exc:
            if self._mine(request) is None:
                raise
            return self._as_tool_error(request, exc)
