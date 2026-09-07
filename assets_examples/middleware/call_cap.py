"""A ceiling on how many tools one turn may call, and the thing it demonstrates.

`docs/guides/middleware.md` is the page for the half a deployment writes --
registering, what a definition may configure, what a middleware sees and what
refuses. It points here for the arguments rather than repeating them, so this
file and `tool_note.py` are still where the reasoning lives.

This is the only example here that a workspace does **not** load. Every other
file under `assets_examples/` is a definition -- an agent, a skill, a subagent, a tool
-- copied into a workspace by `kingfisher seed` and found there by name.
Middleware is not, and cannot be: `DEFINITION_KINDS` is the fields of
`Definitions`, this is not one of them, and `seed` walks exactly those. Nothing
copies this file anywhere.

That is the design rather than a gap. `Capabilities.including` puts it plainly
-- an upload may widen skills and subagents because "a skill or subagent an
upload brings is the caller's own text; a middleware *name* is a selector for
code the deployment wrote". A middleware read out of the workspace would be
code the agent can edit, wrapped around the agent that edited it.

So the two halves of this example live on opposite sides of that line. The class
below is yours, imported by whatever constructs `Kingfisher`. The name is the
definition's, and naming is all a definition may do.

## Wiring it

    from assets_examples.middleware.call_cap import CallCap, CallCapGenerous

    kingfisher = Kingfisher(
        cfg,
        middleware={
            # The factory is the class itself. `declared_middleware` reads
            # `defaults` for the kwarg base and `yaml_settable` for what an
            # agent file is permitted to override; everything else stays
            # in the deployment.
            "call-cap-strict":   CallCap,
            "call-cap-generous": CallCapGenerous,
        },
    )

and in `agents/researcher.yaml`, or any `subagents/*.yaml`:

    middleware: [call-cap-strict]

Both halves of that are written out: `assets_examples/agents/researcher.yaml` and
`assets_examples/subagents/sweeper.yaml` are an agent and its delegate, naming the
registry entries above -- the only definitions in this repository that name
middleware at all.

They live under their own kinds, and `seed` is what keeps them out of a
workspace that cannot build them: it reads the `middleware:` field, leaves such
a definition behind, and says which names it would have needed. `seed --all`
takes them once the factories are registered. That rule used to be the folder's
-- both files sat beside this one, where nothing copies anything -- which kept a
fresh workspace working at the cost of filing an agent somewhere no agent
repository could read it.

An agent that names nothing gets nothing: `middleware` omits to none, like
`skills` and `subagents` and unlike the two tool axes. A name this deployment
did not register is refused when the agent is built, not discovered mid-run.

## Two names, two classes, and why that is the whole lesson

`call-cap-strict` and `call-cap-generous` are two *classes*, and the wording used
to say "two registry entries over one class", which reads true and is not. A
registry key cannot vary a configuration: the build path calls
`cls(**cls.defaults, **settings)`, and `defaults` lives on the class -- so
registering this one class under two keys gives two objects with the same
ceiling and two labels. Varying it has always meant a second class, which is why
`CallCapGenerous` is a subclass and not a second key.

(Written without a dict literal on purpose. The first draft illustrated it with
one, and `test_the_wiring_block_and_the_registry_the_tests_paste_are_one_fact`
read the example as a fourth registry entry -- the guard doing its job on the
prose that describes it.)

The obvious alternative is one entry and a number in the yaml:

    middleware: [call-cap]
    metadata:
      call-cap: {limit: 20}

Do not reach for that, and the reason is this middleware in particular. A cap a
definition can set is not a cap -- the first thing a definition wanting more
than twenty calls would write is `{limit: 1000000}`, and it would be within its
rights, because the format let it. The same argument holds for the audit hook
and the rate limit that `approved_middleware` names as the cases it exists for.

`yaml_settable` is the same argument at the class level: it is the list of keys
a definition is permitted to override, and `limit` is deliberately *not* on it.
The mechanism for varying the ceiling is `CallCapGenerous` below -- a subclass
that overrides `defaults`. The decision stays where the code is, and a definition
chooses among what the deployment registered and can invent nothing.

A request that withholds `call-cap-generous` leaves `call-cap-strict` in reach,
which is what the narrowing story for `middleware` has always been: a *name*
narrows, and the name is a selector for code the deployment wrote. A number in
a mapping could not express that.

## What it is not

Not a budget. `execution_timeout_s` bounds one command, `recursion_limit` bounds
graph steps and `turn_timeout_s` bounds the wall clock; this bounds tool calls,
which is a different axis from all three and stops nothing that does not call a
tool. A model that spends a turn thinking passes it untouched.

Per turn rather than per session, and by construction rather than by choice: the
agent is built once per turn, so the factory runs once per turn and the count
starts at zero each time. A cap that outlived a turn would need somewhere to
live that a graph does not have.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage


class CallCap(AgentMiddleware):
    """Refuse a tool call once this turn has made `limit` of them.

    Refused as a `ToolMessage` rather than raised, which is what
    `ToolAllowlist` does two files over and for the same reason: the agent gets
    to see that it ran out and write its answer from what it already has, where
    an exception would end the turn and lose the work.

    Counted at `wrap_tool_call`, so it counts what actually ran. Counting the
    calls a model *asked* for would be a different and worse rule -- a refusal
    from another middleware would spend the budget it prevented.

    ## The two class attributes a registered class may declare

    `defaults` and `yaml_settable` are what makes a class registerable as
    itself rather than behind a `lambda`. The build path calls
    `cls(**defaults, **whatever the definition wrote)`, and `yaml_settable`
    decides which keys a definition is allowed to have written.

    `limit` is deliberately absent from `yaml_settable`, and this is the same
    argument the module docstring makes rather than a second one: a cap a
    definition can set is not a cap. The class ships settable-by-nobody, which
    is the right default for a middleware whose entire job is to say no.
    """

    #: What a definition writes to select this class, and what the wiring block
    #: below registers it as. `AgentMiddleware.name` is a property answering the
    #: class's own name, so leaving this out would make the selector `CallCap`;
    #: the name this example has always taught is `call-cap-strict`, and a class
    #: that says so is what makes the two the same fact rather than two.
    name = "call-cap-strict"

    #: What the deployment passes when a definition wrote nothing -- which,
    #: since `yaml_settable` is empty here, is always. Copied before the merge,
    #: so nothing a definition writes can reach this dictionary.
    defaults: ClassVar[dict[str, Any]] = {"limit": 20}

    #: The keys a definition may write under `settings:`. Empty is the point:
    #: every value this class takes is the deployment's to choose, and the way
    #: to offer a second ceiling is `CallCapGenerous` below rather than a number
    #: an agent file can raise.
    yaml_settable: ClassVar[frozenset[str]] = frozenset()

    def __init__(self, limit: int) -> None:
        if limit < 1:
            msg = f"a cap of {limit} would refuse every call; omit the middleware instead"
            raise ValueError(msg)
        self._limit = limit
        self._made = 0
        super().__init__()

    def _refuse(self, request: Any) -> ToolMessage | None:
        """A `ToolMessage` when the budget is gone, `None` while it is not."""
        if self._made < self._limit:
            self._made += 1
            return None
        call = request.tool_call
        return ToolMessage(
            content=(
                f"Error: this turn's limit of {self._limit} tool calls is used up. "
                f"Answer from what you have, and say that you stopped early."
            ),
            tool_call_id=call.get("id", ""),
            name=call.get("name"),
            status="error",
        )

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return self._refuse(request) or handler(request)

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        # Both paths, because neither delegates to the other here. `stream` and
        # `astream` are two loops over one turn and a cap that held on only one
        # of them would depend on which the caller reached for.
        refusal = self._refuse(request)
        return refusal if refusal is not None else await handler(request)


class CallCapGenerous(CallCap):
    """The same cap with a bigger number, as a class rather than a setting.

    The variant is a subclass overriding `defaults`, and a definition chooses
    between the two by name. Not a second registry key over this class: keys are
    labels, and `defaults` is the class's, so two keys would be one ceiling twice.

    A subclass rather than `{"limit": 100}` written in a yaml file, for the
    reason `CallCap.yaml_settable` is empty -- the ceiling is the deployment's
    to set. And a subclass rather than a second `defaults` dictionary passed to
    a shared factory, because the name in a definition has to select *something*
    the deployment wrote, and a class is the thing a registry entry now is.

    The cost is one class per variant, which is the honest price of keeping the
    decision in code. A deployment wanting a third writes a third.

    It names itself, like its parent, or it would inherit `call-cap-strict` and
    two classes would answer to one selector -- which the workspace catalogue
    refuses outright and a code registry would resolve by whichever key you
    happened to write.
    """

    name = "call-cap-generous"

    defaults: ClassVar[dict[str, Any]] = {"limit": 100}


#: What this file contributes, declared rather than inferred -- the rule
#: `TOOLS` makes one directory over, and here for its reason: a class imported
#: to build a variant would otherwise be offered as a second entry nobody meant
#: to expose. Both, because the pair *is* the lesson: two selectors over one behaviour,
# varying in code rather than in a definition's `settings:`.
MIDDLEWARE = [CallCap, CallCapGenerous]
