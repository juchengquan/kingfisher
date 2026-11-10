"""Stand-ins more than one test module drives kingfisher with, and readers of what it
builds.

Here rather than in the test module that needed one first: a test module is cut and
split by what it tests, not by who borrows from it.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from deepagents.backends import CompositeBackend, FilesystemBackend, LocalShellBackend
from deepagents.middleware.summarization import SummarizationMiddleware
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from kingfisher import MemoryChanges, MemoryEntry
from kingfisher.core.definitions.catalogue import Definitions
from kingfisher.core.interfaces import (
    CommandResult,
    CommandRunner,
    MemoryStore,
    PdfAttachments,
    Sessions,
    UnusedResults,
)
from kingfisher.core.layout import DEFAULT_LAYOUT, HARNESS, SKILLS_ROUTE
from kingfisher.plugins import LocalSessions
from tests.conftest import FakeGraph, FakeToolCallingModel


class StubAgent(FakeGraph):
    """Stands in for the compiled graph so the orchestration is testable."""

    def __init__(
        self,
        answer: str,
        *,
        updates: list | None = None,
        tokens: list | None = None,
        subagent: tuple | None = None,
    ) -> None:
        self.answer = answer
        self.updates = updates or []
        self.tokens = tokens or []
        self.subagent = subagent or ()
        self.state: dict | None = None
        self.config: dict | None = None

    def stream(self, state, config, stream_mode=None, subgraphs=False):
        self.state, self.config = state, config
        for chunk in self.tokens:
            yield (self.subagent, "messages", chunk)
        for update in self.updates:
            yield (self.subagent, "updates", update)
        yield ((), "values", {"messages": [AIMessage(content=self.answer)]})

    async def astream(self, state, config, stream_mode=None, subgraphs=False):
        """The async twin, because a compiled graph has one and `astream` drives it.

        A stub offering only `stream` would make every async test fail for the
        stub's reason rather than the code's -- and one offering only this would
        hide a sync path that had stopped working.
        """
        for chunk in self.stream(state, config, stream_mode, subgraphs):
            yield chunk

    def get_state(self, config):
        """What a real graph holds when the turn ends."""
        del config
        sent = list((self.state or {}).get("messages", []))
        return SimpleNamespace(values={"messages": [*sent, AIMessage(content=self.answer)]})


class ElsewhereFiles(CompositeBackend):
    """One session's filesystem in a directory kingfisher is never told about."""

    def __init__(self, kept: Path, skills: Path) -> None:
        super().__init__(
            default=LocalShellBackend(root_dir=kept),
            routes={
                route: FilesystemBackend(
                    root_dir=skills if route == SKILLS_ROUTE else kept / route.strip("/")
                )
                for route in DEFAULT_LAYOUT.routed_paths()
            },
        )


class Elsewhere(Sessions):
    """`Sessions` keeping each session under `root`, never in its directory.

    What a remote sandbox looks like from here: a working filesystem the agent can
    read, write and run commands in, sharing nothing with the workspace kingfisher
    holds. Routed the way `route_coverage` asks, so it is refused for nothing but the
    thing under test.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def open(self, cfg, session_id, /, *, catalogue=None):
        kept = self.root / session_id
        for name in (*DEFAULT_LAYOUT.session_dirs, HARNESS):
            (kept / name).mkdir(parents=True, exist_ok=True)
        skills = (catalogue or Definitions.from_config(cfg)).skills.root
        return ElsewhereFiles(kept, skills)

    def sessions(self, cfg):
        if not self.root.is_dir():
            return ()
        return tuple((p.name, p.stat().st_mtime) for p in self.root.iterdir() if p.is_dir())

    def mark_used(self, cfg, session_id):
        (self.root / session_id).touch()

    def size(self, cfg, session_id):
        return sum(p.stat().st_size for p in (self.root / session_id).rglob("*") if p.is_file())

    def delete(self, cfg, session_id):
        shutil.rmtree(self.root / session_id, ignore_errors=True)

    # A lock that never goes stale, which is all a test double needs.
    def claim(self, cfg, session_id, /, *, stale_after, now=None):
        lock = self.root / session_id / HARNESS / "claim"
        lock.parent.mkdir(parents=True, exist_ok=True)
        try:
            lock.mkdir()
        except FileExistsError:
            return None
        (lock / "token").write_text(session_id, encoding="utf-8")
        return session_id

    def release(self, cfg, session_id, token, /):
        if self.claimed_by(cfg, session_id, token):
            shutil.rmtree(self.root / session_id / HARNESS / "claim", ignore_errors=True)

    def claimed_by(self, cfg, session_id, token, /):
        held = self.root / session_id / HARNESS / "claim" / "token"
        return held.is_file() and held.read_text(encoding="utf-8") == token

    # Never stale, so never taken over.
    def took_over(self, cfg, session_id, token, /):
        return False


class ElsewhereRunner(CommandRunner):
    """A runner that ships its commands off this machine, which is the case where
    losing it matters: the confinement names paths on *this* host.
    """

    @property
    def local(self) -> bool:
        return False

    def run(
        self, command: str, *, timeout: int | None = None
    ) -> CommandResult:  # pragma: no cover -- wired, never driven: no turn is run here
        raise NotImplementedError


class Sent(ChatAnthropic):
    """Keeps every request as it would go out, cache breakpoints and all, and answers from
    a script. A `ChatAnthropic` because only one is sent breakpoints."""

    sent: list[dict[str, Any]] = Field(default_factory=list)
    script: list[AIMessage] = Field(default_factory=list)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.sent.append(self._get_request_payload(messages, stop=stop, **kwargs))
        reply = self.script.pop(0) if self.script else AIMessage(content="ok")
        return ChatResult(generations=[ChatGeneration(message=reply)])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return self._generate(messages, stop=stop, **kwargs)


class Shown(FakeToolCallingModel):
    """Keeps what it was shown on every call."""

    shown: list[list[Any]] = []

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.shown.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def subagent_graphs(graph):
    """The compiled subagents, which live in the `task` tool's closure."""
    node = getattr(graph, "nodes", {}).get("tools")
    task_tool = getattr(getattr(node, "bound", None), "tools_by_name", {}).get("task")
    found: dict = {}

    def walk(obj, depth=0):
        if depth > 4 or found:
            return
        for attribute in ("func", "__closure__"):
            value = getattr(obj, attribute, None)
            if attribute == "__closure__":
                for cell in value or ():
                    try:
                        contents = cell.cell_contents
                    except ValueError:
                        continue
                    if isinstance(contents, dict) and any(
                        hasattr(v, "nodes") for v in contents.values()
                    ):
                        found.update(contents)
                        return
                    walk(contents, depth + 1)
            elif value is not None:
                walk(value, depth + 1)

    if task_tool is not None:
        walk(task_tool)
    return found


def tools_of(graph) -> set[str]:
    node = getattr(graph, "nodes", {}).get("tools")
    by_name = getattr(getattr(node, "bound", None), "tools_by_name", {})
    return set(by_name)


def claim_of(cfg, session_id: str) -> Path:
    """One session's turn slot, which lives inside the session it guards."""
    from kingfisher.plugins.sessions.backend import CLAIM

    return cfg.workspace / "sessions" / session_id / HARNESS / CLAIM


def through_hooks(stack, call: dict, tool, way: str = "sync"):
    """What the model reads back for `call`, through each tool hook of `stack` in the
    order langchain composes them, the first outermost."""
    from functools import partial

    from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
    from langchain.tools import ToolRuntime
    from langchain_core.messages.tool import tool_call

    state = {"messages": []}
    runtime = ToolRuntime(
        state=state,
        context=None,
        config={},
        stream_writer=lambda _chunk: None,
        tool_call_id=call["id"],
        store=None,
    )
    request = ToolCallRequest(
        tool_call=tool_call(name=call["name"], args=call["args"], id=call["id"]),
        tool=tool,
        state=state,
        runtime=runtime,
    )
    hooked = [
        one for one in stack if type(one).wrap_tool_call is not AgentMiddleware.wrap_tool_call
    ]
    if way == "sync":
        handler = partial(_ran, tool)
        for one in reversed(hooked):
            handler = partial(one.wrap_tool_call, handler=handler)
        return handler(request)
    ahandler = partial(_aran, tool)
    for one in reversed(hooked):
        ahandler = partial(one.awrap_tool_call, handler=ahandler)
    return asyncio.run(ahandler(request))


def _ran(tool, request):
    return tool.invoke({"type": "tool_call", **request.tool_call})


async def _aran(tool, request):
    return await tool.ainvoke({"type": "tool_call", **request.tool_call})


@dataclass(frozen=True)
class Plain(CommandRunner):
    """The runner a deployment writes first: it runs the command it is handed, and is
    handed nothing else. Not local, so no fence of this host's is put in front of it.
    """

    @property
    def local(self) -> bool:
        return False

    def run(self, command: str, *, timeout: int | None = None) -> CommandResult:
        done = subprocess.run(  # noqa: S602 -- `execute` is a shell by definition
            command, shell=True, capture_output=True, text=True, timeout=timeout, check=False
        )
        return CommandResult(
            output=done.stdout + done.stderr, exit_code=done.returncode, truncated=False
        )


class Here(Plain):
    """A runner a deployment supplies that runs its commands on this host."""

    @property
    def local(self) -> bool:
        return True


#: Where `Early` fires: well under deepagents' 170,000 on a model with no profile.
EARLY = 100_000


class Early(SummarizationMiddleware):
    """deepagents' summariser in its own place, firing at `EARLY` tokens."""

    name = "early"
    wants = frozenset({"model", "backend"})

    def __init__(self, model: Any, backend: Any) -> None:
        super().__init__(model, backend=backend, trigger=("tokens", EARLY))
        self.name = "SummarizationMiddleware"


@dataclass
class _Asked:
    """The two things `_input_budget` reads off a request."""

    model: Any
    model_settings: dict[str, Any]


class SlowAgent(FakeGraph):
    """A turn that keeps taking steps, as a real one can for hours."""

    def __init__(self, steps: int = 50) -> None:
        self.steps = steps
        self.taken = 0

    def stream(self, state, config, stream_mode=None, subgraphs=False):
        for _ in range(self.steps):
            self.taken += 1
            yield ((), "updates", {"agent": {"messages": [AIMessage(content="working")]}})
        yield ((), "values", {"messages": [AIMessage(content="done")]})


class _Ordered(LocalSessions):
    """Kingfisher's own backends, noting every file written or deleted through one, in order."""

    def __init__(self) -> None:
        super().__init__()
        self.written: list[str] = []
        #: The paths of each save, a list a save.
        self.saves: list[list[str]] = []

    def open(self, cfg, session_id, /, *, catalogue=None):
        opened = super().open(cfg, session_id, catalogue=catalogue)
        upload = opened.upload_files

        def noted(files):
            self.written.extend(path for path, _ in files)
            self.saves.append([path for path, _ in files])
            return upload(files)

        opened.upload_files = noted  # ty: ignore[invalid-assignment]
        delete = opened.delete

        def deleted(path):
            self.written.append(f"deleted {path}")
            return delete(path)

        opened.delete = deleted  # ty: ignore[invalid-assignment]
        return opened

    def at(self, name: str) -> int:
        """Where the first write of a path ending in `name` came."""
        return next(i for i, path in enumerate(self.written) if path.endswith(name))


class Shelf(MemoryStore):
    """A store in a dict, recording every call, merging by id as a store should."""

    def __init__(self, **users: tuple[MemoryEntry, ...]) -> None:
        self.users: dict[str, tuple[MemoryEntry, ...]] = dict(users)
        self.calls: list[tuple[Any, ...]] = []

    def key(self, caller) -> str | None:
        self.calls.append(("key",))
        return caller.context.get("user_id")

    def load(self, key: str) -> tuple[MemoryEntry, ...]:
        self.calls.append(("load", key))
        return self.users.get(key, ())

    def apply(self, key: str, changes: MemoryChanges) -> tuple[MemoryEntry, ...]:
        self.calls.append(("apply", key, changes))
        kept = {one.id: one for one in self.users.get(key, ())}
        for one in changes.removed:
            kept.pop(one.id, None)
        for one in (*changes.changed, *changes.added):
            kept[one.id] = one
        self.users[key] = tuple(kept.values())
        return self.users[key]


#: Written into what a turn is asked, handed and answered, and into what a failing hook
#: raises, so that finding it in a run event is content leaking.
SAID = "q7-said-q7"


class _FailingPdfs(PdfAttachments):
    def replacement(self, pdf):
        raise RuntimeError(SAID)


class _FailingSweep(UnusedResults):
    def settle(self, found):
        raise RuntimeError(SAID)
