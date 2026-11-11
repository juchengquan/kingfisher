"""What each chunk of a turn's stream says: the events it makes, and the answer it carries."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import replace
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langgraph.errors import GraphRecursionError
from langgraph.types import StreamMode

from kingfisher.core.definitions.subagents import TASK_TOOL
from kingfisher.core.result import RunEvent

from .runtime import SUBAGENT_TYPE_ARG, SUMMARISER, TOOLS_NODE, cut_off, tool_calls, usage_of

#: What langgraph raises out of `stream` when a turn uses up `recursion_limit`.
OutOfSteps = GraphRecursionError

#: How much of a provider's error message to keep. A tool result's share is
#: `Config.stream_result_chars`, and this is its default too.
PREVIEW = 300

#: A `messages` chunk is a (message, metadata) pair.
TOKEN_CHUNK_PARTS = 2

#: `updates` drives progress events; `values` carries the state, whose last emission
#: holds the answer; `messages` the model's output as it comes; `custom` what
#: kingfisher's middleware reports of a reply that never reaches the state; `tasks` the
#: call each tool task runs, the one place a subagent's chunks are tied to its `task` call.
STREAM_MODES: list[StreamMode] = ["updates", "values", "messages", "custom", "tasks"]

#: The key a middleware writes to the `custom` stream under, for a cut answer it asked
#: again and so kept out of the state, where a `model_call` event is otherwise read from.
CUT_OFF_MARK = "kingfisher.cut_off"

#: A program's `task()` dispatch, by its chain: the `eval` call's id, then its own. The
#: key on the metadata of every run inside it, and on the `custom` chunks announcing its
#: start and its answer, which no `tasks` or `updates` chunk carries: the sandbox calls
#: `task` past the `ToolNode`.
DISPATCH = "kingfisher.dispatch"


def dispatch_started(chain: Sequence[str], agent: str, asked: str) -> dict[str, Any]:
    """The `custom` chunk read as a program's dispatch's `delegation_start`."""
    return {DISPATCH: {"chain": list(chain), "agent": agent, "asked": asked}}


def dispatch_answered(call: str, answer: str) -> dict[str, Any]:
    """The `custom` chunk read as the `tool_result` ending a program's dispatch."""
    return {DISPATCH: {"call": call, "answer": answer}}


def cut_off_mark(reply: Any) -> dict[str, Any]:
    """What the `custom` stream is handed for a cut reply the state will never hold."""
    return {CUT_OFF_MARK: usage_of(reply)}


def _event_for(message: Any, *, result_chars: int) -> RunEvent | None:
    if isinstance(message, ToolMessage):
        return RunEvent(
            kind="tool_result",
            tool=getattr(message, "name", None),
            text=message.text[:result_chars],
            call=message.tool_call_id or None,
        )
    if isinstance(message, AIMessage):
        # One event per model turn, with its tools' arguments but not its prose, which
        # already arrived as tokens.
        names, args, ids = tool_calls(message)
        # `.text`, not `str()`, which renders the Responses API's content blocks as a repr.
        # A message with no text, tools or usage is a state shuffle, not a turn.
        if not (names or message.text.strip() or getattr(message, "usage_metadata", None)):
            return None
        return RunEvent(
            kind="model_call",
            tools=names,
            args=args,
            calls=ids,
            usage=usage_of(message),
            cut_off=cut_off(message),
        )
    return None


def messages_in(update: Any) -> list[Any]:
    """Messages carried by a node update, tolerating shapes that carry none."""
    # A node writing twice in one step streams a list of writes, as the model node does
    # when clearing runs inside the call; read as one mapping, the reply would be lost.
    if isinstance(update, (list, tuple)):
        return [message for write in update for message in messages_in(write)]
    if not isinstance(update, Mapping):
        return []
    messages = update.get("messages")
    if messages is None:
        return []
    return list(messages) if isinstance(messages, (list, tuple)) else [messages]


class SubagentNames:
    """Which subagent produced a chunk, learned from the stream as it arrives.

    Only `messages` chunks carry a name, in `lc_agent_name`; `updates` carry none. A
    subagent's first model call is streamed before the update reporting it, so the name
    is known in time -- driven, not assumed, by a test on a real two-level run.
    """

    def __init__(self) -> None:
        self._names: dict[tuple[str, ...], str] = {}

    def name(self, namespace: Any, metadata: Any = None) -> str | None:
        """The subagent a chunk belongs to, or `None` for the main agent."""
        key = tuple(namespace or ())
        if not key:
            return None
        if isinstance(metadata, Mapping) and (found := metadata.get("lc_agent_name")):
            self._names[key] = str(found)
        return self._names.get(key)


class Delegations:
    """Which delegations a chunk or a callback came from inside, learned as the turn runs.

    A namespace names each graph a chunk passed through by the id of the task that ran
    it, and a `task` call's id is known before anything inside it runs: langgraph
    announces a step's tasks, a `tools` task with the call it runs, before running any,
    and a tool's start callback carries its call's id beside its task's. So it is known
    again for each one a resumed turn re-enters.
    """

    def __init__(self) -> None:
        self._calls: dict[str, str] = {}
        #: Tasks seen running more than one `task` call.
        self._shared: set[str] = set()
        #: The chain of the program's dispatch each namespace seen stamped is inside.
        self._dispatches: dict[tuple[str, ...], tuple[str, ...]] = {}

    def started(self, namespace: Any, chunk: Any) -> RunEvent | None:
        """The `delegation_start` a `tasks` chunk announces, or `None`."""
        if not isinstance(chunk, Mapping) or chunk.get("name") != TOOLS_NODE:
            return None
        call = _one_call(chunk.get("input"))
        if call is None or call.get("name") != TASK_TOOL:
            return None
        made = str(call.get("id") or "")
        self.entered(str(chunk.get("id")), made)
        asked = call.get("args") or {}
        return RunEvent(
            kind="delegation_start",
            text=str(asked.get("description") or ""),
            agent=str(asked.get(SUBAGENT_TYPE_ARG) or "") or None,
            delegations=(*self.within(namespace), made),
        )

    def entered(self, task: str, call: str) -> None:
        """Note that the `tools` task `task` runs the `task` call `call`. A task seen
        running a second stands for neither: a tool's start arrives one call at a time,
        and nothing would say which a chunk inside belonged to."""
        if self._calls.setdefault(task, call) != call:
            self._shared.add(task)

    def dispatched(self, namespace: Any, metadata: Any) -> None:
        """Note which program's dispatch a namespace is inside, off a chunk stamped with
        it: the one kind that is, and the only word of it an `updates` chunk inside gets."""
        if isinstance(metadata, Mapping) and (chain := metadata.get(DISPATCH)):
            self._dispatches[tuple(namespace or ())] = tuple(str(one) for one in chain)

    def within(self, namespace: Any) -> tuple[str, ...]:
        """The delegations a chunk came from inside, outermost first; `()` for the main
        agent. A segment no `task` call started, a supplied graph's own subgraph, adds
        nothing."""
        key = tuple(namespace or ())
        # The longest: a second dispatch from one program runs a segment below the first.
        roots = [root for root in self._dispatches if key[: len(root)] == root]
        dispatch = self._dispatches[max(roots, key=len)] if roots else ()
        return (
            *dispatch,
            *(
                self._calls[ran]
                for segment in key
                if (ran := str(segment).partition(":")[2]) in self._calls
                and ran not in self._shared
            ),
        )


def _one_call(started: Any) -> Mapping[str, Any] | None:
    """The tool call a `tools` task runs. Only a task running one call can stand for it:
    with two, nothing would say which a chunk inside belonged to."""
    if isinstance(started, list) and len(started) == 1 and isinstance(started[0], Mapping):
        return started[0]
    return None


class SeenMessages:
    """Which messages each graph's conversation already held, read off `values` chunks.

    A node can write back a message it did not make, under the same id -- clearing, or
    the summariser clipping a result -- and its update reads like a new one. The id
    tells them apart: `values` arrives after a step's writes and `updates` before, so
    what is held here is the conversation as the step found it.
    """

    def __init__(self) -> None:
        self._ids: dict[tuple[str, ...], frozenset[str]] = {}

    def held(self, namespace: Any, state: Any) -> None:
        """Note what one graph's conversation holds, from a `values` chunk."""
        if not isinstance(state, Mapping):
            return
        self._ids[tuple(namespace or ())] = frozenset(
            one.id for one in messages_in(state) if getattr(one, "id", None) is not None
        )

    def rewritten(self, namespace: Any, message: Any) -> bool:
        """Whether an update's message is one this graph's conversation already held."""
        known = self._ids.get(tuple(namespace or ()), frozenset())
        return getattr(message, "id", None) in known


def _token_event(chunk: Any) -> RunEvent | None:
    """One `messages` chunk into a token event, or nothing."""
    if not isinstance(chunk, tuple) or len(chunk) != TOKEN_CHUNK_PARTS:
        return None
    message, metadata = chunk
    # The summariser calls its model inside the model node, so only the tag it sets
    # tells its tokens from the answer's.
    if isinstance(metadata, Mapping) and metadata.get("lc_source") == SUMMARISER:
        return None
    # Not the base `AIMessage`, which would admit the tool results this exists to exclude.
    if not isinstance(message, AIMessageChunk):
        return None
    text = message.text
    return RunEvent(kind="token", text=text) if text else None


def _dispatch_event(
    said: Mapping[str, Any],
    *,
    agent: str | None,
    delegations: tuple[str, ...],
    result_chars: int,
) -> RunEvent:
    """A program's dispatch starting or answering, written where its `eval` call runs."""
    if "chain" in said:
        return RunEvent(
            kind="delegation_start",
            text=str(said.get("asked") or ""),
            agent=str(said.get("agent") or "") or None,
            delegations=(*delegations, *(str(one) for one in said["chain"])),
        )
    return RunEvent(
        kind="tool_result",
        tool=TASK_TOOL,
        text=str(said.get("answer") or "")[:result_chars],
        call=str(said.get("call") or "") or None,
        agent=agent,
        delegations=delegations,
    )


def events_in(  # noqa: PLR0912, PLR0913 -- a branch per mode, and what the stream taught
    namespace: Any,
    mode: str,
    chunk: Any,
    subagent_names: SubagentNames,
    *,
    seen: SeenMessages,
    delegations: Delegations,
    result_chars: int = PREVIEW,
) -> Iterator[RunEvent]:
    """Translate one stream chunk into domain events."""
    if mode == "tasks":
        if (start := delegations.started(namespace, chunk)) is not None:
            yield start
        return
    named = subagent_names.name(namespace)
    within = delegations.within(namespace)
    if mode == "values":
        seen.held(namespace, chunk)
        return
    if mode == "custom":
        if not isinstance(chunk, Mapping):
            return
        if isinstance(usage := chunk.get(CUT_OFF_MARK), Mapping):
            yield RunEvent(
                kind="model_call",
                usage=dict(usage),
                cut_off=True,
                agent=named,
                delegations=within,
            )
        elif isinstance(said := chunk.get(DISPATCH), Mapping):
            yield _dispatch_event(
                said, agent=named, delegations=within, result_chars=result_chars
            )
        return
    if mode == "messages":
        if isinstance(chunk, tuple) and len(chunk) == TOKEN_CHUNK_PARTS:
            # Only a `messages` chunk carries metadata, so the maps learn here.
            named = subagent_names.name(namespace, chunk[1])
            delegations.dispatched(namespace, chunk[1])
            within = delegations.within(namespace)
        if (event := _token_event(chunk)) is not None:
            yield replace(event, agent=named, delegations=within)
        return
    if mode != "updates":
        return
    for update in (chunk or {}).values():
        for message in messages_in(update):
            if seen.rewritten(namespace, message):
                continue
            if (event := _event_for(message, result_chars=result_chars)) is not None:
                yield replace(event, agent=named, delegations=within)


def answer_in(namespace: Any, mode: str, chunk: Any) -> str | None:
    """What the assistant last said, from a `values` chunk that ends on its reply.

    `None` for a subagent's chunk, which is why this takes a namespace: their `values`
    arrive here too, and a turn cut short just after a subagent finished would report
    that subagent's answer as its own.
    """
    if namespace:
        return None
    if mode != "values":
        return None
    messages = messages_in(chunk)
    # Only the model's own reply: the first chunk ends on the caller's message and one
    # after the tools on a tool's output, and a turn stopped there answered with either.
    if not messages or not isinstance(messages[-1], AIMessage):
        return None
    reply = messages[-1]
    # A reply that only calls tools says nothing, so what was said before it stands.
    if reply.tool_calls and not reply.text:
        return None
    return reply.text
