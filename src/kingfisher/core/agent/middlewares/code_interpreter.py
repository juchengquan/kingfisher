"""The code interpreter, describing `eval` only to a model call that offers it.

Imported only where a deployment switched the interpreter on, so a run that never does
loads no langchain-quickjs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_quickjs import CodeInterpreterMiddleware

from kingfisher.core.definitions.subagents import INTERPRETER_TOOL

from .narrowing import requested_tool_name

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


def _offers_eval(request: Any) -> bool:
    return any(requested_tool_name(one) == INTERPRETER_TOOL for one in request.tools)


class InterpreterMiddleware(CodeInterpreterMiddleware):
    """langchain-quickjs's, with its block left out of a call that does not offer `eval`."""

    # Its own appends the block to every call. A grant without `eval` keeps the tool
    # registered, for `GrantedOnlyMiddleware` to refuse by name, and takes it out of the
    # call before this sees it -- so unchecked, the block, 16k characters, describes a
    # tool the model cannot call.
    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        if _offers_eval(request):
            return super().wrap_model_call(request, handler)
        return handler(request)

    async def awrap_model_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        if _offers_eval(request):
            return await super().awrap_model_call(request, handler)
        return await handler(request)
