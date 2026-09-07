"""What a request is allowed to use.

Turn-scoped: they travel with a request rather than being fixed for a workspace or a
conversation. Construction is not the cost -- an agent rebuild is 8ms empty and 54ms
seeded, and `docs/findings.md` records that the smaller figure is the misleading one.
The cost is prompt caching, and only when the set actually *changes*, since the cache
compares bytes and does not care that we rebuilt: a caller passing the same set every
turn keeps its hits.
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
    """What one request may use, out of what the deployment has wired."""

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
    #: Endpoints a definition may reach. Granted like `middleware` and for a stronger
    #: reason: this one decides which credentials are used and which endpoint receives
    #: the run's prompts and files. Checked against where a named model *resolves to*,
    #: since definitions name models rather than endpoints.
    endpoints: Selection = ALL
    #: Models a request may put a delegate on, overriding what its file says.
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
        """True when nothing is narrowed, so the agent can be built as configured."""
        return self == Capabilities()

    def including(
        self, *, skills: tuple[str, ...] = (), subagents: tuple[str, ...] = ()
    ) -> Capabilities:
        """Widen by definitions the request brought with it.

        **`middleware` and `endpoints` are deliberately absent**, and that absence is
        the rule. A skill or subagent an upload brings is the caller's own text; a
        middleware *name* selects code the deployment wrote. Widening it would let
        anyone who can upload a definition activate anything the deployment
        registered -- the escalation the rest of this method exists to avoid.
        `endpoints` is the same argument with more at stake: it chooses which
        endpoint receives the run's prompts and files, and whose credentials pay for
        them.
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
        """Narrow these capabilities by another set. Never widens."""
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
    """A refusal from either side wins; otherwise the side with an opinion does."""
    if left is False or right is False:
        return False
    if left is True or right is True:
        return True
    return None


def belongs_in(names: tuple[str, ...], *, field: str) -> str:
    """"that is a builtin tool -- name it in builtin_tools", agreeing in number."""
    # `split`/`join` rather than `.replace`, which `test_domain_touches_nothing`
    # forbids here: `Path.replace` renames a file, and the check reads names rather
    # than types. A blunt guard is the point of that test, so this bends to it.
    kind = " ".join(field[:-1].split("_"))
    if len(names) == 1:
        return f"that is a {kind} -- name it in {field}"
    return f"those are {kind}s -- name them in {field}"


def narrowed(selection: Selection, *, by: Selection) -> Selection:
    """`selection`, keeping only what `by` also allows. Never widens."""
    if selection is None or by is None:
        return None  # nothing, narrowed by anything at all, is still nothing
    if selection == ALL:
        return by
    if by == ALL:
        return selection
    allowed = set(by)
    return tuple(name for name in selection if name in allowed)


def _widened(selection: Selection, extra: tuple[str, ...]) -> Selection:
    """`selection` plus names the caller brought with it -- see `including`."""
    if selection is None or selection == ALL:
        return selection
    return (*selection, *extra)


def withheld(granted: Selection, *, offered: Iterable[str]) -> tuple[str, ...]:
    """Names the workspace offers that this grant leaves out."""
    if granted == ALL:
        return ()
    if granted is None:
        return tuple(sorted(offered))
    permitted = set(granted)
    return tuple(sorted(name for name in offered if name not in permitted))


def all_but(excluded: tuple[str, ...], *, offered: Iterable[str]) -> tuple[str, ...]:
    """The grant that "everything except these" means, against what is offered now."""
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
    """Refuse a model a request may not put a delegate on."""
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
    """Refuse a name nothing offers, whoever named it."""
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
    """Refuse an endpoint this request may not reach."""
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
    registered but did not *grant* is an escalation attempt or a misconfiguration,
    and running with silently less middleware than the definition specified could
    mean running without the rate limit or the audit hook it was written to have.
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
    """Which of the settings a definition wrote beside a name it may actually pass."""
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
    """Every tool a delegate may call, from the two lists it may narrow."""
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
