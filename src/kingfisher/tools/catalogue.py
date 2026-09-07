"""Finding a workspace's own tools on disk."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kingfisher.infrastructure.importing import (
    PACKAGE_MARKER,
    LoadError,
    load,
    modules_in,
)
from kingfisher.tools.spec import Found, named, tool_name

if TYPE_CHECKING:
    pass

__all__ = [
    "EXPORT",
    "PACKAGE_MARKER",
    "Found",
    "LocalToolRepository",
    "ToolError",
    "tool_name",
]

#: What a module must define: the tools it contributes, as a sequence.
EXPORT = "TOOLS"

class ToolError(LoadError):
    """A workspace's tool module could not be loaded, or should not be."""


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

            module = load(path, declares=EXPORT, error=ToolError)
            exported = getattr(module, EXPORT, None)
            if exported is None:
                declared_in = f"{where}{PACKAGE_MARKER}" if path.is_dir() else where
                msg = f"{declared_in}: must define {EXPORT}, the tools it contributes"
                raise ToolError(msg)
            # A list or a tuple, and nothing looser. `BaseTool` is a pydantic
            # model and pydantic models are iterable, so `TOOLS = add` would
            # pass a duck test and then quietly iterate the tool's own fields.
            if not isinstance(exported, (list, tuple)):
                msg = (
                    f"{where}: {EXPORT} must be a list or tuple of tools, "
                    f"got {type(exported).__name__} -- write {EXPORT} = [my_tool]"
                )
                raise ToolError(msg)

            for tool in exported:
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
                        f"{where}: {EXPORT} names the class {tool.__name__!r} rather "
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
                        f"{where}: {EXPORT} holds {type(tool).__name__} "
                        f"{tool!r}, which is not a tool and has no name -- write "
                        f"the tool itself, not its name. A tool is what `@tool` "
                        f"returns, an instance of a `BaseTool` subclass, or a "
                        f"plain function"
                    )
                    raise ToolError(msg)
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
