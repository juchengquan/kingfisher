"""What a request is allowed to use.

Turn-scoped: they travel with a request rather than being fixed for a workspace
or a conversation. Construction is not the cost -- an agent rebuild is 8ms empty
and 54ms seeded, and `docs/findings.md` records that the smaller figure is the
misleading one. The cost is prompt caching, and only when the set actually
*changes*, since the cache compares bytes and does not care that we rebuilt: a
caller passing the same set every turn keeps its hits.

Names, never definitions. A request activates what the workspace already offers;
it cannot invent a tool or write a subagent's prompt. That keeps definitions
reviewable wherever the operator keeps them, and means an untrusted caller can
widen nothing.

`"*"` means everything, a list means exactly those, `None` means none. The
default is `"*"` deliberately: authorisation is not the request's job. A request
states intent; a service decides what a caller may have by clamping with
`intersect` before the run. Baking fail-closed in here would make callers
authorise themselves.

Spelled this way -- rather than `None` for "no opinion" and an empty tuple for
none -- the two ends are an ordinary lattice and narrowing is set intersection. A
JSON caller also cannot tell an absent key from a null one, and reading both as
"everything" is the least safe reading of a missing field.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

#: Everything the workspace offers, whatever that turns out to be. The top of
#: the lattice: narrowing by it changes nothing, and it does not go stale,
#: because it names nothing that could.
ALL: Literal["*"] = "*"

#: What separates where a thing came from from what it is called. Two colons
#: rather than one because a Windows path can carry a single one, and because
#: pytest already taught everyone that `file::thing` means "that thing, in that
#: file".
SEPARATOR = "::"


def _bare(written: str) -> str:
    """A written name with any source stripped off, for a caller comparing names."""
    return written.rpartition(SEPARATOR)[2].strip()

#: `"*"` is everything, a tuple is exactly those names, `None` is nothing.
#:
#: The declared contract is `ALL` or a tuple, and consumers can rely on that.
#: `__post_init__` is nonetheless lenient about what it will normalise, because
#: a service deserialising JSON hands us lists; that leniency is a backstop, not
#: the contract, so a caller holding a list should convert at its own edge.
Selection = Literal["*"] | tuple[str, ...] | None


class CapabilityError(ValueError):
    """A request named a tool, skill, subagent or middleware it may not have."""


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

    Every field but `memory` names things; `memory` is a switch, because it has
    no names to choose between -- one file, mounted or not. The switch keeps its
    own three states: `None` there is still "no opinion", a bool having no `"*"`
    to be the top of.

    This is the *narrowing* axis. The other is `Config.memory_enabled` and
    `Config.skills_enabled`: what the deployment wired at all. Those shape the
    system prompt and must stay stable across requests; these vary per turn.
    Narrowing only subtracts from wiring -- asking for memory a deployment never
    wired does not conjure it.
    """

    #: The tools deepagents brings -- read_file, execute, task and the rest.
    #: Separate from `tools` because the two sets change for different reasons:
    #: this one moves when the dependency is upgraded, which is deliberate and
    #: visible, while a workspace gains a tool whenever someone adds a file.
    builtin_tools: Selection = ALL
    #: The tools this workspace defines, loaded from `tools/`.
    tools: Selection = ALL
    skills: Selection = ALL
    #: `"*"` means *everything this agent declares*, and an agent declares the
    #: delegates it calls, so the set is small and deliberate rather than whatever
    #: the workspace holds. That matters because wiring a subagent compiles a whole
    #: graph -- 5-6ms each, re-measured 2026-09-03.
    subagents: Selection = ALL
    #: Middleware a definition may name, out of what the deployment registered.
    #: Unlike the three above it is never widened by `including` -- see there.
    middleware: Selection = ALL
    #: Endpoints a definition may reach. Granted like `middleware` and for a
    #: stronger reason: this one decides which credentials are used and which
    #: endpoint receives the run's prompts and files. Checked against where a
    #: named model *resolves to*, since definitions name models rather than
    #: endpoints.
    #:
    #: It overlaps `models` below without being redundant: different subjects,
    #: deliberately opposite defaults. This narrows what *reviewed definitions* may
    #: reach and starts open; that gates what an *untrusted caller* may name and
    #: starts closed. Collapsing them would force one default on both.
    endpoints: Selection = ALL
    #: Models a request may put a delegate on, overriding what its file says.
    #:
    #: `None` by default, and that default is the point. Every other axis only
    #: takes something away -- a request picks from what the workspace offers and
    #: cannot invent anything, which is what makes an untrusted caller safe to
    #: accept. Naming a model *chooses* rather than narrows, and models differ in
    #: price by more than an order of magnitude. So it is off until a deployment
    #: grants it, and granted per name rather than as a switch: "on" with no list
    #: means any caller may name the most expensive model you have credentials
    #: for.
    models: Selection = None
    memory: bool | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "builtin_tools",
            "tools",
            "skills",
            "subagents",
            "middleware",
            "endpoints",
            "models",
        ):
            object.__setattr__(self, field_name, _normalise(getattr(self, field_name)))

    @property
    def is_unrestricted(self) -> bool:
        """True when nothing is narrowed, so the agent can be built as configured.

        Compared against the defaults rather than field by field: written out,
        this would be a list to keep in step with the fields above, and a new field
        added without a matching line would silently count as "narrowing nothing".
        """
        return self == Capabilities()

    def including(
        self, *, skills: tuple[str, ...] = (), subagents: tuple[str, ...] = ()
    ) -> Capabilities:
        """Widen by definitions the request brought with it.

        The only thing allowed to widen, and it barely does: an uploaded
        definition is the caller's own text, so permitting it grants nothing they
        did not already hold. A grant list is written before an upload exists and
        its name is unknowable then, so clamping against it would strip every
        upload rather than authorise it.

        What bounds an uploaded skill is the tool selection, which this does not
        touch -- a skill's `allowed-tools` is prompt text to deepagents and binds
        nothing.

        **`middleware` and `endpoints` are deliberately absent**, and that
        absence is the rule. A skill or subagent an upload brings is the caller's
        own text; a middleware *name* selects code the deployment wrote. Widening
        it would let anyone who can upload a definition activate anything the
        deployment registered -- the escalation the rest of this method exists to
        avoid. `endpoints` is the same argument with more at stake: it chooses
        which endpoint receives the run's prompts and files, and whose
        credentials pay for them.

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
            endpoints=self.endpoints,  # nor this: it chooses where prompts go
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
            endpoints=narrowed(other.endpoints, by=self.endpoints),
            models=narrowed(other.models, by=self.models),
            memory=_narrow_switch(self.memory, other.memory),
        )


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

    `field` is the plural key a name belongs under -- `builtin_tools` -- and the
    kind is its singular, which is why the two never disagree.
    """
    # `split`/`join` rather than `.replace`, which `test_domain_touches_nothing`
    # forbids here: `Path.replace` renames a file, and the check reads names rather
    # than types. A blunt guard is the point of that test, so this bends to it.
    kind = " ".join(field[:-1].split("_"))
    if len(names) == 1:
        return f"that is a {kind} -- name it in {field}"
    return f"those are {kind}s -- name them in {field}"


def narrowed(selection: Selection, *, by: Selection) -> Selection:
    """`selection`, keeping only what `by` also allows. Never widens.

    `None` on either side wins outright -- nothing, narrowed by anything, is still
    nothing. `ALL` is the identity, so the other side wins; where both name things
    only the overlap survives, in `selection`'s order.

    Public, and `by` keyword-only, because the rule applies at two levels:
    `Capabilities.intersect` clamps a request against what the deployment granted,
    and `subagents.harness.as_subagent` clamps a definition's declared tools
    against what its caller was granted. Written twice with the arguments the other
    way round, it is one convention away from a delegate quietly getting more than
    the request that summoned it.
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

    The mirror of `Offering.refuse_unknown`, which reports names asked for that
    do not exist; this reports the ones that exist and were not asked for. Same
    reason: a grant is a whitelist, so it can only mean *less* than the workspace
    holds, and a caller cannot see how much less.

    That gap widens on its own. A grant written as "everything except the shell" is
    stored as the other names, so a tool added afterwards falls outside it --
    refused, silently, months after the list was written. A deny-list fails the
    other way, letting tomorrow's new tool through by default. So the whitelist
    stays and the silence goes.

    `ALL` withholds nothing and cannot go stale; `None` withholds everything.
    """
    if granted == ALL:
        return ()
    if granted is None:
        return tuple(sorted(offered))
    permitted = set(granted)
    return tuple(sorted(name for name in offered if name not in permitted))


def all_but(excluded: tuple[str, ...], *, offered: Iterable[str]) -> tuple[str, ...]:
    """The grant that "everything except these" means, against what is offered now.

    Subtraction is what a caller usually means -- "not the shell" rather than
    the other eleven names -- and the one thing a whitelist cannot say.

    It resolves here rather than being stored, which is the point. A stored
    subtraction *is* a deny-list, letting tomorrow's new tool through by default
    -- the wrong way to fail when that tool is another `execute`. Resolved as it
    is written, what gets stored and enforced is an ordinary whitelist, and
    `withheld` still reports what it left out.

    A name that excludes nothing is refused. `--without-tools exec` is a typo that
    would otherwise grant everything quietly.

    The set difference is `withheld`'s, asked the other way round.
    """
    known = set(offered)
    missing = [name for name in excluded if name not in known]

    # An ambiguous name is not an absent one, and saying so matters more here
    # than on the granting side. `--without-skills lookup` against two of them
    # is a subtraction that refuses, so nothing dangerous happens -- but told it
    # is "unknown", a reader goes looking for a skill they can see in the
    # listing printed underneath. The two mistakes send them to different
    # places: one is a typo, the other is a name that stopped being enough.
    for name in sorted(missing):
        if spellings := tuple(sorted(n for n in known if _bare(n) == name)):
            msg = (
                f"cannot exclude {name!r}: more than one source offers it, so "
                f"subtracting it alone would leave one behind -- "
                f"write {', '.join(spellings)}"
            )
            raise CapabilityError(msg)

    if missing:
        msg = (
            f"cannot exclude unknown name(s): {', '.join(sorted(missing))}; "
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


def refuse_unoffered(
    asked: Iterable[str],
    *,
    offered: Iterable[str],
    kind: str,
    subject: str,
    listing: str | None = None,
) -> None:
    """Refuse a name nothing offers, whoever named it.

    The sibling of `refuse_ungranted_models`.

    Raised rather than dropped: a name that exists and was not activated is a
    caller being narrower than a definition, which is ordinary; a name nothing
    defines is a mistake, and narrowing it away leaves no trace of the typo.

    `listing` is for the caller that can say more than a tuple. Tools know which
    file each one came from, and a bare list of names is what sends a reader
    grepping; skills and subagents have nowhere to point yet, so they pass
    nothing and get the names.
    """
    known = set(offered)
    if unknown := tuple(name for name in asked if name not in known):
        # A name two sources offer is not a name nobody offers, and the same
        # distinction `all_but` makes on the way out matters more on the way in:
        # subtracting an ambiguous name leaves one behind, but *granting* one
        # would hand over whichever the reader did not mean. Refusing is the
        # only answer that cannot be silently wrong, and it has to say what to
        # write instead or it is a refusal someone has to go and research.
        for name in unknown:
            if spellings := tuple(sorted(n for n in known if _bare(n) == name)):
                msg = (
                    f"{subject} names {kind} {name!r}, which more than one source "
                    f"offers -- naming it alone would silently pick one: "
                    f"write {', '.join(spellings)}"
                )
                raise CapabilityError(msg)
        shown = listing if listing is not None else f"{tuple(sorted(known))}"
        # `offered:` rather than "this workspace offers" or "this request
        # offers": who owns the set differs by kind -- a workspace offers tools
        # and skills, a request offers the subagents it activated -- and one
        # message serving five callers cannot claim an owner without being wrong
        # for some of them. It also stopped the sentence repeating its subject.
        msg = f"{subject} names unknown {kind}(s): {', '.join(unknown)}; offered: {shown}"
        raise CapabilityError(msg)


def refuse_ungranted_endpoint(endpoint: str, *, granted: Selection, subject: str) -> None:
    """Refuse an endpoint this request may not reach.

    The other half of `refuse_ungranted_models`, and it is deliberately a second
    check rather than the same one. A model grant says which names a caller may
    ask for; this says which endpoints may receive prompts and whose credentials
    may pay. They overlap and do not coincide -- "anything on the local gateway,
    nothing on OpenAI" is one line here and an enumeration there, which goes
    stale the moment a model is added.

    Called with the endpoint a model *resolved to*, not one a definition wrote:
    definitions name models now. Which is why this takes a name rather than a
    spec -- by the time the endpoint is known, the caller has a `Config` and
    this layer does not.
    """
    if granted == ALL or endpoint in (granted or ()):
        return
    msg = (
        f"{subject} resolves to endpoint {endpoint!r}, which this request may not "
        f"reach; permitted {granted}"
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

    Two refusals, and both raise, **for a definition that named names**. A name
    nothing registered is a mistake in the definition. A name the deployment
    registered but did not *grant* is an escalation attempt or a
    misconfiguration, and running with silently less middleware than the
    definition specified could mean running without the rate limit or the audit
    hook it was written to have.

    A wildcard is the third branch and behaves like neither. `["*"]` resolves
    smaller when the request narrowed the axis, and to *nothing* when the request
    withheld it -- no refusal either way. Deliberate: a star asks for a set rather
    than for particular names, so there is nothing in particular to refuse on
    behalf of, and a star that refused whenever anything was withheld would stop
    any deployment keeping a hook from one caller without breaking every definition
    that wrote one. The shortfall is not reported back either;
    `reporting.withheld_by_kind` carries that decision.

    Checked identically for a catalogue definition and an uploaded one.
    `Capabilities.including` widens skills and subagents for an upload because
    those are the caller's own text; a middleware name selects code the deployment
    wrote, so an upload gets no such exemption.

    What it decides is *names*, which is all this layer knows about middleware.
    Turning an approved name into an object needs the registry, and that is the
    caller's half.
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


def approved_settings(
    wrote: Mapping[str, object],
    *,
    settable: Iterable[str],
    subject: str,
    registered_as: str,
) -> dict[str, object]:
    """Which of the settings a definition wrote beside a name it may actually pass.

    The second half of `approved_middleware`, on the axis that one added. A name
    selects code the deployment wrote; a setting reaches *into* that code, so the
    class behind the name says which of its own keys a definition is allowed to
    write and this refuses the rest.

    Refuses rather than drops, which is the same choice made one function up and
    for a sharper reason. A definition that wrote a setting believes it took
    effect, and the failure it is guarding against is exactly the one where it
    did not: running with a value the deployment chose while the file says
    otherwise is how a cap nobody raised turns out to have been raised.

    `yaml_settable` is a class attribute, written once beside the code it governs,
    and a class that declares none -- the default -- is a class no definition may
    configure at all. `CallCap` is deliberately that: a cap a definition can set is
    not a cap, so `limit` is absent from its `yaml_settable`, and the way to have a
    looser one is a second registry entry over a subclass.

    Takes the permitted *names* rather than the class: reading an attribute off a
    registered object is the caller's half.
    """
    if not wrote:
        return {}
    permitted = set(settable)
    unknown = tuple(key for key in wrote if key not in permitted)
    if unknown:
        offered = tuple(sorted(permitted))
        # Worded around what the *class* allows rather than what the file wrote,
        # because the fix is nearly always to stop writing the key rather than to
        # spell it differently -- and a class offering none should say so plainly
        # instead of printing an empty tuple and leaving the reader guessing.
        allows = (
            f"{registered_as!r} takes settings for {', '.join(offered)}"
            if offered
            else f"{registered_as!r} takes no settings from a definition at all"
        )
        msg = (
            f"{subject} writes settings {', '.join(repr(k) for k in unknown)} for middleware "
            f"{registered_as!r}, which it does not accept; {allows}. A setting a "
            f"definition may write is one the class named in `yaml_settable`"
        )
        raise CapabilityError(msg)
    return dict(wrote)


#: What a deployment permits when it says nothing, which is exactly the default a
#: request gets: `"*"` on either side means "everything this agent declares", so
#: both jobs want the same answer. A name rather than a bare constructor, because
#: `granted.intersect(asked)` reads better when the left-hand side says what it is.
UNRESTRICTED = Capabilities()


#: Two lists into one, for a delegate that may narrow both. Here rather than in
#: `tools.spec` because it takes two `Selection`s and answers a third, never
#: touching a registry -- which is the line that module draws.

def ceiling(
    asked_builtin: Selection,
    asked_tools: Selection,
    *,
    granted_builtin: Selection,
    granted_tools: Selection,
    subject: str,
) -> Selection:
    """Every tool a delegate may call, from the two lists it may narrow.

    Not a method on `Offering`, which is the obvious move and is wrong: a delegate
    is narrowed by what the *request was granted*, not by what the workspace
    offers. Those differ exactly when a request narrowed something, which is the
    case this exists for, so an `Offering.ceiling` would silently widen a delegate
    back to the workspace.

    Answers `ALL` for "narrowed by nobody" where `Offering.permitted` answers
    `None`. Two consumers, two conventions: a delegate's selection is narrowed
    again downstream, a request's is handed to a middleware. Folding them would
    make one of the two lie.
    """
    from_builtin = narrowed(asked_builtin, by=granted_builtin)
    from_workspace = narrowed(asked_tools, by=granted_tools)
    if from_builtin == ALL and from_workspace == ALL:
        return ALL
    if ALL in (from_builtin, from_workspace):
        # Quiet if unguarded: `ALL` is the string `"*"`, so unpacking it into
        # the union contributes a tool *named* `*` and drops the axis it stood
        # for. An allowlist is one flat set of names and cannot say "all of
        # those, plus these".
        msg = (
            f"{subject}: one tool axis resolved to {ALL!r} while the other named "
            f"tools ({from_builtin!r} / {from_workspace!r}). Resolve both against "
            f"what is offered before calling this, or neither"
        )
        raise ValueError(msg)
    return (*(from_builtin or ()), *(from_workspace or ()))
