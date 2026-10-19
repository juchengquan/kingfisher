"""Finding a workspace's own tools on disk."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kingfisher.core.definitions.fields import Reader
from kingfisher.core.definitions.importing import (
    PACKAGE_MARKER,
    Export,
    LoadError,
    exported_from,
    modules_in,
)
from kingfisher.core.definitions.tools.spec import FoundTool, ToolSpec, named, tool_name

if TYPE_CHECKING:
    pass

__all__ = [
    "EXPORT",
    "PACKAGE_MARKER",
    "CarriedTools",
    "FoundTool",
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


def found_in(entry: Any, *, where: str, holder: str) -> FoundTool:
    """One entry of `TOOLS`, or of a definition's carried tools, as the catalogue keeps it.

    A bare tool is read as `ToolSpec(tool)`, open to everyone.
    """
    spec = entry if isinstance(entry, ToolSpec) else ToolSpec(entry)
    refuse_untoollike(spec.tool, where=where, holder=holder)
    tool = _as_named(spec, where=where)
    read = Reader(source=f"{where}: tool {tool_name(tool)!r}", error=ToolError)
    return FoundTool(
        tool=tool,
        source=where,
        source_ids=read.source_ids(spec.source_ids),
        datazone=read.datazone(spec.datazone),
    )


def _as_named(spec: ToolSpec, *, where: str) -> Any:
    """The tool under the name and description its spec gives, or as it was written."""
    wanted = {
        key: value
        for key, value in (("name", spec.name), ("description", spec.description))
        if value is not None
    }
    if not wanted:
        return spec.tool
    original = tool_name(spec.tool)
    for key, value in wanted.items():
        if not isinstance(value, str) or not value.strip():
            msg = (
                f"{where}: the ToolSpec for {original!r} gives {key}={value!r} -- write "
                f"the {key} itself, or leave it out to keep the tool's own"
            )
            raise ToolError(msg)

    # Deferred: the command imports this module before it knows which verb it runs.
    from langchain_core.tools import BaseTool, StructuredTool  # noqa: PLC0415

    tool = spec.tool
    try:
        if isinstance(tool, BaseTool):
            # A copy, so the object keeps its own name wherever else its author uses it.
            made = tool.model_copy(update=wanted)
        else:
            asynchronous = inspect.iscoroutinefunction(tool)
            made = StructuredTool.from_function(
                func=None if asynchronous else tool,
                coroutine=tool if asynchronous else None,
                name=spec.name,
                description=spec.description,
            )
    except ValueError as exc:
        changed = " and ".join(wanted)
        msg = f"{where}: tool {original!r} cannot take its ToolSpec's {changed} -- {exc}"
        raise ToolError(msg) from exc
    # Not redundant: a property inherited ahead of `BaseTool` wins over what a copy holds.
    if kept := [key for key, value in wanted.items() if getattr(made, key) != value]:
        msg = (
            f"{where}: tool {original!r} still answers with its own {' and '.join(kept)} "
            f"after its ToolSpec gave another -- {type(tool).__name__} computes it, as a "
            f"property does, so a copy cannot change it. Set it on the class instead"
        )
        raise ToolError(msg)
    return made


@dataclass(frozen=True)
class CarriedTools:
    """Tools a definition brought with it, offered as a repository like any other."""

    #: Each a `ToolSpec` or a bare tool, as a module's `TOOLS` holds them.
    tools: tuple[Any, ...]
    #: What a `where::what` reference names these by: a label, never a path, since there
    #: is no file to open.
    source: str

    @property
    def where(self) -> str:
        """Where an author would go to rename one of these: the label, there being no
        folder. Both readers answer it, because the shadow probe asks whichever backs a
        bundle."""
        return self.source

    @cached_property
    def found(self) -> tuple[FoundTool, ...]:
        """Every tool carried here, checked the way a module's are."""
        seen: set[str] = set()
        entries: list[FoundTool] = []
        for entry in self.tools:
            one = found_in(entry, where=self.source, holder="bundle tools")
            if one.name in seen:
                # Two tools of one name here have no second source to tell them apart.
                msg = f"{self.source}: tool {one.name!r} is carried twice by this definition"
                raise ToolError(msg)
            seen.add(one.name)
            entries.append(one)
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

    @property
    def where(self) -> str:
        """Where an author would go to rename one: this directory. See `CarriedTools`."""
        return str(self.root)

    @cached_property
    def found(self) -> tuple[FoundTool, ...]:
        """Every tool this directory defines, with its origin, in a stable order."""
        directory = Path(self.root)
        if not directory.is_dir():
            return ()

        found: list[FoundTool] = []
        claimed: dict[str, str] = {}
        for path in modules_in(directory):
            # Relative to the catalogue, so an error names a file a reader can open; a
            # package keeps its trailing slash so it does not read as a missing file.
            where = str(path.relative_to(directory)) + ("/" if path.is_dir() else "")

            for entry in exported_from(path, where=where, declares=DECLARES):
                one = found_in(entry, where=where, holder=EXPORT)
                if claimed.get(one.name) == where:
                    # Only within one file: across files, the source tells them apart.
                    msg = f"{where}: tool {one.name!r} is defined twice in this file"
                    raise ToolError(msg)
                claimed[one.name] = where
                found.append(one)

        return tuple(found)

    @property
    def tools(self) -> tuple[Any, ...]:
        """The tool objects alone."""
        return tuple(found.tool for found in self.found)

    @property
    def names(self) -> tuple[str, ...]:
        """Tool names offered here."""
        return tuple(found.name for found in self.found)
