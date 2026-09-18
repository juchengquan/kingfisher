"""Finding a workspace's own tools on disk."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kingfisher.kinds.importing import (
    PACKAGE_MARKER,
    Export,
    LoadError,
    exported_from,
    modules_in,
)
from kingfisher.kinds.tools.spec import Found, named, tool_name

if TYPE_CHECKING:
    pass

__all__ = [
    "EXPORT",
    "PACKAGE_MARKER",
    "CarriedTools",
    "Found",
    "LocalToolRepository",
    "ToolError",
    "refuse_untoollike",
    "tool_name",
]

#: What a module must define: the tools it contributes, as a sequence.
EXPORT = "TOOLS"


class ToolError(LoadError):
    """A workspace's tool module could not be loaded, or should not be."""


DECLARES = Export(EXPORT, error=ToolError, holding="tools", example="my_tool")


def refuse_untoollike(tool: Any, *, where: str, holder: str) -> None:
    """Refuse an entry that is not a tool, saying which of the two mistakes it is.

    Shared by the two places tools arrive as objects rather than as names -- a module's
    `TOOLS`, and a definition carrying its own. Both fail the same ways, and `holder`
    is the only difference: a copy of this beside the second caller would drift from
    the first, which is what these messages exist to stop happening to a workspace.
    """
    # A class is never a tool, and this is the one mistake in this area that
    # produces a *successful* wrong answer rather than an error. `TOOLS =
    # [Shout]` instead of `[Shout()]` loads, is advertised under the class
    # name rather than its own `name` field -- on a pydantic model that
    # field is not a class attribute, so `tool_name` falls through to
    # `__name__` -- and then calling it *instantiates* it. Measured: the
    # model gets `status="success"` and the repr of a `CallbackManager`, and
    # the run carries on.
    if isinstance(tool, type):
        msg = (
            f"{where}: {holder} names the class {tool.__name__!r} rather "
            f"than a tool -- write {tool.__name__}() to build one. A class "
            f"loads and is offered to the model, and calling it returns a "
            f"new instance as if it were an answer"
        )
        raise ToolError(msg)
    # And an entry that is not a tool in any of the three shapes the format
    # documents: a `BaseTool` from `@tool`, an instantiated `BaseTool`
    # subclass, or a plain function. Anything else was accepted and named by
    # its `repr` -- measured, a workspace writing `TOOLS = ["line_count"]`
    # for the *name* of its tool got one advertised as `'line_count'`,
    # quotes included, and a build that died with `AttributeError:
    # 'function' object has no attribute 'name'` naming neither the file nor
    # the entry.
    #
    # It is also langchain's rule, which is why it is the right one and not
    # just the one available here. Measured against
    # `convert_to_openai_tool`: a plain function is named `shout`, a lambda
    # `<lambda>`, and everything without one of those two attributes -- a
    # `functools.partial`, an instance with `__call__` -- dies there with
    # the same `AttributeError` this refuses, only later and naming no file.
    # So `callable` would have been wrong twice over: a `BaseTool` is *not*
    # callable and would be refused, a `partial` is and would be let
    # through.
    if not named(tool):
        msg = (
            f"{where}: {holder} holds {type(tool).__name__} "
            f"{tool!r}, which is not a tool and has no name -- write "
            f"the tool itself, not its name. A tool is what `@tool` "
            f"returns, an instance of a `BaseTool` subclass, or a "
            f"plain function"
        )
        raise ToolError(msg)


@dataclass(frozen=True)
class CarriedTools:
    """Tools a definition brought with it, offered as a repository like any other.

    A directory is the usual backing and cannot be one here: a subagent imported from
    an installed package has no folder under the catalogue, so its tools arrive as the
    objects themselves. Everything downstream asks a repository for `found`, so this
    is what lets a carried bundle reach the same consumers a folder does.
    """

    tools: tuple[Any, ...]
    #: What a `where::what` reference would name these by. Not a path: there is no
    #: file to open, and a reference that pointed at one would send a reader looking
    #: for something the deployment does not have.
    source: str

    @cached_property
    def found(self) -> tuple[Found, ...]:
        """Every tool carried here, checked the way a module's are."""
        seen: set[str] = set()
        entries: list[Found] = []
        for tool in self.tools:
            refuse_untoollike(tool, where=self.source, holder="bundle tools")
            name = tool_name(tool)
            if name in seen:
                # The same refusal a module gets for the same reason: two tools
                # under one name have no second source to tell them apart, so
                # nothing downstream could offer a way to pick between them.
                msg = f"{self.source}: tool {name!r} is carried twice by this definition"
                raise ToolError(msg)
            seen.add(name)
            entries.append(Found(tool=tool, source=self.source))
        return tuple(entries)

    @property
    def names(self) -> tuple[str, ...]:
        """Tool names carried here."""
        return tuple(found.name for found in self.found)

    @property
    def root(self) -> None:
        """No directory: these arrived as objects."""
        return None


@dataclass(frozen=True)
class LocalToolRepository:
    """The tools defined in one directory, imported into this process."""

    root: Path

    @cached_property
    def found(self) -> tuple[Found, ...]:
        """Every tool this directory defines, with its origin, in a stable order."""
        directory = Path(self.root)
        if not directory.is_dir():
            return ()

        found: list[Found] = []
        claimed: dict[str, str] = {}
        for path in modules_in(directory):
            # Relative to the catalogue, so an error names something a reader
            # can go and open. `find_company.py` is ambiguous once three folders
            # may hold one; `research/find_company.py` is not. A package keeps
            # its trailing slash so it does not read as a file that is not
            # there.
            where = str(path.relative_to(directory)) + ("/" if path.is_dir() else "")

            for tool in exported_from(path, where=where, declares=DECLARES):
                refuse_untoollike(tool, where=where, holder=EXPORT)
                name = tool_name(tool)
                if name in claimed and claimed[name] == where:
                    # Within one file it is a plain mistake: the same module
                    # exporting a name twice has no second source to tell them
                    # apart, and nothing downstream could offer a way to pick.
                    msg = f"{where}: tool {name!r} is defined twice in this file"
                    raise ToolError(msg)
                claimed[name] = where
                found.append(Found(tool=tool, source=where))

        return tuple(found)

    @property
    def tools(self) -> tuple[Any, ...]:
        """The objects alone: what a directory offers, said the short way."""
        return tuple(found.tool for found in self.found)

    @property
    def names(self) -> tuple[str, ...]:
        """Tool names offered here."""
        return tuple(found.name for found in self.found)
