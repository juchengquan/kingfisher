"""A model a turn the service runs can be scripted with.

A module of its own rather than a class in `conftest`, because pytest imports
`conftest` under a name of its own: a test importing `tests.conftest` gets a second
copy of the class, and a script queued on it never reaches the one the service
builds.
"""

from __future__ import annotations

import json
from collections import deque
from typing import ClassVar

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult


class Scripted(BaseChatModel):
    """A model the service builds for itself, from the catalogue row naming this class.

    `Kingfisher` takes no model: it builds one per turn out of `cfg.models`. So a turn
    whose graph kingfisher assembled -- the only kind that is driven with a context --
    can be scripted only through the row. The script is on the class because a resume
    is a second graph and a second instance, and has to carry on where the first
    stopped.
    """

    model: str
    base_url: str
    api_key: str
    max_tokens: int
    timeout: float

    #: A reply, or an error the call raises in its place, as a provider's refusal is raised.
    script: ClassVar[deque[AIMessage | Exception]] = deque()
    #: What each call was handed, in order: what the model saw, after every middleware.
    heard: ClassVar[list[list[BaseMessage]]] = []
    #: How many times deepagents' summariser asked for a summary.
    summaries: ClassVar[int] = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        # The summariser calls the agent's own model from inside a model call, tagged.
        # Answered here rather than scripted, so a script lists the agent's turns
        # whether or not a summary falls between them.
        if run_manager is not None and run_manager.metadata.get("lc_source") == "summarization":
            type(self).summaries += 1
            return ChatResult(generations=[ChatGeneration(message=AIMessage("a summary"))])
        self.heard.append(list(messages))
        reply = self.script.popleft()
        if isinstance(reply, Exception):
            raise reply
        return ChatResult(generations=[ChatGeneration(message=reply)])


class Streamed(Scripted):
    """`Scripted`, streaming each reply's prose a few characters at a time, as a
    provider does: the only way a turn's narration reaches its reader as tokens."""

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        reply = self._generate(messages, stop, run_manager, **kwargs).generations[0].message
        text = reply.text
        for start in range(0, len(text), 4):
            piece = AIMessageChunk(content=text[start : start + 4])
            if run_manager is not None:
                run_manager.on_llm_new_token(piece.text, chunk=ChatGenerationChunk(message=piece))
            yield ChatGenerationChunk(message=piece)
        if reply.tool_calls:
            calls = [
                {"name": c["name"], "args": json.dumps(c["args"]), "id": c["id"], "index": i}
                for i, c in enumerate(reply.tool_calls)
            ]
            yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=calls))
