"""What a request is allowed to use.

Capabilities are turn-scoped: they travel with a request rather than being
fixed for a workspace or a conversation. Rebuilding an agent costs about 8ms,
so the cost is not construction — it is prompt caching, and only when the set
actually *changes*, since the cache compares bytes and does not care that we
rebuilt. A caller passing the same set every turn keeps its cache hits.

Names, never definitions. A request activates what the workspace already
offers; it cannot invent a tool or write a subagent's prompt. That keeps
definitions reviewable in git and means an untrusted caller can widen nothing.

Unset means "everything this workspace offers"; an empty tuple means none. That
default is deliberate and its consequence is deliberate too: authorisation is
not the request's job. A request states intent, and a service decides what a
given caller may have, by clamping with `intersect` before the request is run.
Baking fail-closed in here would make callers authorise themselves.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

#: `None` means unrestricted; a tuple — including an empty one — is a whitelist.
#:
#: The declared contract is tuples, and consumers can rely on that. `__post_init__`
#: is nonetheless lenient about what it will normalise, because a service
#: deserialising JSON hands us lists; that leniency is a backstop, not the
#: contract, so a caller holding a list should convert at its own edge.
Selection = tuple[str, ...] | None


class CapabilityError(ValueError):
    """A request named a tool, skill, subagent or middleware it may not have.

    Here rather than in `infrastructure`, where it lived, because it says
    nothing about the harness: a name asked for and not offered is kingfisher's
    own vocabulary, and the rules that raise it are in this module.
    """


def _normalise(value: Iterable[str] | None) -> Selection:
    if value is None:
        return None
    return tuple(dict.fromkeys(str(v) for v in value))  # de-duped, order kept


@dataclass(frozen=True)
class Capabilities:
    """What one request may use, out of what the deployment has wired.

        Capabilities()                                  # everything configured
        Capabilities(tools=())                          # no tools at all
        Capabilities(tools=("read_file", "glob"))       # read-only
        Capabilities(memory=False)                      # do not read the memory file

    Four of these name things and one is a switch, because memory has no names
    to choose between -- it is one file, either mounted or not. `None` still
    means "no opinion" for all five, which is what keeps the default free.

    This is the *narrowing* axis. The other one is `Config.memory_enabled` and
    `Config.skills_enabled`: what this deployment wired at all. Those shape the
    system prompt and so must stay stable across requests; these do not, and
    vary per turn. Narrowing can only ever subtract from wiring -- asking for
    memory a deployment never wired does not conjure it.
    """

    tools: Selection = None
    skills: Selection = None
    subagents: Selection = None
    #: Middleware a definition may name, out of what the deployment registered.
    #: Unlike the three above it is never widened by `including` -- see there.
    middleware: Selection = None
    #: Endpoints a definition may name. Granted like `middleware` and for a
    #: stronger reason: this one decides which credentials are used and which
    #: endpoint receives the run's prompts and files.
    providers: Selection = None
    memory: bool | None = None

    def __post_init__(self) -> None:
        for field_name in ("tools", "skills", "subagents", "middleware", "providers"):
            object.__setattr__(self, field_name, _normalise(getattr(self, field_name)))

    @property
    def is_unrestricted(self) -> bool:
        """True when nothing is narrowed, so the agent can be built as configured."""
        return (
            self.tools is None
            and self.skills is None
            and self.subagents is None
            and self.middleware is None
            and self.providers is None
            and self.memory is None
        )

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

        Unrestricted stays unrestricted: it already includes these.
        """
        return Capabilities(
            tools=self.tools,
            skills=self.skills if self.skills is None else (*self.skills, *skills),
            subagents=(
                self.subagents if self.subagents is None else (*self.subagents, *subagents)
            ),
            middleware=self.middleware,  # never widened; see above
            providers=self.providers,  # nor this: it chooses where prompts go
            memory=self.memory,
        )

    def intersect(self, other: Capabilities) -> Capabilities:
        """Narrow these capabilities by another set. Never widens.

        This is what a service calls to clamp an incoming request against what
        the caller is permitted:

            granted.intersect(request.capabilities)

        Unrestricted on either side means "no opinion", so the other side wins;
        where both name things, only the overlap survives. Because it can only
        remove, a caller cannot escalate by asking for more.
        """
        return Capabilities(
            tools=narrowed(other.tools, by=self.tools),
            skills=narrowed(other.skills, by=self.skills),
            subagents=narrowed(other.subagents, by=self.subagents),
            middleware=narrowed(other.middleware, by=self.middleware),
            providers=narrowed(other.providers, by=self.providers),
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
            (self.tools, tools, "tool"),
            (self.skills, skills, "skill"),
            (self.subagents, subagents, "subagent"),
        ):
            if requested is None:
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
    if selection is None:
        return by
    if by is None:
        return selection
    allowed = set(by)
    return tuple(name for name in selection if name in allowed)


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

    `None` withholds nothing: it is the unrestricted grant, and it does not go
    stale because it names nothing to go stale.
    """
    if granted is None:
        return ()
    permitted = set(granted)
    return tuple(sorted(name for name in offered if name not in permitted))


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

    if granted is not None:
        permitted = set(granted)
        ungranted = tuple(name for name in declared if name not in permitted)
        if ungranted:
            msg = (
                f"{subject} names middleware this request may not use: "
                f"{', '.join(ungranted)}; permitted {granted}"
            )
            raise CapabilityError(msg)

    return tuple(declared)


#: No restriction at all — the default a bare `run("do a thing")` gets.
UNRESTRICTED = Capabilities()
