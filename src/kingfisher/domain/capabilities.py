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


def _normalise(value: Iterable[str] | None) -> Selection:
    if value is None:
        return None
    return tuple(dict.fromkeys(str(v) for v in value))  # de-duped, order kept


@dataclass(frozen=True)
class Capabilities:
    """The tools, skills and subagents active for one request.

        Capabilities()                                  # everything configured
        Capabilities(tools=())                          # no tools at all
        Capabilities(tools=("read_file", "glob"))       # read-only
    """

    tools: Selection = None
    skills: Selection = None
    subagents: Selection = None

    def __post_init__(self) -> None:
        for field_name in ("tools", "skills", "subagents"):
            object.__setattr__(self, field_name, _normalise(getattr(self, field_name)))

    @property
    def is_unrestricted(self) -> bool:
        """True when nothing is narrowed, so the agent can be built as configured."""
        return self.tools is None and self.skills is None and self.subagents is None

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
            tools=_narrow(self.tools, other.tools),
            skills=_narrow(self.skills, other.skills),
            subagents=_narrow(self.subagents, other.subagents),
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


def _narrow(left: Selection, right: Selection) -> Selection:
    if left is None:
        return right
    if right is None:
        return left
    allowed = set(left)
    return tuple(name for name in right if name in allowed)


#: No restriction at all — the default a bare `run("do a thing")` gets.
UNRESTRICTED = Capabilities()
