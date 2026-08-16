"""What a tool is, seen from the domain: an object, and where it came from.

Here rather than in `tool_store` because `ToolRepository` is a port, and a port
in `domain/ports.py` cannot name a type that lives one layer out. Nothing
foreign travels with it: `tool` is `Any` on purpose and `tool_name` is three
`getattr` calls, so the pure layer stays pure under the same rule
`test_domain_imports_only_the_standard_library_and_itself` enforces.

That `Any` is doing real work, and it is worth being honest about rather than
letting the import scan speak for it. What a `Found` holds is, in practice, a
langchain `BaseTool`. The domain never calls one, never imports the type and
never depends on its shape -- it carries the object from the loader that
imported it to the agent that runs it, and asks it for a name it may not have.
If that ever stops being true, the fix is a domain-owned description of a tool,
not a wider import here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def tool_name(tool: Any) -> str:
    """What a request names this tool by.

    `BaseTool` carries `.name`; a bare callable is named by the function. Both
    are accepted because `create_deep_agent` accepts both, and a definition
    should not have to know which one deepagents prefers this month.
    """
    return getattr(tool, "name", None) or getattr(tool, "__name__", None) or repr(tool)


@dataclass(frozen=True)
class Found:
    """One tool and the file it came from, relative to the catalogue.

    The pair rather than either alone, because every caller that wants one
    eventually wants the other: the agent needs the object, and anything that
    has to *say* something about a tool -- a listing, a refusal -- needs
    somewhere a reader can go and open.
    """

    tool: Any
    source: str

    @property
    def name(self) -> str:
        return tool_name(self.tool)
