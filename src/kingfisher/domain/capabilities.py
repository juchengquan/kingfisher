"""What a request is allowed to use.

Capabilities are turn-scoped: they travel with a request rather than being
fixed for a workspace or a conversation. Rebuilding an agent costs about 8ms,
so the cost is not construction — it is prompt caching, and only when the set
actually *changes*, since the cache compares bytes and does not care that we
rebuilt. A caller passing the same set every turn keeps its cache hits.

Names, never definitions. A request activates what the workspace already
offers; it cannot invent a tool or write a subagent's prompt. That keeps
definitions reviewable wherever the operator keeps them and means an untrusted
caller can widen nothing.

`"*"` means everything, a list means exactly those, `None` means none. The
default is `"*"`, and that default is deliberate: authorisation is not the
request's job. A request states intent, and a service decides what a given
caller may have, by clamping with `intersect` before the request is run. Baking
fail-closed in here would make callers authorise themselves.

`None` used to mean "no opinion, so everything", with an empty tuple for none.
Two things were wrong with it. A JSON caller cannot tell an absent key from a
null one, and both had to mean "everything" -- the least safe reading of a
missing field. And narrowing needed a three-state rule at every step, because
"no opinion" is neither a set nor the absence of one. Spelled `"*"` and `None`,
the two ends are an ordinary lattice and narrowing is set intersection.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

#: Everything the workspace offers, whatever that turns out to be. The top of
#: the lattice: narrowing by it changes nothing, and it does not go stale,
#: because it names nothing that could.
ALL: Literal["*"] = "*"

#: `"*"` is everything, a tuple is exactly those names, `None` is nothing.
#:
#: The declared contract is `ALL` or a tuple, and consumers can rely on that.
#: `__post_init__` is nonetheless lenient about what it will normalise, because
#: a service deserialising JSON hands us lists; that leniency is a backstop, not
#: the contract, so a caller holding a list should convert at its own edge.
Selection = Literal["*"] | tuple[str, ...] | None


class CapabilityError(ValueError):
    """A request named a tool, skill, subagent or middleware it may not have.

    Here rather than in `infrastructure`, where it lived, because it says
    nothing about the harness: a name asked for and not offered is kingfisher's
    own vocabulary, and the rules that raise it are in this module.
    """


def _normalise(value: object) -> Selection:
    if value is None:
        return None
    if value == ALL:
        return ALL
    if isinstance(value, str):
        # A bare string is a caller meaning one name, or a typo for `ALL`.
        # Either way, silently iterating its characters is the worst answer.
        msg = f"a selection is {ALL!r}, a list of names, or None -- got {value!r}"
        raise CapabilityError(msg)
    if not isinstance(value, Iterable):
        msg = f"a selection is {ALL!r}, a list of names, or None -- got {value!r}"
        raise CapabilityError(msg)
    return tuple(dict.fromkeys(str(v) for v in value))  # de-duped, order kept


@dataclass(frozen=True)
class Capabilities:
    """What one request may use, out of what the deployment has wired.

        Capabilities()                                  # everything configured
        Capabilities(tools=None)                        # no tools at all
        Capabilities(tools=("read_file", "glob"))       # read-only
        Capabilities(memory=False)                      # do not read the memory file

    Five of these name things and one is a switch, because memory has no names
    to choose between -- it is one file, either mounted or not. The switch keeps
    its own three states: `None` there is still "no opinion", because a bool has
    no `"*"` to be the top of.

    This is the *narrowing* axis. The other one is `Config.memory_enabled` and
    `Config.skills_enabled`: what this deployment wired at all. Those shape the
    system prompt and so must stay stable across requests; these do not, and
    vary per turn. Narrowing can only ever subtract from wiring -- asking for
    memory a deployment never wired does not conjure it.
    """

    #: The tools deepagents brings -- read_file, execute, task and the rest.
    #: Separate from `tools` because the two sets change for different reasons:
    #: this one moves when the dependency is upgraded, which is deliberate and
    #: visible, while a workspace gains a tool whenever someone adds a file.
    #: Granting one used to cost the other, so naming a workspace tool took
    #: `read_file` away with it.
    builtin_tools: Selection = ALL
    #: The tools this workspace defines, loaded from `tools/`.
    tools: Selection = ALL
    skills: Selection = ALL
    #: `None`, alone among these, and it is not an inconsistency in the model --
    #: it is the one axis whose default differs from the others, for a reason
    #: the model can now state. Wiring a subagent compiles a whole graph, at a
    #: measured 4.3ms each, so a workspace with eight of them would charge every
    #: unrestricted turn ~34ms for delegates it may never call. `"*"` here means
    #: every subagent defined, and a request that wants them says so.
    subagents: Selection = None
    #: Middleware a definition may name, out of what the deployment registered.
    #: Unlike the three above it is never widened by `including` -- see there.
    middleware: Selection = ALL
    #: Endpoints a definition may name. Granted like `middleware` and for a
    #: stronger reason: this one decides which credentials are used and which
    #: endpoint receives the run's prompts and files.
    providers: Selection = ALL
    #: Models a request may put a delegate on, overriding what its file says.
    #:
    #: `None` by default, and that default is the point. Every other axis here
    #: only ever takes something away -- a request picks from what the
    #: workspace offers and cannot invent anything, which is what makes an
    #: untrusted caller safe to accept. Naming a model is the one thing that
    #: *chooses* rather than narrows, and models differ in price by more than
    #: an order of magnitude. So it is off until a deployment grants it, and
    #: granted per name rather than as a switch: "on" with no list means any
    #: caller may name the most expensive model you have credentials for.
    models: Selection = None
    memory: bool | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "builtin_tools",
            "tools",
            "skills",
            "subagents",
            "middleware",
            "providers",
            "models",
        ):
            object.__setattr__(self, field_name, _normalise(getattr(self, field_name)))

    @property
    def is_unrestricted(self) -> bool:
        """True when nothing is narrowed, so the agent can be built as configured.

        Compared against the defaults rather than field by field, because the
        defaults are no longer one value: `subagents` starts at `None` and the
        rest at `ALL`. Written out, this would be a list to keep in step with
        the fields above -- and a new field added without a matching line here
        would silently count as "narrowing nothing".
        """
        return self == Capabilities()

    def including(
        self, *, skills: tuple[str, ...] = (), subagents: tuple[str, ...] = ()
    ) -> Capabilities:
        """Widen by definitions the request brought with it.

        The only thing allowed to widen, and it does not really: an uploaded
        definition is the caller's own text, so permitting it grants nothing
        they did not already hold. A grant list is written before an upload
        exists and its name is unknowable then, so clamping against it would
        strip every upload rather than authorise it.

        What bounds an uploaded skill is the tool selection, which this does
        not touch. A skill's `allowed-tools` is prompt text to deepagents and
        binds nothing.

        **`middleware` and `providers` are deliberately absent**, and that
        absence is the rule.
        A skill or subagent an upload brings is the caller's own text; a
        middleware *name* is a selector for code the deployment wrote. Widening
        it here would let anyone who can upload a definition activate anything
        the deployment registered, which is the escalation the rest of this
        method exists to avoid. `providers` is the same argument with more at
        stake: it chooses which endpoint receives the run's prompts and files,
        and whose credentials pay for them.

        `ALL` stays `ALL`: it already includes these. `None` stays `None` -- a
        request that asked for no skills at all did not ask for its own either,
        and an upload is not a way to reopen a door the caller shut.
        """
        return Capabilities(
            builtin_tools=self.builtin_tools,
            tools=self.tools,
            skills=_widened(self.skills, skills),
            subagents=_widened(self.subagents, subagents),
            middleware=self.middleware,  # never widened; see above
            providers=self.providers,  # nor this: it chooses where prompts go
            models=self.models,  # nor this: it chooses what the run costs
            memory=self.memory,
        )

    def intersect(self, other: Capabilities) -> Capabilities:
        """Narrow these capabilities by another set. Never widens.

        This is what a service calls to clamp an incoming request against what
        the caller is permitted:

            granted.intersect(request.capabilities)

        `ALL` on either side is the identity, so the other side wins; where both
        name things, only the overlap survives; `None` on either side wins
        outright. Because it can only remove, a caller cannot escalate by asking
        for more.
        """
        return Capabilities(
            builtin_tools=narrowed(other.builtin_tools, by=self.builtin_tools),
            tools=narrowed(other.tools, by=self.tools),
            skills=narrowed(other.skills, by=self.skills),
            subagents=narrowed(other.subagents, by=self.subagents),
            middleware=narrowed(other.middleware, by=self.middleware),
            providers=narrowed(other.providers, by=self.providers),
            models=narrowed(other.models, by=self.models),
            memory=_narrow_switch(self.memory, other.memory),
        )

    def unknown(
        self, *, tools: Iterable[str], skills: Iterable[str], subagents: Iterable[str]
    ) -> tuple[str, ...]:
        """Names asked for that the workspace does not offer.

        Reported so an unresolvable request fails loudly instead of running
        with quietly less than the caller asked for — the difference between a
        clear rejection and an agent that silently could not do the job.
        """
        missing: list[str] = []
        for requested, available, label in (
            (self.builtin_tools, tools, "tool"),
            (self.tools, tools, "tool"),
            (self.skills, skills, "skill"),
            (self.subagents, subagents, "subagent"),
        ):
            # Neither end names anything, so neither can name something wrong.
            if requested is None or requested == ALL:
                continue
            known = set(available)
            missing.extend(f"{label}:{name}" for name in requested if name not in known)
        return tuple(missing)


def _narrow_switch(left: bool | None, right: bool | None) -> bool | None:
    """A refusal from either side wins; otherwise the side with an opinion does.

    `False` is the only value that can subtract, which is what makes this
    narrowing rather than negotiation: a caller cannot turn memory on by asking
    when the other side said no.
    """
    if left is False or right is False:
        return False
    if left is True or right is True:
        return True
    return None


def belongs_in(names: tuple[str, ...], *, field: str) -> str:
    """"that is a builtin tool -- name it in builtin_tools", agreeing in number.

    Both places that say this built the sentence for one name and then printed
    it for however many there were:

        names read_file, ls, glob, grep, execute in tools,
        but it is a builtin tool -- name it in builtin_tools

    Which is the message *every* definition written before the two tool lists
    existed will hit, so it is the one worth reading like a sentence. `field`
    is the plural key a name belongs under -- `builtin_tools` -- and the kind
    is its singular, which is why the two never disagree.
    """
    # `split`/`join` rather than `.replace`, which `test_domain_touches_nothing`
    # forbids here: `Path.replace` renames a file, and the check reads names
    # rather than types. A blunt guard is the point of that test, so this bends
    # to it rather than the other way round.
    kind = " ".join(field[:-1].split("_"))
    if len(names) == 1:
        return f"that is a {kind} -- name it in {field}"
    return f"those are {kind}s -- name them in {field}"


def narrowed(selection: Selection, *, by: Selection) -> Selection:
    """`selection`, keeping only what `by` also allows. Never widens.

    `None` on either side means "no opinion", so the other side wins; where both
    name things only the overlap survives, in `selection`'s order.

    Public, and `by` is keyword-only, because this rule is applied at two levels
    and used to be written twice to do it. `Capabilities.intersect` clamps a
    request against what the deployment granted; `delegation.as_subagent` clamps
    a definition's declared tools against what its caller was granted. The
    second was a private copy in `infrastructure`, identical to this across
    every input pair, with the arguments in the other order and nothing
    comparing them -- one convention away from a delegate quietly getting more
    than the request that summoned it.
    """
    if selection is None or by is None:
        return None  # nothing, narrowed by anything at all, is still nothing
    if selection == ALL:
        return by
    if by == ALL:
        return selection
    allowed = set(by)
    return tuple(name for name in selection if name in allowed)


def _widened(selection: Selection, extra: tuple[str, ...]) -> Selection:
    """`selection` plus names the caller brought with it -- see `including`.

    The two ends are left alone for opposite reasons. `ALL` already has them.
    `None` asked for none, and an upload is not a way back through a door the
    request itself closed.
    """
    if selection is None or selection == ALL:
        return selection
    return (*selection, *extra)


def withheld(granted: Selection, *, offered: Iterable[str]) -> tuple[str, ...]:
    """Names the workspace offers that this grant leaves out.

    The mirror of `Capabilities.unknown`, which reports names asked for that do
    not exist. This reports the ones that exist and were not asked for, and it
    is reported for the same reason: a grant is a whitelist, so it can only ever
    mean *less* than the workspace holds, and a caller cannot see how much less.

    That gap widens on its own. A grant written as "everything except the
    shell" is stored as the other names, so a tool added afterwards is outside
    it -- refused, with nothing said, months after the list was written. The
    alternative shape fails the other way: a deny-list would let tomorrow's new
    tool through by default, which is the worse of the two when the new tool is
    another `execute`. So the whitelist stays and the silence goes.

    `ALL` withholds nothing, and does not go stale, because it names nothing
    that could. `None` withholds everything, which is the whole of what it says.
    """
    if granted == ALL:
        return ()
    if granted is None:
        return tuple(sorted(offered))
    permitted = set(granted)
    return tuple(sorted(name for name in offered if name not in permitted))


def all_but(excluded: tuple[str, ...], *, offered: Iterable[str]) -> tuple[str, ...]:
    """The grant that "everything except these" means, against what is offered now.

    Subtraction is what a caller usually means -- "not the shell", rather than
    the other eleven names -- and it is the one thing a whitelist cannot say.

    It resolves here rather than being stored, and that is the point. A stored
    subtraction *is* a deny-list: it lets tomorrow's new tool through by
    default, which is the wrong way to fail when the new tool is another
    `execute`. Resolved at the moment it is written, what gets stored and
    enforced is still an ordinary whitelist, and `withheld` still reports what
    it left out.

    A name that excludes nothing is refused. `--without-tools exec` is a typo
    that would otherwise grant everything quietly, which is the failure this
    area keeps being about -- the mirror of `Capabilities.unknown`, for the
    other direction.

    The set difference is `withheld`'s, asked the other way round: that one
    turns a grant into what it leaves out, this one turns what to leave out
    into a grant. One rule, two directions.
    """
    known = set(offered)
    unknown = tuple(sorted(name for name in excluded if name not in known))
    if unknown:
        msg = (
            f"cannot exclude unknown name(s): {', '.join(unknown)}; "
            f"this workspace offers {tuple(sorted(known))}"
        )
        raise CapabilityError(msg)
    return withheld(excluded, offered=offered)


def refuse_ungranted_models(wanted: Iterable[str], *, granted: Selection, subject: str) -> None:
    """Refuse a model a request may not put a delegate on.

    Raised rather than dropped, which is the opposite of how a narrower caller
    is treated elsewhere -- and deliberately. Dropping a skill leaves a
    delegate knowing less; silently ignoring "run it on the cheap model" leaves
    it running on the expensive one, which is the answer nobody asked for and
    the bill nobody expected. The same reasoning `approved_middleware` gives.
    """
    if granted == ALL:
        return
    permitted = set(granted or ())
    if refused := tuple(name for name in wanted if name not in permitted):
        msg = (
            f"{subject} names model(s) this request may not use: "
            f"{', '.join(sorted(refused))}; permitted {granted}"
        )
        raise CapabilityError(msg)


def approved_middleware(
    declared: Selection,
    *,
    registered: Iterable[str],
    granted: Selection,
    subject: str,
) -> tuple[str, ...]:
    """Which of the middleware a definition names it may actually have.

    Two refusals, and both raise -- neither is the "caller was narrower" case
    that quietly drops a skill. A name nothing registered is a mistake in the
    definition. A name the deployment registered but did not *grant* is an
    escalation attempt or a misconfiguration, and running with silently less
    middleware than the definition specified could mean running without the
    rate limit or the audit hook it was written to have.

    Checked identically for a catalogue definition and an uploaded one.
    `Capabilities.including` widens skills and subagents for an upload because
    those are the caller's own text; a middleware name selects code the
    deployment wrote, so an upload gets no such exemption.

    A rule, so it lives here. What it decides is *names*, which is all this
    layer knows about middleware -- `Capabilities.middleware` and
    `SubagentSpec.middleware` are both name lists. Turning an approved name
    into an object needs the registry, and that is the caller's half.
    """
    if declared is None:
        return ()
    if declared == ALL:
        # Everything registered *and* granted -- a definition that asks for the
        # deployment's middleware still cannot reach past what the request holds.
        return tuple(n for n in registered if granted == ALL or n in (granted or ()))
    if not declared:
        return ()

    known = set(registered)
    unknown = tuple(name for name in declared if name not in known)
    if unknown:
        msg = (
            f"{subject} names unregistered middleware: {', '.join(unknown)}; "
            f"this deployment registered {tuple(registered)}"
        )
        raise CapabilityError(msg)

    if granted != ALL:
        # `None` permits nothing, so everything declared is ungranted.
        permitted = set(granted or ())
        ungranted = tuple(name for name in declared if name not in permitted)
        if ungranted:
            msg = (
                f"{subject} names middleware this request may not use: "
                f"{', '.join(ungranted)}; permitted {granted}"
            )
            raise CapabilityError(msg)

    return tuple(declared)


#: Permits everything, which is not the same as the default a request gets.
#:
#: One type serves two jobs -- what a request *asks for*, and what a deployment
#: *permits* -- and `subagents` is where they part. A request that says nothing
#: should wire no delegates, because each one compiles a graph it may never use.
#: A deployment that says nothing should permit all of them, or the first
#: request to name one is clamped to nothing by a grant nobody wrote.
#:
#: So `Capabilities()` is the request default and this is the grant default, and
#: the difference is one field. It was found by a test: a request naming a
#: subagent was silently narrowed away by `Capabilities().intersect(...)`.
UNRESTRICTED = Capabilities(subagents=ALL)
