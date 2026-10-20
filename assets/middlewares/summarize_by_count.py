"""A summarizer that fires at a message count, and the two things it shows a middleware author.

The first is which of its own settings a class opens to a definition. The second is
the half a yaml file cannot reach: a summarizer needs a *model*, a built client with an
endpoint, a key and a timeout behind it, and no spelling of a yaml file produces one.
`docs/guides/middleware.md` has the rest -- registering, what refuses, and the setting a
class keeps shut.

## Which of its own settings a class opens

`messages` is open: `yaml_settable` lists it, so a definition may write
`settings: {messages: 30}`. The test is not whether a key looks harmless but whether
*more* or *different* is a failure mode, and for this key no value is one.
deepagents' own trigger, which summarizes by size before the conversation outgrows the
model, stays in the list whatever count is written, so a count can only add an earlier
reason to summarize. A definition choosing a lower one chooses more summaries -- more
summarizer calls, more of its history read back from a file -- and it chooses them for
its own agent and nobody else's.

## Why a closure is not the answer here

`guides/middleware.md` has the pattern for an http client, and it is still the right
pattern for an http client: make the object once in the program that constructs
`Kingfisher`, close over it in the class, register the class. That works because the
client is the same object for every agent, every request and every turn.

A model is not. Which one an agent runs is decided per build -- the agent file may pin
one, a subagent may pin another, and the request may override either -- so a class
closing over a model closes over the deployment's *default* and runs that whatever the
agent in front of it was pinned to. An agent on the cheap model would be summarized by
the expensive one, silently, and the only symptom is the bill.

And this file could not close over the model either. A `middlewares/` module is
imported once, when `Kingfisher` reads its catalogue, which is before any agent is
built -- so there is no model yet for anything made at module level here to hold.

## `wants`

The third class attribute, beside `defaults` and `yaml_settable`:

    wants = frozenset({"model", "backend"})

Each name is filled in where the agent is assembled, which is the only place that
knows the answer. `model` is the model *this graph* runs -- the agent's own, or a
subagent's own, or whatever it inherited from the one that summoned it. `backend` is
the filesystem the agent sees, rooted at this session. A build also offers
`definition`, the agent or subagent spec an instance was built for, which this class
has no use for.

A want the build does not provide is refused when the agent is built, naming the class
and listing what it does provide. A want is never silently skipped: a hook that appears
to be installed and is not running is the failure worth guarding hardest against.

**A wanted model is an instance, never a string.** Handed `model: "gpt-5"`, a
summarizer passes it to `init_chat_model`, which infers a provider from the name and
reads credentials from the environment -- around the catalogue, around the endpoint's
`base_url`, around every param the profile carries. That is the same trap
`core.agent.kinds.subagents` documents for a subagent's model. Handing it a built
instance is the fix, not a convenience.

## Wiring it

    from assets.middlewares.summarize_by_count import SummarizeByCount

    kingfisher = Kingfisher(
        cfg,
        middlewares={
            "summarize-by-count": SummarizeByCount,
        },
    )

Or not at all: `MIDDLEWARES` at the foot means `kingfisher seed` copies this file into
a workspace like any other definition, and a deployment that wires nothing in Python
still gets it. That is the case `wants` exists for -- a workspace file has no program
to close over.
"""

from __future__ import annotations

from typing import Any, ClassVar

from deepagents.middleware.summarization import (
    SummarizationMiddleware,
    compute_summarization_defaults,
)


class SummarizeByCount(SummarizationMiddleware):
    """deepagents' summarizer, replacing the one deepagents adds, and firing at a message count too.

    ## deepagents', not langchain's

    deepagents puts a summarizer on every agent and every subagent, and it records
    what it summarized as an index into the conversation rather than rewriting the
    conversation. langchain's rewrites it. Beside deepagents' own, a rewriting
    summarizer leaves that index pointing at the wrong messages or past the end,
    and the model is shown a stale summary and none of what it just did. So a
    second summarizer can only be this one, taking the first one's place.

    Everything else is deepagents' too: the thresholds it picks from the model's
    profile, what it keeps, the history it saves where the summary tells the agent
    to look, the retry when a call overflows anyway. What this adds is one trigger
    -- `messages` of them, whatever their size.

    ## The name

    `name` is read twice: on the class by kingfisher, as what a definition writes,
    and on the instance by deepagents, which merges middleware by name. The
    instance answers to deepagents' own summarizer, and that is what puts this in
    its place rather than beside it, in every graph it is built into. kingfisher
    says so when the agent is built; here that notice is the design working.
    """

    #: What a definition writes to select this class, and what the wiring block
    #: above registers it as.
    name = "summarize-by-count"

    defaults: ClassVar[dict[str, Any]] = {"messages": 60}

    #: Open because no count is a failure mode: deepagents' size trigger stays beside it.
    yaml_settable: ClassVar[frozenset[str]] = frozenset({"messages"})

    #: Filled in where the agent is assembled: the model this graph runs, and the
    #: session filesystem the summarized history is saved to.
    wants: ClassVar[frozenset[str]] = frozenset({"model", "backend"})

    def __init__(self, model: Any, backend: Any, messages: int) -> None:
        # A definition writes this. Not a `TypeError`, here or from langchain's own check:
        # the build reports that as `defaults` missing an argument -- the wrong culprit.
        if isinstance(messages, bool) or not isinstance(messages, int):
            msg = f"`messages` is a count of messages, not {messages!r}"
            raise ValueError(msg)  # noqa: TRY004
        if messages < 1:
            msg = (
                f"a count of {messages} messages would summarize before every call; "
                f"to summarize by size alone, omit the middleware instead"
            )
            raise ValueError(msg)
        # What `create_summarization_middleware` builds for every agent, with the
        # message count beside its own trigger. A list fires when any clause is met.
        upstream = compute_summarization_defaults(model)
        super().__init__(
            model,
            backend=backend,
            trigger=[("messages", messages), upstream["trigger"]],
            keep=upstream["keep"],
            trim_tokens_to_summarize=None,
            truncate_args_settings=upstream["truncate_args_settings"],
        )
        self.name = "SummarizationMiddleware"


#: What this file contributes, declared rather than inferred -- the rule `TOOLS`
#: makes one directory over.
MIDDLEWARES = [SummarizeByCount]
