"""Finding a workspace's own tools on disk.

The third of the three kinds this package is named for, and the odd one out.
A skill is markdown and a
subagent is a YAML document; both are *data* the agent reads. A tool
is Python, imported into this process and called in it. There is no format to
parse here — only a module to import and a decision about what to refuse.

That difference is the whole design:

- **`TOOLS` is declared, never inferred.** Scanning a module for anything
  callable would guess at intent, and a helper promoted to a tool by accident
  is worse than one that never appears. The other formats make a definition
  state its own name; this makes a module state its own exports.
- **A module that will not import is an error, not a skipped file.** Quietly
  offering fewer tools than the workspace defines is the failure
  `CapabilityError` already exists to prevent, one layer down.
- **Nothing here is routed to the agent.** `/skills` and `/data` are backend
  routes; the tools directory deliberately is not one, so no file tool can
  reach it. An agent that could still write here is one holding `execute`,
  which already runs arbitrary code on the host — so this adds a way in that
  is no wider than the one already open, and none at all for a request without
  the shell.

That last point is also why this is the directory that gets to have folders.
Skills are read by deepagents off the filesystem, one level down and no
further, so nesting one makes it invisible rather than tidy. Nothing outside
kingfisher reads this directory, so nothing outside kingfisher has an opinion
about how deep it goes.

Two shapes, and `__init__.py` decides which:

    tools/                      a flat catalogue, as it always was
    tools/research/*.py         organisation; each file independent
    tools/research/__init__.py  a package; one unit, imported whole

A package is where the walk stops. Descending into one would scan the helper
modules it exists to hold as though each were a tool file — and a tool growing
helpers is the entire reason to write a folder. So the folder imports the way
Python says a folder imports, relative imports and all, and declares its
exports once.

Folders never reach a *name*. A tool is named by itself, so nesting cannot
change what a request grants, what the allowlist enforces, or what the model
calls. It changes where a person looks for the file, which is what it is for.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kingfisher.infrastructure.catalogue.importing import (
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
    """A workspace's tool module could not be loaded, or should not be.

    A `LoadError` since the loader moved to `importing`, so the shared code can
    raise the caller's own error and a reader still sees the word for the
    catalogue they were editing.
    """


@dataclass(frozen=True)
class LocalToolRepository:
    """The tools defined in one directory, imported into this process.

    Given the directory rather than a workspace to derive one from, for the
    same reason `LocalSkillRepository` is: a catalogue may be deployed outside
    any workspace and shared by all of them.

    A class rather than a set of free functions, and here that is not tidying.
    These modules are *executed* to be read, and each function funnelled back
    into a fresh walk -- so a caller wanting two answers imported every tool file
    twice, paying twice the import cost and running any module-level side effect
    twice over. One instance reads once, and `found`, `tools` and `names` all
    come from that one read.
    """

    root: Path

    @cached_property
    def found(self) -> tuple[Found, ...]:
        """Every tool this directory defines, with its origin, in a stable order.

        Folders are read for the sake of whoever has to find a file again, and
        now for one thing more: two files may each define a `fetch`. Vendors do
        not coordinate names, and refusing the pair here stopped a deployment
        over a clash no single agent would ever have seen -- unfixable by
        anyone who owns neither file.

        So the catalogue holds both, under `vendor_a/fetch.py::fetch` and
        `vendor_b/fetch.py::fetch`, and the refusal moves to the place the
        constraint actually lives: an *agent* dispatches by name, so an agent
        granted both is refused. A name still stays flat wherever it is unique,
        which is every catalogue that has no collision.

        Twice in one file is still refused here, because there is no second
        source to tell those apart and so nothing downstream could offer a way
        to pick.
        """
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
                # A class is never a tool, and this is the one mistake in this
                # area that produces a *successful* wrong answer rather than an
                # error. `TOOLS = [Shout]` instead of `[Shout()]` loads, is
                # advertised under the class name rather than its own `name`
                # field -- on a pydantic model that field is not a class
                # attribute, so `tool_name` falls through to `__name__` -- and
                # then calling it *instantiates* it. Measured: the model gets
                # `status="success"` and the repr of a `CallbackManager`, and
                # the run carries on.
                #
                # The same family as the container check above, one level in:
                # there the whole export was a pydantic model that iterated,
                # here one entry is a class that instantiates. Both pass a duck
                # test and neither says anything.
                if isinstance(tool, type):
                    msg = (
                        f"{where}: {EXPORT} names the class {tool.__name__!r} rather "
                        f"than a tool -- write {tool.__name__}() to build one. A class "
                        f"loads and is offered to the model, and calling it returns a "
                        f"new instance as if it were an answer"
                    )
                    raise ToolError(msg)
                # And an entry that is not a tool in any of the three shapes the
                # format documents: a `BaseTool` from `@tool`, an instantiated
                # `BaseTool` subclass, or a plain function. Anything else was
                # accepted and named by its `repr` -- measured, a workspace
                # writing `TOOLS = ["line_count"]` for the *name* of its tool got
                # one advertised as `'line_count'`, quotes included, and a build
                # that died with `AttributeError: 'function' object has no
                # attribute 'name'` naming neither the file nor the entry.
                #
                # Asked as `named`, which is the domain's own rule rather than
                # a second copy of it: `tool_name` is `.name or .__name__ or
                # repr(tool)`, and that last fallback is the hole. It is there so
                # naming never raises, which a listing needs -- and it means
                # anything at all gets *a* name instead of a refusal.
                #
                # It is also langchain's rule, which is why it is the right one
                # and not just the one available here. Measured against
                # `convert_to_openai_tool`: a plain function is named `shout`, a
                # lambda `<lambda>`, and everything without one of those two
                # attributes -- a `functools.partial`, an instance with
                # `__call__` -- dies there with the same `AttributeError` this
                # refuses, only later and naming no file. So `callable` would
                # have been wrong twice over: a `BaseTool` is *not* callable and
                # would be refused, a `partial` is and would be let through.
                #
                # Not `isinstance(tool, BaseTool)`, the first attempt: this area
                # may import `yaml` and nothing else, because a catalogue reads
                # files and `Found.tool` is `Any` on purpose. The architecture
                # rule caught it and was right to.
                #
                # A class passes this check -- it has `__name__` -- so the class
                # refusal above is not made redundant by it and neither ordering
                # would change what either one says.
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
        """The objects alone: what a directory offers, said the short way.

        Two derived views survive here and a third did not, so the line is worth
        drawing. This one is the question the loader exists to answer -- "what
        does this directory define" -- and it is how every test of import
        failure, duplicate names and package handling says what it is checking.
        """
        return tuple(found.tool for found in self.found)

    @property
    def names(self) -> tuple[str, ...]:
        """Tool names offered here.

        `AssetRepository.names` requires it of every kind, which is the reason
        it is here: nothing in this package reads it off a *tool* repository,
        because names reach the capability layer through `Found.name` and
        `Offering`. A substituted repository still has to be able to list
        itself.
        """
        return tuple(found.name for found in self.found)
