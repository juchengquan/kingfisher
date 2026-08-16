"""Finding a workspace's own tools on disk.

The third of the store trio, and the odd one out. A skill is markdown and a
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

import importlib.util
import sys
from pathlib import Path
from typing import Any

#: What a module must define: the tools it contributes, as a sequence.
EXPORT = "TOOLS"

#: The package marker. A subfolder holding one is a unit rather than a pile:
#: it states its exports once, in here, and nothing inside it is scanned.
PACKAGE_MARKER = "__init__.py"

#: The namespace every workspace module is imported under. Never registered and
#: never on `sys.path`: it exists to make a module name unique, nothing more. A
#: package resolves its own relative imports against *itself*, so no parent has
#: to exist -- measured, after writing one that turned out to do nothing.
_NAMESPACE = "kingfisher_workspace_tools"


class ToolError(ValueError):
    """A workspace's tool module could not be loaded, or should not be."""


def tool_name(tool: Any) -> str:
    """What a request names this tool by.

    `BaseTool` carries `.name`; a bare callable is named by the function. Both
    are accepted because `create_deep_agent` accepts both, and a definition
    should not have to know which one deepagents prefers this month.
    """
    return getattr(tool, "name", None) or getattr(tool, "__name__", None) or repr(tool)


def _module_name(path: Path) -> str:
    """A name no other workspace file can collide with.

    Keyed on the full path rather than the stem: two workspaces with a
    `maths.py` each must not share an entry in `sys.modules`, and neither
    should a workspace file and a real installed package.
    """
    return f"{_NAMESPACE}.{path.stem}_{abs(hash(str(path)))}"


def _import(path: Path) -> Any:
    """Import one file, or one package, without putting it on the import path.

    A directory is imported as a package: the spec is built from its
    `__init__.py` and told the directory is where its submodules live, which is
    the whole of what makes `from .client import resolve` resolve. A tool that
    grew helpers is the reason to write a folder at all, so the folder has to
    import the way Python says a folder imports.
    """
    is_package = path.is_dir()
    source = path / PACKAGE_MARKER if is_package else path

    module_name = _module_name(path)
    spec = importlib.util.spec_from_file_location(
        module_name,
        source,
        submodule_search_locations=[str(path)] if is_package else None,
    )
    if spec is None or spec.loader is None:  # pragma: no cover -- defensive
        msg = f"{path.name}: cannot be imported as a module"
        raise ToolError(msg)

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        del sys.modules[module_name]
        # The file name matters more than the traceback here: the reader is
        # someone who just added a tool, not someone debugging kingfisher.
        msg = _relative_import_advice(path, exc) or f"{path.name}: {type(exc).__name__}: {exc}"
        raise ToolError(msg) from exc
    return module


def _relative_import_advice(path: Path, exc: Exception) -> str | None:
    """Turn a leaked internal module name into the thing to actually do.

    A loose file cannot use a relative import: it is loaded as a module with no
    parent package, so `from .client import x` resolves against the namespace
    this loader invents and fails naming it -- which tells a reader nothing
    except that kingfisher has internals. The fix is always the same, and it is
    the feature next door: make the folder a package.
    """
    if not isinstance(exc, ModuleNotFoundError) or _NAMESPACE not in str(exc.name or ""):
        return None
    return (
        f"{path.name}: a relative import needs a package, and this file is loaded on "
        f"its own. Add {PACKAGE_MARKER} to {path.parent.name}/ and declare {EXPORT} "
        f"there -- then its modules import from each other normally."
    )


def _skipped(name: str) -> bool:
    """Directories a walk must not descend into, nor a file be read from.

    New because the exposure is new. A one-level scan could never reach a
    virtualenv or a build directory left under `tools/`; a recursive one can,
    and this module *imports what it finds*. `__pycache__` is the one that
    turns up by accident -- importing a preset once leaves it behind.
    """
    return name.startswith(".") or name == "__pycache__"


def _modules_in(directory: Path) -> list[Path]:
    """Every module a directory contributes, deepest layout first resolved.

    Two shapes, and `__init__.py` is the switch between them:

    * a folder holding one is a **package** -- one unit, imported whole, its
      exports declared once in `__init__.py`. The walk stops there, because
      descending would scan the helper modules it exists to hold as though each
      were a tool file.
    * anything else is **organisation** -- files are independent, nested as
      deep as you like, and each declares its own tools exactly as a flat one
      always did.

    Sorted so two workspaces holding the same files build the same agent.
    """
    found: list[Path] = []
    for entry in sorted(directory.iterdir()):
        if _skipped(entry.name):
            continue
        if entry.is_dir():
            if (entry / PACKAGE_MARKER).is_file():
                found.append(entry)  # a package: one module, not a directory to walk
            else:
                found.extend(_modules_in(entry))
        elif entry.suffix == ".py" and not entry.name.startswith("_"):
            found.append(entry)
    return found


def load_tools(directory: Path) -> tuple[Any, ...]:
    """Every tool the directory defines, in a stable order.

    Given the directory rather than a workspace to derive one from, for the
    same reason `skill_store.names` is: a catalogue may be deployed outside any
    workspace and shared by all of them.

    Folders are read, and only for the sake of whoever has to find a file
    again. A folder cannot reach a *name* -- a tool is named by itself, never by
    where it sits -- so nesting is invisible to a request, to the grant that
    permits it, and to the model. What it is not invisible to is a second tool
    claiming a name already taken, which is refused across folders exactly as it
    is within one.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return ()

    tools: list[Any] = []
    claimed: dict[str, str] = {}
    for path in _modules_in(directory):
        # Relative to the catalogue, so an error names something a reader can
        # go and open. `find_company.py` is ambiguous once three folders may
        # hold one; `research/find_company.py` is not.
        where = path.relative_to(directory)

        module = _import(path)
        exported = getattr(module, EXPORT, None)
        if exported is None:
            declared_in = f"{where}/{PACKAGE_MARKER}" if path.is_dir() else str(where)
            msg = f"{declared_in}: must define {EXPORT}, the tools it contributes"
            raise ToolError(msg)
        # A list or a tuple, and nothing looser. `BaseTool` is a pydantic model
        # and pydantic models are iterable, so `TOOLS = add` would pass a duck
        # test and then quietly iterate the tool's own fields.
        if not isinstance(exported, (list, tuple)):
            msg = (
                f"{where}: {EXPORT} must be a list or tuple of tools, "
                f"got {type(exported).__name__} -- write {EXPORT} = [my_tool]"
            )
            raise ToolError(msg)

        for tool in exported:
            name = tool_name(tool)
            if name in claimed:
                # `tools_by_name` is a dict, so the later one would take the
                # name in silence and the earlier tool would simply never run.
                msg = f"{where}: tool {name!r} is already defined by {claimed[name]}"
                raise ToolError(msg)
            claimed[name] = str(where)
            tools.append(tool)

    return tuple(tools)


def names(directory: Path) -> tuple[str, ...]:
    """Tool names the directory offers. A listing, for `--list` and errors."""
    return tuple(tool_name(t) for t in load_tools(directory))


def sources(directory: Path) -> dict[str, str]:
    """Where each tool is defined, by name, relative to the catalogue.

    For `--list`. Nesting exists so a person can find a file again, and a bare
    list of names is exactly what sends them grepping for it.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return {}

    found: dict[str, str] = {}
    for path in _modules_in(directory):
        # A package keeps its trailing slash, so a reader can tell "the folder
        # csv_profile" from "a file that would have been csv_profile.py".
        where = str(path.relative_to(directory)) + ("/" if path.is_dir() else "")
        module = _import(path)
        for tool in getattr(module, EXPORT, None) or ():
            found[tool_name(tool)] = where
    return found
