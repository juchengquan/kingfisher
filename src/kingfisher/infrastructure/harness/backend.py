"""Filesystem + shell backend."""

from __future__ import annotations

import sys
from collections.abc import Awaitable, Callable, Iterator, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, Protocol

from deepagents.backends import CompositeBackend, FilesystemBackend, LocalShellBackend
from deepagents.backends.protocol import ExecuteResponse
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from kingfisher.config import Config, ConfigError
from kingfisher.domain.ports import CommandRunner
from kingfisher.domain.references import UnsafeReferenceError, within
from kingfisher.infrastructure.catalogue import Definitions
from kingfisher.infrastructure.sandbox import confinement
from kingfisher.kinds.subagents.spec import SubagentError
from kingfisher.layout import (
    AGENT_HOME,
    AGENT_TMP,
    BUNDLED_SKILLS_ROUTE,
    DATA,
    DATA_ROUTE,
    HARNESS,
    HARNESS_ROUTE,
    MEMORY,
    MEMORY_ROUTE,
    RESERVED_SKILL_FOLDER,
    SESSION_DIRS,
    SESSION_PLUMBING,
    SKILLS_ROUTE,
    routed_paths,
)

_BASE_PATH: tuple[str, ...] = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")


def agent_home(session_dir: Path) -> Path:
    """`HOME` for the agent's shell: per session, and disposable.

    Inside the session so that no new janitor is needed: `reap` already removes
    session directories, and `session_bytes` already counts everything in one, so a
    session that caches a gigabyte says so. Above the session, caches accumulated
    beside `skills/` with nothing sweeping them -- 59MB in one real workspace -- and
    counted toward no quota.
    """
    return Path(session_dir) / AGENT_HOME


def shell_env(
    cfg: Config, session_dir: Path, *, catalogue: Definitions | None = None
) -> dict[str, str]:
    """The explicit allowlist handed to the shell — no credentials.

    That is all it does. Redirecting `HOME` moves where a path is *resolved*; the
    files stay where they are, and an absolute path still reaches them. Measured on
    this machine, the shell could read `~/.aws` and `~/.config/gh` right through it.
    Keeping those closed is `confinement`'s job, not this function's.
    """
    path_parts = [str(Path(sys.executable).parent), *cfg.shell_path_extra, *_BASE_PATH]
    env = {
        "PATH": ":".join(path_parts),
        "HOME": str(agent_home(session_dir)),
        "LANG": "en_US.UTF-8",
        "LC_ALL": "en_US.UTF-8",
        "TMPDIR": str(session_dir / AGENT_TMP),
    }
    env["KINGFISHER_SKILLS"] = str((catalogue or Definitions.from_config(cfg)).skills.root)
    return env


#: Absolute prefixes that can never name a workspace directory, so a file-tool
#: path starting with one is a host path that was passed to the wrong kind of
#: tool. Deliberately a short, explicit list rather than a rule inferred from
#: the filesystem: it has to be readable, and it has to be the same on every
#: machine regardless of what happens to exist at `/`.
_HOST_ROOTS: tuple[str, ...] = (
    "/Users/",
    "/home/",
    # S108 reads a "/tmp" literal as insecure temp-file use. This is the
    # inverse: an entry in a deny-list, naming the prefix a file tool must
    # refuse. Nothing is written here.
    "/tmp/",  # noqa: S108
    "/private/",
    "/etc/",
    "/var/",
    "/usr/",
    "/opt/",
)


class HostPathError(ValueError):
    """A host path reached a file tool."""


def reject_host_path(key: str, workspace: Path) -> None:
    """Refuse a host path handed to a file tool."""
    if not key.startswith("/"):
        return

    prefix = f"{workspace}/"
    if key.startswith(prefix):
        suggestion = f"/{key[len(prefix) :]}"
        msg = (
            f"{key!r} is a host path, and file tools take virtual paths rooted at the "
            f"workspace. Use {suggestion!r} instead. (Passing the host path would have "
            f"created it inside the workspace, under a mirror of its own location.)"
        )
        raise HostPathError(msg)

    if key.startswith(_HOST_ROOTS):
        msg = (
            f"{key!r} is a host path, and file tools take virtual paths rooted at the "
            f"workspace — it would have been created inside the workspace, not where "
            f"you meant. Use the shell for host paths, or a virtual path such as "
            f"/runs/<session>/<turn>/ for files that belong to this task."
        )
        raise HostPathError(msg)


class ConfinedLocalShellBackend(LocalShellBackend):
    """`LocalShellBackend` with every command run through a confinement.

    `None` means run it here, which is what upstream already does -- a default runner
    would be 110 lines of upstream's truncation, timeout and exit-code shaping,
    copied to be kept in step. The confinement is applied either way, before the
    runner sees the command, so a runner cannot forget to.
    """

    def __init__(
        self,
        confined: confinement.Confinement,
        *,
        runner: CommandRunner | None = None,
        **kwargs: Any,
    ) -> None:
        self.confinement = confined
        self.runner = runner
        super().__init__(**kwargs)

    def execute(self, command: str, *, timeout: int | None = None) -> Any:
        if self.runner is None:
            return super().execute(self.confinement.wrap(command), timeout=timeout)
        # A confinement is a command prefix naming paths on *this* host, so
        # applying it to something that runs elsewhere produces a
        # `sandbox-exec -f /Users/.../shell.sb` shipped to a machine with no
        # such file -- which fails looking like a broken remote shell rather
        # than like a wrong prefix. `local` defaults to True so a runner that
        # says nothing keeps the fence.
        outcome = self.runner.run(
            self.confinement.wrap(command) if getattr(self.runner, "local", True) else command,
            timeout=timeout,
        )
        return ExecuteResponse(
            output=outcome.output,
            exit_code=outcome.exit_code,
            truncated=outcome.truncated,
        )


def _once(result: Any, *, key: Callable[[Any], Any]) -> Any:
    """A result with its repeated matches dropped, first occurrence kept."""
    if result.matches is None:
        return result
    seen: set[Any] = set()
    kept = []
    for one in result.matches:
        identity = key(one)
        if identity in seen:
            continue
        seen.add(identity)
        kept.append(one)
    return replace(result, matches=kept)


class WorkspaceScopedBackend(CompositeBackend):
    """A `CompositeBackend` that refuses host paths instead of re-rooting them.

    `glob` and `grep` are deduplicated. They merge every backend's answer, and two
    of the routes here point *inside* the default backend's own root -- `/data` and
    `/memory` are real directories under the session -- so each saw the same file
    twice: measured, one file supplied with `--data` came back as two matches with
    one path between them, on every pattern.
    """

    def glob(self, pattern: str, path: str | None = None) -> Any:
        """Every match, minus the ones a second backend already gave."""
        return _once(super().glob(pattern, path), key=lambda one: one.get("path"))

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> Any:
        """The same, keyed by the line rather than the file."""
        return _once(
            super().grep(pattern, path, glob, max_count=max_count),
            key=lambda one: (one.get("path"), one.get("line"), one.get("text")),
        )

    def __init__(
        self,
        default: Any,
        routes: dict[str, Any],
        *,
        workspace: Path,
    ) -> None:
        super().__init__(default=default, routes=routes)
        self.workspace = workspace

    def _get_backend_and_key(self, key: str) -> tuple[Any, str]:
        reject_host_path(key, self.workspace)
        return super()._get_backend_and_key(key)


def _bundles_with_skills(catalogue: Definitions) -> tuple[Any, ...]:
    """Every bundle that has skills to mount, or none."""
    try:
        bundles = catalogue.subagents.bundles
    except SubagentError:
        # A catalogue that will not parse has no bundles to mount, and this is
        # not the place that says so. `--list` exists to be run *because*
        # something is broken -- it catches the loader error and prints it over
        # the rest of the inventory -- and it builds a backend on the way.
        # Raising here took that listing down with it, which is the same shape
        # as warming inside `resolve_definitions`, and a test caught that one
        # too. `warm()` still refuses at startup, so nothing is being excused.
        return ()
    return tuple(bundle for bundle in bundles.values() if bundle.skills is not None)


def bundled_skills_route(where: str) -> str:
    """The route one bundle's skills are mounted at."""
    return f"{BUNDLED_SKILLS_ROUTE}{where}/"


def skills_sources(folders: tuple[str, ...] = ()) -> list[tuple[str, str]]:
    """Every place the agent should look for skills, labelled."""
    if RESERVED_SKILL_FOLDER in folders:
        # Refused rather than skipped: `subagents/` under the skills root would
        # shadow *every* bundle at once, so the skills of every delegate that has
        # any would silently stop being found. A folder that vanishes is the
        # failure this package keeps naming, and the fix is one rename.
        msg = (
            f"the skills catalogue has a folder called {RESERVED_SKILL_FOLDER!r}, "
            f"which is where each subagent's own skills are mounted "
            f"({BUNDLED_SKILLS_ROUTE}). Rename it -- left as it is, it would hide "
            "every bundled skill in the deployment"
        )
        raise ConfigError(msg)
    return [
        (SKILLS_ROUTE, "catalogue"),
        *((f"{SKILLS_ROUTE}{name}/", name) for name in folders),
    ]


#: The one memory file the agent is told to read. `/memory/` is a route so a
#: request that declined memory can be given a deny rule for it.
MEMORY_SOURCES = [f"{MEMORY_ROUTE}AGENTS.md"]


def _fence_for(
    cfg: Config,
    session_dir: Path,
    confined: confinement.Confinement,
    skills_dir: Path | None,
    env: Mapping[str, str],
) -> CommandRunner | None:
    """The Linux fence, when the confinement says there is one."""
    if confined.mechanism not in ("bubblewrap", "Landlock"):
        return None

    # One answer for both fences rather than the same three lines twice. `argv_for` and
    # `policy_for` take the same arguments and mean the same thing by them, so a path
    # added to one branch and not the other fences the shell differently depending on
    # which mechanism the host happens to have -- and the suite would not catch it,
    # because a run only ever exercises the one mechanism its own kernel offers. That is
    # the divergence this function already avoids one question earlier by deriving from
    # `Confinement`.
    #
    # Not `readable_roots`, which is the obvious call and the wrong one: it also returns
    # the *workspace*, which is right where the home is denied and the workspace
    # re-allowed inside it, and catastrophic here. Sessions live under the workspace, so
    # granting it hands every tenant back the directory this fence exists to take away
    # -- measured, before the fence: tenant B read tenant A's `derived/secret.txt` with
    # `cat ../<A>/...`, exit 0.
    readable = [*confinement.toolchain_roots(cfg.shell_path_extra)]
    if skills_dir is not None:
        readable.append(skills_dir)
    # Nothing beyond the session, which both fences grant already: `TMPDIR` is
    # inside it now, so the writable set is exactly the session this turn owns.
    writable: list[Path] = []

    if confined.mechanism == "bubblewrap":
        from kingfisher.infrastructure.sandbox.bubblewrap import (  # noqa: PLC0415
            BubblewrapRunner,
            argv_for,
        )

        return BubblewrapRunner(
            argv_for(session_dir, readable=readable, writable=writable),
            env=env,
        )

    # Imported here for the reason the module explains: `sandlock` is a
    # Linux-only optional install, and this function is called on every turn on
    # every platform.
    from kingfisher.infrastructure.sandbox.fence import LandlockRunner, policy_for  # noqa: PLC0415

    return LandlockRunner(
        policy_for(session_dir, readable=readable, writable=writable),
        cwd=session_dir,
        env=env,
    )


def _require_layout(session_dir: Path) -> None:
    """Refuse a session directory that has not been made yet."""
    missing = [
        name
        for name in (*SESSION_DIRS, *SESSION_PLUMBING)
        if not (session_dir / name).is_dir()
    ]
    if missing:
        msg = (
            f"session directory {session_dir} is missing {', '.join(missing)}; "
            "call ensure_session_layout on it before building a backend"
        )
        raise ValueError(msg)


class BackendFactory(Protocol):
    """How a deployment says what filesystem its agents run on.

    Typed against the call and not the return, which is the asymmetry worth
    knowing. deepagents decides what a backend *is* with `isinstance` against its
    own abstract base class, so a protocol describing the return would describe the
    requirement wrongly -- an object satisfying it exactly would still be handed no
    shell. `refuse_unusable_backend` asks that question at the only time it can be
    answered, which is once there is an object to ask about.

    What a type can settle is the call, and there is one mistake here it is the only
    thing that can catch. A factory written without `runner` drops the
    `CommandRunner` the deployment wired, and nothing downstream can tell: the
    backend that comes back is well-formed and passes every check, and merely runs
    its commands somewhere the deployment did not choose.
    """

    def __call__(
        self,
        cfg: Config,
        session_dir: Path,
        # Positional-only, or this would be dictating parameter *names*: a protocol
        # matches those, so without the slash a deployment whose factory reads
        # `(config, where)` fails to satisfy it for no reason anybody could act on.
        # Both are passed positionally, so nothing is given up.
        /,
        *,
        catalogue: Definitions | None = None,
        runner: CommandRunner | None = None,
    ) -> Any: ...


def default_backend(
    cfg: Config,
    session_dir: Path,
    *,
    catalogue: Definitions | None = None,
    runner: CommandRunner | None = None,
) -> WorkspaceScopedBackend:
    """Kingfisher's own backend, rooted at one session.

    Typed to the class rather than to deepagents' protocol because a deployment
    adjusting what this built reads `default` and `routes` off it, and the protocol
    carries neither.
    """
    skills_dir = (catalogue or Definitions.from_config(cfg)).skills.root

    _require_layout(session_dir)
    # `FilesystemBackend` wants the root to exist. A *supplied* catalogue was
    # already refused by `resolve_definitions` if it did not, so this only ever
    # creates a derived one -- and stays here for the callers that build a
    # backend directly, without a service to have resolved anything for them.
    skills_dir.mkdir(parents=True, exist_ok=True)

    confined = confinement.shell_confinement(cfg, skills=skills_dir)
    env = shell_env(cfg, session_dir, catalogue=catalogue)
    if runner is None:
        chosen = _fence_for(cfg, session_dir, confined, skills_dir, env)
    else:
        # Said once, here, rather than left for a reader to work out from two
        # places. What confines the shell depends on what runs the command, and
        # a supplied runner that is not local receives nothing this process
        # applied -- so the confinement has to stop claiming otherwise.
        chosen = runner
        confined = confinement.with_supplied_runner(
            confined, local=getattr(runner, "local", True)
        )
    shell = ConfinedLocalShellBackend(
        confined,
        runner=chosen,
        root_dir=str(session_dir),
        env=env,
        timeout=cfg.execution_timeout_s,
    )
    # What backs each path. Keyed by the table rather than written as one dict
    # so that a route declared in `kingfisher.layout` and forgotten here raises when
    # the backend is built, instead of reaching a turn as a path that resolves
    # to the default backend and quietly ignores its own deny rule.
    backing = {
        DATA_ROUTE: lambda: FilesystemBackend(root_dir=str(session_dir / DATA)),
        SKILLS_ROUTE: lambda: FilesystemBackend(root_dir=str(skills_dir)),
        MEMORY_ROUTE: lambda: FilesystemBackend(root_dir=str(session_dir / MEMORY)),
        # Mounted so it can be refused. Every operation through it is denied by
        # `read_only_permissions`, and a rule is only expressible against a path
        # the composite routes -- so the mount is what makes the refusal legal,
        # not a way in.
        HARNESS_ROUTE: lambda: FilesystemBackend(root_dir=str(session_dir / HARNESS)),
    }
    missing = [path for path in routed_paths() if path not in backing]
    if missing:  # pragma: no cover -- a table edit, caught by its own test
        msg = f"routes declared in kingfisher.layout with nothing to back them: {missing}"
        raise ConfigError(msg)

    routes: dict[str, Any] = {path: backing[path]() for path in routed_paths()}
    # One per bundle, so a delegate's own skills are readable by the file tools
    # that read every other skill -- and read-only for the same two reasons,
    # since both enforcement points are scoped to `/skills/` and this sits
    # underneath it. Generated rather than declared: the names are not known
    # until a catalogue is read, which is what `family=True` marks.
    routes.update({
        bundled_skills_route(bundle.where): FilesystemBackend(root_dir=str(bundle.skills))
        for bundle in _bundles_with_skills(catalogue or Definitions.from_config(cfg))
    })

    return WorkspaceScopedBackend(default=shell, routes=routes, workspace=session_dir)


# `HostPathGuard` lives beside `reject_host_path` rather than with the other
# middleware, and the two are one mechanism: this catches what that raises. It
# applies no capability, which is what keeps it out of `narrowing`.


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
NOT_FOR_TOOLS: tuple[str, ...] = (*_HOST_ROOTS, "/root/", "/proc/", "/sys/", "/dev/")


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


class WorkspaceToolPaths(AgentMiddleware):
    """Translate the agent's own paths into real ones, per session.

    **It closes a leak and a usability bug with one change, and the second is how the
    first was found.** `system.md` teaches virtual paths and says the two views do
    not mix; the tools wanted host paths and the agent is never told one. Measured in
    a real run: the model passed `/data/config.ini` and the tool raised
    `FileNotFoundError`. The only way it could succeed was to go looking -- `pwd` in
    the shell, learn the layout -- and from there it can name *any* session:
    `line_count('/workspace/sessions/<other>/secret.txt')` returned an answer.

    Rewriting the call rather than wrapping each tool, because the tools are not
    alike: some are `BaseTool`s from `@tool` and some are plain functions. The call
    is the one shape they share, and langgraph documents the rewrite --
    `{**request.tool_call, "args": {...}}`.
    """

    def __init__(self, names: frozenset[str], session_dir: Path) -> None:
        self.names = names
        self.session_dir = Path(session_dir)
        # The directory this session's siblings are in, spelled both ways. In a
        # container the workspace is `/workspace`, which no host root names, and
        # another session is one directory over from this one.
        siblings = self.session_dir.parent
        self._refused = (
            *NOT_FOR_TOOLS,
            *{f"{root}/" for root in (str(siblings), str(siblings.resolve()))},
        )
        super().__init__()

    def _host_path_in(self, args: Mapping[str, Any]) -> str | None:
        """The first host path in an argument that is not `path`, or `None`."""
        for key, value in args.items():
            if key in PATH_ARGUMENTS:
                continue
            for text in _strings_in(value):
                if text.startswith(self._refused) or f"{text}/" in self._refused:
                    return text
        return None

    def _translated(self, request: Any) -> Any:
        """The same call with its path arguments made real, or the request
        unchanged when it names no tool of ours and no path."""
        call = request.tool_call
        if call.get("name") not in self.names:
            return request
        args = call.get("args") or {}
        if (host := self._host_path_in(args)) is not None:
            msg = f"{host!r} is a host path, and a tool is handed this session's own paths"
            raise HostPathError(msg)
        wanted = {key: args[key] for key in args if key in PATH_ARGUMENTS}
        if not wanted:
            return request
        return replace(
            request,
            tool_call={
                **call,
                "args": {**args, **{key: self._real(value) for key, value in wanted.items()}},
            },
        )

    def _real(self, value: Any) -> Any:
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
            content=(
                f"Error: {escaped}. Tool paths are the same virtual paths the file "
                "tools take, rooted at this session -- `/data/<name>`, "
                "`/derived/<name>` -- and cannot climb out of it."
            ),
            tool_call_id=call.get("id", ""),
            name=call.get("name"),
            status="error",
        )


class WorkspaceToolErrors(AgentMiddleware):
    """Turn a workspace tool's exception into a failed tool result.

    A built-in reports its failures through `_tool_error` and the model carries on;
    `HostPathGuard` below gives a rejected host path the same treatment. A workspace
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


class HostPathGuard(AgentMiddleware):
    """Turn a rejected host path back into something the agent can act on."""

    def _as_tool_error(self, request: Any, exc: HostPathError) -> ToolMessage:
        call = request.tool_call
        return ToolMessage(
            content=f"Error: {exc}",
            tool_call_id=call.get("id", ""),
            name=call.get("name"),
            status="error",
        )

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        try:
            return handler(request)
        except HostPathError as exc:
            return self._as_tool_error(request, exc)

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        try:
            return await handler(request)
        except HostPathError as exc:
            return self._as_tool_error(request, exc)
