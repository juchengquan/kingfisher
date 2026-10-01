"""A middleware that needs the model the agent is running, and the seam that gives it one.

`call_cap.py` is the first of these three and says what middleware is, why `seed`
leaves behind a definition naming one, and why a definition may only ever *name*
code the deployment wrote. `tool_note.py` is the second and says which of a
class's own settings it may open to a definition. Neither is repeated here.

What this adds is the half a yaml file cannot reach. `defaults` and a
definition's `settings:` both carry scalars -- a number, a sentence, a flag --
and a summarizer needs a *model*: a built client with an endpoint, a key and a
timeout behind it. There is no spelling of a yaml file that produces one.

## Why a closure is not the answer here

`guides/middleware.md` has the pattern for an http client, and it is still the
right pattern for an http client: make the object once in the program that
constructs `Kingfisher`, close over it in the class, register the class. That
works because the client is the same object for every agent, every request and
every turn.

A model is not. Which one an agent runs is decided per build -- the agent file
may pin one, a delegate may pin another, and the request may override either --
so a class closing over a model closes over the deployment's *default* and runs
that whatever the agent in front of it was pinned to. An agent on the cheap
model would be compacted by the expensive one, silently, and the only symptom is
the bill.

And this file could not close over the model either. A `middlewares/` module is
imported once, when `Kingfisher` reads its catalogue, which is before any agent is
built -- so there is no model yet for anything made at module level here to hold.

## `wants`

The third class attribute, beside `defaults` and `yaml_settable`:

    wants = frozenset({"model", "backend"})

Each name is filled in where the agent is assembled, which is the only place
that knows the answer. `model` is the model *this graph* runs -- the agent's
own, or a delegate's own, or whatever it inherited from the one that summoned
it. `backend` is the filesystem the agent sees, rooted at this session. A build
also offers `definition`, the agent or subagent spec an instance was built for,
which this class has no use for.

A want the build does not provide is refused when the agent is built, naming the
class and listing what it does provide. A want is never silently skipped, for
the reason this page gives about `wrap_tool_call` and `awrap_tool_call`: a hook
that appears to be installed and is not running is the failure worth guarding
hardest against.

**A wanted model is an instance, never a string.** Handed `model: "gpt-5"`, a
summarizer passes it to `init_chat_model`, which infers a provider from the name
and reads credentials from the environment -- around the
catalogue, around the endpoint's `base_url`, around every param the profile
carries. That is the same trap `infrastructure.harness.subagents` documents for a
delegate's model. Handing it a built instance is the fix, not a convenience.

## Wiring it

    from assets_examples.middlewares.call_cap import CallCap, CallCapGenerous
    from assets_examples.middlewares.compaction import Compact
    from assets_examples.middlewares.tool_note import ToolNote

    kingfisher = Kingfisher(
        cfg,
        middlewares={
            "call-cap-strict":   CallCap,
            "call-cap-generous": CallCapGenerous,
            "tool-note":         ToolNote,
            "compact":           Compact,
        },
    )

Or not at all: `MIDDLEWARES` at the foot means `kingfisher seed` copies this file
into a workspace like any other definition, and a deployment that wires nothing
in Python still gets it. That is the case `wants` exists for -- a workspace file
has no program to close over.
"""

from __future__ import annotations

from typing import Any, ClassVar

from deepagents.middleware.summarization import (
    SummarizationMiddleware,
    compute_summarization_defaults,
)


class Compact(SummarizationMiddleware):
    """deepagents' summarizer, replacing the one deepagents adds, and firing at sixty messages too.

    ## deepagents', not langchain's

    deepagents puts a summarizer on every agent and every delegate, and it records
    what it summarized as an index into the conversation rather than rewriting the
    conversation. langchain's rewrites it. Beside deepagents' own, a rewriting
    summarizer leaves that index pointing at the wrong messages or past the end,
    and the model is shown a stale summary and none of what it just did. So a
    second summarizer can only be this one, taking the first one's place.

    Everything else is deepagents' too: the thresholds it picks from the model's
    profile, what it keeps, the history it saves where the summary tells the agent
    to look, the retry when a call overflows anyway. What this adds is one trigger
    -- sixty messages, whatever their size.

    ## The name

    `name` is read twice: on the class by kingfisher, as what a definition writes,
    and on the instance by deepagents, which merges middleware by name. The
    instance answers to deepagents' own summarizer, and that is what puts this in
    its place rather than beside it, in every graph it is built into. kingfisher
    says so when the agent is built; here that notice is the design working.
    """

    #: What a definition writes to select this class, and what the wiring block
    #: above registers it as.
    name = "compact"

    #: Filled in where the agent is assembled: the model this graph runs, and the
    #: session filesystem the summarized history is saved to.
    wants: ClassVar[frozenset[str]] = frozenset({"model", "backend"})

    def __init__(self, model: Any, backend: Any) -> None:
        # What `create_summarization_middleware` builds for every agent, with the
        # message count beside its own trigger. A list fires when any clause is met.
        upstream = compute_summarization_defaults(model)
        super().__init__(
            model,
            backend=backend,
            trigger=[("messages", 60), upstream["trigger"]],
            keep=upstream["keep"],
            trim_tokens_to_summarize=None,
            truncate_args_settings=upstream["truncate_args_settings"],
        )
        self.name = "SummarizationMiddleware"


#: What this file contributes, declared rather than inferred -- the rule `TOOLS`
#: makes one directory over.
MIDDLEWARES = [Compact]
