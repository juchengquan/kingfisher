"""A reminder appended to every tool result, and the settings half of the axis.

`call_cap.py` is the other half of this pair and was here first. Read it first
too: it says what middleware is, why nothing seeds this folder, and why a
definition may only ever *name* code the deployment wrote. None of that is
repeated here.

What this adds is the case that one could not show. `CallCap` exposes nothing
to a definition -- `yaml_settable` is empty -- because every value it takes is
a ceiling, and a ceiling the ceilinged thing can set is not a ceiling. So the
looser variant is a second class and a second registry entry, and the yaml
chooses between them by name.

That is the right answer for a cap and the wrong one for this. What is worth
reminding an agent about depends on the agent's job, and the definition is the
only thing that knows its job. Registering one class per wording would be a
registry that grows every time somebody writes a sentence.

## Wiring it

    from examples.middleware.call_cap import CallCap, CallCapGenerous
    from examples.middleware.tool_note import ToolNote

    kingfisher = Kingfisher(
        cfg,
        middleware={
            "call-cap-strict":   CallCap,
            "call-cap-generous": CallCapGenerous,
            "tool-note":         ToolNote,
        },
    )

One entry for this one, where the cap needed two. That difference is the whole
point of the pair: `researcher.yaml` and `sweeper.yaml` both name `tool-note`
and each writes its own `text`, so one registered class serves two definitions
that want different things from it. Two names over one class is what you write
when the variants must not be the definition's to choose; one name and a
setting is what you write when they must.

## Which of its own settings a class opens

`yaml_settable` here is `{"text"}` out of a `defaults` holding two. The other
key, `max_length`, is deliberately shut, and having one of each on one class is
why this example is worth reading -- the whitelist is visibly doing work rather
than being a formality.

The test is not "is this key harmless on its own". It is **whether "more" or
"different" is a failure mode**:

- `text` -- a different reminder is just a different reminder. It cannot widen
  a capability, raise a ceiling, or darken an audit trail. There is no wording
  a definition could choose that is an escalation, so there is nothing for the
  deployment to be protecting by holding it.

- `max_length` -- a bound on how much a definition may inject into every tool
  result for the rest of the turn. Left settable, the first definition that
  wanted a longer note would write a longer bound and be within its rights,
  because the format let it. That is `CallCap`'s sentence exactly, and it is
  the same sentence whether the resource being spent is tool calls or context.

A setting that fails that test does not belong in `yaml_settable` however
harmless it looks in a single file, because what it costs is paid by whoever
runs the definition rather than by whoever wrote it. A sampling rate that can
be set to zero, a log destination that can be pointed off the machine and a
retention window that can be shortened are all this shape and none of them
reads as dangerous at a glance.

## What a definition writing nothing gets

`defaults` is the deployment's half and applies whole when a definition writes
no settings at all, so `middleware: [tool-note]` is a working line rather than
a no-op -- it gets the wording below. A definition that writes `text` overrides
that one key and leaves `max_length` alone; the merge is per key, not
all-or-nothing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage


class ToolNote(AgentMiddleware):
    """Append a fixed line to every tool result this agent sees.

    The cheapest way to say something the system prompt already said, at the
    moment it matters. A prompt is read once at the top of a turn and a tool
    result is read immediately before the model decides what to do next, so a
    reminder that has to survive twenty tool calls is better placed here.

    Appended rather than prepended, and to the end of the content rather than
    as a message of its own: a separate message would cost a turn of the graph
    and would read as something the *workspace* said.
    """

    name = "ToolNote"

    #: The deployment's values, applied whole when a definition writes nothing.
    #: `text` is a working default rather than an empty string, so naming this
    #: middleware and saying no more is still worth doing.
    defaults: ClassVar[dict[str, Any]] = {
        "text": "Say where this came from before you rely on it.",
        "max_length": 200,
    }

    #: The one key a definition may write. `max_length` is absent on purpose --
    #: see the module docstring for the test a key has to pass to be here.
    yaml_settable: ClassVar[frozenset[str]] = frozenset({"text"})

    def __init__(self, text: str, max_length: int) -> None:
        if max_length < 1:
            msg = (
                f"a max_length of {max_length} leaves no room for a note; "
                f"omit the middleware instead"
            )
            raise ValueError(msg)
        written = text.strip()
        # Truncated rather than refused, and refusing was the first version.
        # A definition that writes a long note should get a short one, not a
        # build that fails -- the bound exists to stop the context being eaten,
        # and it has done that either way. Visibly truncated, because a note
        # that stops mid-sentence with no sign of why reads as a bug in the
        # middleware rather than as a ceiling doing its job.
        self._text = (
            written
            if len(written) <= max_length
            else written[: max(max_length - 1, 0)] + "…"
        )
        self._max_length = max_length
        super().__init__()

    def _annotate(self, result: Any) -> Any:
        """The result with the note on the end, where there is somewhere to put it.

        Passed through untouched when there is not. A tool may answer with
        something that is not a `ToolMessage` at all -- a `Command` redirecting
        the graph is the case that exists today -- and a middleware that
        assumed otherwise would turn a working tool into an error the first
        time one was used. The same goes for content that arrived as a list of
        blocks rather than a string: appending to it is a guess about a shape
        this example has no need to guess at.
        """
        if not isinstance(result, ToolMessage) or not isinstance(result.content, str):
            return result
        if not self._text:
            return result
        return result.model_copy(update={"content": f"{result.content}\n\n{self._text}"})

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return self._annotate(handler(request))

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        # Both paths, for the reason `CallCap` gives: `stream` and `astream`
        # are two loops over one turn, and a note that appeared on only one of
        # them would depend on which the caller reached for.
        return self._annotate(await handler(request))
