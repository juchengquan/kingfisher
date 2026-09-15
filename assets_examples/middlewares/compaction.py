"""A middleware that needs the model the agent is running, and the seam that gives it one.

`call_cap.py` is the first of these three and says what middleware is, why `seed`
leaves behind a definition naming one, and why a definition may only ever *name*
code the deployment wrote. `tool_note.py` is the second and says which of a
class's own settings it may open to a definition. Neither is repeated here.

What this adds is the half a yaml file cannot reach. `defaults` and a
definition's `settings:` both carry scalars -- a number, a sentence, a flag --
and a compaction middleware needs a *model*: a built client with an endpoint, a
key and a timeout behind it. There is no spelling of a yaml file that produces
one.

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

And this file could not close over anything in the first place. A `middlewares/`
module is executed every time the repository is read, and a repository is built
per request, so an object made at module level here is a new object on every
turn -- which is the whole argument for the registry keeping factories.

## `wants`

The third class attribute, beside `defaults` and `yaml_settable`:

    wants = frozenset({"model", "backend", "definition"})

Each name is filled in where the agent is assembled, which is the only place
that knows the answer. `model` is the model *this graph* runs -- the agent's
own, or a delegate's own, or whatever it inherited from the one that summoned
it. `backend` is the filesystem the agent sees, rooted at this session.
`definition` is the agent or subagent spec this instance was built for.

A want the build does not provide is refused when the agent is built, naming the
class and listing what it does provide. A want is never silently skipped, for
the reason this page gives about `wrap_tool_call` and `awrap_tool_call`: a hook
that appears to be installed and is not running is the failure worth guarding
hardest against.

## A name in the file, an object in the constructor

`model` is above *and* in `yaml_settable`, which looks like a contradiction and
is the point. A wanted key is a name wherever it is written and an object by the
time `__init__` sees it:

    middlewares:
      - name: compact
        settings: {model: cheap}

resolves `cheap` through the same catalogue a subagent's `model:` field goes
through -- `models.yaml`, the profile's `max_tokens` and `timeout_s`, the
endpoint's `base_url` and adapter -- and refuses an endpoint this request may
not reach, before a single prompt is sent anywhere. Write nothing and the
fallback applies, which is the model the agent is already running.

`backend` and `definition` are not in `yaml_settable` and could not be. There is
no name a file could write for "the filesystem", so a value written for one of
them is refused rather than interpreted.

**A wanted key is why the model must not be a string.** Handed `model: "gpt-5"`,
`SummarizationMiddleware` passes it to `init_chat_model`, which infers a provider
from the name and reads credentials from the environment -- around the
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

from langchain.agents.middleware import SummarizationMiddleware


class Compact(SummarizationMiddleware):
    """Summarise the older half of a conversation, and keep what it threw away.

    ## The two things this class declares that langchain's does not

    `wants` names what the harness fills in. `defaults` names what the deployment
    chose. Between them every argument `__init__` requires is covered, which is
    the rule a registered class is built by -- an argument in neither is a
    deployment mistake and is refused as such, naming this class.

    ## `trigger` is in `defaults` because its absence is silent

    Left unset, `SummarizationMiddleware` normalises `trigger=None` into an empty
    list of clauses and never fires. It installs, it reports, it wraps every
    model call, and it summarises nothing -- for the life of the deployment, with
    nothing said. A threshold belongs here anyway, since it is a ceiling on
    context and a ceiling is the deployment's to set, but the reason it is not
    merely *preferable* to write one is that omitting it produces a middleware
    that looks present and is inert.

    ## The name

    `AgentMiddleware.name` answers the class's own name, and deepagents merges
    middleware by name. Without the line below, this would arrive as
    `SummarizationMiddleware` and *replace* langchain's rather than run as
    itself. The registry key is separate and is not involved.
    """

    #: What a definition writes to select this class, and what the wiring block
    #: above registers it as.
    name = "compact"

    #: Filled in where the agent is assembled. `model` is the one this graph
    #: runs; `backend` is the session filesystem; `definition` is the agent or
    #: subagent spec this instance belongs to, which is how the note below knows
    #: whose conversation it is recording.
    wants: ClassVar[frozenset[str]] = frozenset({"model", "backend", "definition"})

    #: The deployment's half. `trigger` is here because an unset one never fires
    #: -- see the class docstring -- and `keep` because how much context survives
    #: a compaction is a judgement about cost, not about the agent's job.
    defaults: ClassVar[dict[str, Any]] = {
        "trigger": ("messages", 60),
        "keep": ("messages", 20),
    }

    #: `model` and nothing else. A definition choosing which model summarises its
    #: own conversation is choosing what that costs, and the endpoint ceiling
    #: already refuses the escalation that would matter -- a name resolving
    #: somewhere this request may not reach. `trigger` and `keep` are shut for
    #: `CallCap`'s reason: a bound the bounded thing can raise is not a bound.
    yaml_settable: ClassVar[frozenset[str]] = frozenset({"model"})

    def __init__(
        self, model: Any, backend: Any, definition: Any, trigger: Any, keep: Any
    ) -> None:
        super().__init__(model, trigger=trigger, keep=keep)
        self._backend = backend
        self._definition = definition
        self._compactions = 0

    def _note(self, before: list[Any], summarised: dict[str, Any]) -> tuple[str, str]:
        """Where this compaction's lost messages go, and what is written there.

        The messages that were removed are not in what `before_model` returns --
        it carries the summary and what survived. So they are the difference
        between the two, matched by id, which is public shape rather than a
        second copy of the partitioning upstream already did.
        """
        kept = {getattr(message, "id", None) for message in summarised["messages"]}
        gone = [m for m in before if getattr(m, "id", None) not in kept]
        self._compactions += 1
        body = "\n\n".join(
            f"### {type(message).__name__}\n\n{message.content}" for message in gone
        )
        return (
            f"/derived/compaction/{self._definition.name}-{self._compactions}.md",
            f"# Compacted out of {self._definition.name}\n\n{body}\n",
        )

    def _refuse_a_lost_note(self, result: Any, path: str) -> None:
        """Raise when the note could not be written, rather than carry on without it.

        A backend reports a failed write by returning one, not by raising, so
        ignoring the result is the default behaviour and not a decision anybody
        makes. It is the wrong one here: this middleware exists so that what
        compaction discards is still readable afterwards, and a run that
        continues believing it has that record is worse off than one that stops.
        """
        if getattr(result, "error", None):
            msg = f"compaction could not write {path}: {result.error}"
            raise RuntimeError(msg)

    def before_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        before = list(state["messages"])
        summarised = super().before_model(state, runtime)
        if summarised is None:
            return None
        path, body = self._note(before, summarised)
        self._refuse_a_lost_note(self._backend.write(path, body), path)
        return summarised

    async def abefore_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        # Both paths, because neither delegates to the other. `stream` and
        # `astream` are two loops over one turn, and a record written on only one
        # of them is absent for every caller who reached for the other -- the
        # failure `guides/middleware.md` says to guard hardest against, on the
        # one hook whose whole purpose is leaving evidence behind.
        before = list(state["messages"])
        summarised = await super().abefore_model(state, runtime)
        if summarised is None:
            return None
        path, body = self._note(before, summarised)
        self._refuse_a_lost_note(await self._backend.awrite(path, body), path)
        return summarised


#: What this file contributes, declared rather than inferred -- the rule `TOOLS`
#: makes one directory over. One class, where the cap needed two: the variation
#: worth offering here is which model summarises, and that is a name a definition
#: writes rather than a second class the deployment registers.
MIDDLEWARES = [Compact]
