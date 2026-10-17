"""Finding a workspace's own tools on disk."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kingfisher.core.definitions.importing import (
    PACKAGE_MARKER,
    Export,
    LoadError,
    exported_from,
    modules_in,
)
from kingfisher.core.definitions.tools.spec import (
    Found,
    named,
    own_audience,
    own_datazone,
    tool_name,
)

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

    Shared by a module's `TOOLS` and a definition carrying its own, so the two agree.
    """
    # A class loads, is advertised under its `__name__`, and calling it instantiates it:
    # a wrong answer reported as a success rather than an error.
    if isinstance(tool, type):
        msg = (
            f"{where}: {holder} names the class {tool.__name__!r} rather "
            f"than a tool -- write {tool.__name__}() to build one. A class "
            f"loads and is offered to the model, and calling it returns a "
            f"new instance as if it were an answer"
        )
        raise ToolError(msg)
    # Not `callable`, which is wrong twice over: a `BaseTool` is not callable, and a
    # `partial` is but has no name, so `convert_to_openai_tool` would raise on it later.
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
    """Tools a definition brought with it, offered as a repository like any other."""

    tools: tuple[Any, ...]
    #: What a `where::what` reference names these by: a label, never a path, since there
    #: is no file to open.
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
                # Two tools of one name here have no second source to tell them apart.
                msg = f"{self.source}: tool {name!r} is carried twice by this definition"
                raise ToolError(msg)
            seen.add(name)
            entries.append(
                Found(
                    tool=tool,
                    source=self.source,
                    source_ids=own_audience(tool),
                    datazone=own_datazone(tool),
                )
            )
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
            # Relative to the catalogue, so an error names a file a reader can open; a
            # package keeps its trailing slash so it does not read as a missing file.
            where = str(path.relative_to(directory)) + ("/" if path.is_dir() else "")

            for tool in exported_from(path, where=where, declares=DECLARES):
                refuse_untoollike(tool, where=where, holder=EXPORT)
                name = tool_name(tool)
                if name in claimed and claimed[name] == where:
                    # Only within one file: across files, the source tells them apart.
                    msg = f"{where}: tool {name!r} is defined twice in this file"
                    raise ToolError(msg)
                claimed[name] = where
                found.append(
                    Found(
                        tool=tool,
                        source=where,
                        source_ids=own_audience(tool),
                        datazone=own_datazone(tool),
                    )
                )

        return tuple(found)

    @property
    def tools(self) -> tuple[Any, ...]:
        """The tool objects alone."""
        return tuple(found.tool for found in self.found)

    @property
    def names(self) -> tuple[str, ...]:
        """Tool names offered here."""
        return tuple(found.name for found in self.found)
