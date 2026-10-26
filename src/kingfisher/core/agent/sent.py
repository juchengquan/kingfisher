"""Catching the first model call a graph makes, and naming where each part of it came from."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from deepagents.middleware.skills import SKILLS_SYSTEM_PROMPT
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import SystemMessage

from kingfisher.core.agent.middlewares.builtin_tools import MAPPINGS, TODOS
from kingfisher.core.agent.middlewares.turn_instructions import TurnInstructionsMiddleware
from kingfisher.core.definitions.tools.spec import tool_name
from kingfisher.core.layout import DEFAULT_LAYOUT
from kingfisher.core.result import Sent, SentBlock, SentTool

from .prompting import memory_guidelines

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .assembly import Assembled

#: How each block a middleware appends opens, by the source it is named for. Read off what
#: writes each, so a reworded heading reaches here rather than leaving its block `unknown`;
#: langchain-quickjs keeps its template private, so its heading is spelled here, and
#: `test_no_sandbox_outlives_the_look` reads the block it writes.
_OPENINGS = (
    ("host_paths", MAPPINGS),
    ("write_todos", TODOS),
    ("skills", SKILLS_SYSTEM_PROMPT.lstrip().splitlines()[0]),
    ("interpreter", "### Interpreter"),
    ("memory", memory_guidelines(None, DEFAULT_LAYOUT).lstrip().splitlines()[0]),
)


class _Caught(BaseException):
    """Not an `Exception`, so a middleware answering in the model's place when a call
    fails does not carry the turn on and run the tools its answer calls."""


class _Catch(BaseCallbackHandler):
    """Keeps what the first chat model start was handed, and stops the call there.

    A callback rather than a middleware: deepagents lands a new middleware ahead of its
    caching and memory, so one would see the request before they finish it.
    """

    raise_error = True

    def __init__(self) -> None:
        self.messages: list[Any] | None = None
        self.params: Mapping[str, Any] = {}

    def on_chat_model_start(self, serialized: Any, messages: Any, **kwargs: Any) -> None:
        del serialized
        self.messages = list(messages[0])
        self.params = kwargs.get("invocation_params") or {}
        raise _Caught


def first_call(built: Assembled, task: str, *, agent: str, session_id: str) -> Sent:
    """What `built`'s first model call is sent for `task`, caught before it leaves."""
    catch = _Catch()
    with suppress(_Caught):
        built.graph.invoke(
            {"messages": [{"role": "user", "content": task}]},
            context=built.context,
            config={"callbacks": [catch]},
        )
    if catch.messages is None:
        msg = "the graph finished without calling a model, so there is nothing it sends"
        raise RuntimeError(msg)
    system = next((m for m in catch.messages if isinstance(m, SystemMessage)), None)
    turn = next(
        (m.text for m in built.middleware if isinstance(m, TurnInstructionsMiddleware)), None
    )
    offered = frozenset(tool_name(tool) for tool in built.tools or ())
    return Sent(
        agent=agent,
        session_id=session_id,
        system=labelled(_blocks(system), built.system_prompt, turn),
        tools=tuple(sent_tool(tool, offered) for tool in catch.params.get("tools") or ()),
    )


def labelled(
    blocks: Sequence[tuple[str, bool]], prompt: str, turn: str | None
) -> tuple[SentBlock, ...]:
    """Each `(text, cached)` block of a system message, named for what wrote it.

    The first opens with `prompt`, what kingfisher assembled; deepagents writes a model's
    profile onto its end. A first block that does not open with it is some other call,
    made before the agent's, and naming it would describe the wrong request.
    """
    if not blocks or not blocks[0][0].startswith(prompt):
        msg = (
            "the first model call was not the agent's: its system message does not open "
            "with the prompt kingfisher assembled, so something called a model before it"
        )
        raise ValueError(msg)
    (first, cached), rest = blocks[0], blocks[1:]
    profile = first[len(prompt) :]
    named = [SentBlock(prompt, "kingfisher", cached=cached and not profile.strip())]
    if profile.strip():
        named.append(SentBlock(profile, "profile", cached=cached))
    named.extend(SentBlock(text, _source(text, turn), cached=marked) for text, marked in rest)
    return tuple(named)


def _source(text: str, turn: str | None) -> str:
    opening = text.lstrip()
    if turn is not None and text.strip() == turn.strip():
        return "turn"
    return next((name for name, heading in _OPENINGS if opening.startswith(heading)), "unknown")


def _blocks(system: SystemMessage | None) -> list[tuple[str, bool]]:
    """`(text, cached)` for each block of `system`, a plain string being one block."""
    if system is None:
        return []
    if isinstance(system.content, str):
        return [(system.content, False)]
    found = []
    for block in system.content:
        if isinstance(block, str):
            found.append((block, False))
        elif isinstance(block, Mapping) and block.get("type") == "text":
            found.append((str(block.get("text", "")), "cache_control" in block))
    return found


def sent_tool(tool: Any, offered: frozenset[str]) -> SentTool:
    """One tool as the provider is sent it: Anthropic's `input_schema`, or OpenAI's
    `parameters`, flat or under `function`."""
    if not isinstance(tool, Mapping):
        name = str(getattr(tool, "name", tool))
        return SentTool(name, str(getattr(tool, "description", "")), {}, offered=name in offered)
    spec = tool.get("function", tool)
    name = str(spec.get("name", ""))
    return SentTool(
        name=name,
        description=str(spec.get("description", "")),
        parameters=spec.get("input_schema", spec.get("parameters", {})),
        offered=name in offered,
        cached="cache_control" in tool,
    )
