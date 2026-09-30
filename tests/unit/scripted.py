"""A model a turn the service runs can be scripted with.

A module of its own rather than a class in `conftest`, because pytest imports
`conftest` under a name of its own: a test importing `tests.conftest` gets a second
copy of the class, and a script queued on it never reaches the one the service
builds.
"""

from __future__ import annotations

from collections import deque
from typing import ClassVar

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult


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

    script: ClassVar[deque[AIMessage]] = deque()

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=self.script.popleft())])
