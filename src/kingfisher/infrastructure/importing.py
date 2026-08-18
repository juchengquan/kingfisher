"""Importing a workspace's own Python, without putting it on the import path.

Moved out of `tool_store`, unchanged, because tools stopped being the only kind
that is code. A workspace may now define a subagent as a compiled graph, and
that arrives the same way a tool does: a file or a folder under a catalogue,
imported into this process and asked what it declares.

Nothing here knows what it is loading. Which name a module must export, and what
to do with what it exports, is the caller's -- this answers only "which files
contribute a module" and "import that one, safely, and say something useful when
it will not".

Kept in flat `infrastructure/` rather than beside either caller, and it imports
nothing foreign: what it adapts is the filesystem and Python's own loader, which
is the same reason `workspace_fs` lives here.
"""

from __future__ import annotations

import importlib.util
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["PACKAGE_MARKER", "LoadError", "load", "modules_in", "skipped"]

#: The package marker. A subfolder holding one is a unit rather than a pile:
#: it states its exports once, in there, and nothing inside it is scanned.
PACKAGE_MARKER = "__init__.py"

#: The namespace every workspace module is imported under. Never registered and
#: never on `sys.path`: it exists to make a module name unique, nothing more. A
#: package resolves its own relative imports against *itself*, so no parent has
#: to exist -- measured, after writing one that turned out to do nothing.
_NAMESPACE = "kingfisher_workspace"


class LoadError(ValueError):
    """A workspace module could not be loaded, or should not be.

    Subclassed per kind, so a caller can keep catching the error its own
    catalogue raises -- `ToolError` is the first, and reads exactly as it did
    when this code lived next to it.
    """


def skipped(name: str) -> bool:
    """Directories a walk must not descend into, nor a file be read from.

    Needed because the exposure is: a one-level scan could never reach a
    virtualenv or a build directory left under a catalogue; a recursive one can,
    and this module *imports what it finds*. `__pycache__` is the one that turns
    up by accident -- importing a workspace module once leaves it behind.
    """
    return name.startswith(".") or name == "__pycache__"


def modules_in(directory: Path) -> list[Path]:
    """Every module a directory contributes, deepest layout first resolved.

    Two shapes, and `PACKAGE_MARKER` is the switch between them:

    * a folder holding one is a **package** -- one unit, imported whole, its
      exports declared once in `__init__.py`. The walk stops there, because
      descending would scan the helper modules it exists to hold as though each
      were a module of its own.
    * anything else is **organisation** -- files are independent, nested as deep
      as you like, and each declares its own exports exactly as a flat one
      always did.

    Files whose names begin with `_` are helpers and are never modules of the
    catalogue: that is how a loose file keeps something private without needing
    a folder for it.

    Sorted so two workspaces holding the same files build the same agent.
    """
    found: list[Path] = []
    for entry in sorted(directory.iterdir()):
        if skipped(entry.name):
            continue
        if entry.is_dir():
            if (entry / PACKAGE_MARKER).is_file():
                found.append(entry)  # a package: one module, not a directory to walk
            else:
                found.extend(modules_in(entry))
        elif entry.suffix == ".py" and not entry.name.startswith("_"):
            found.append(entry)
    return found


def _module_name(path: Path) -> str:
    """A name no other workspace file can collide with.

    Keyed on the full path rather than the stem: two workspaces with a
    `maths.py` each must not share an entry in `sys.modules`, and neither should
    a workspace file and a real installed package. It is also what keeps a
    `tools/analysis/` and a `subagents/analysis/` apart.
    """
    return f"{_NAMESPACE}.{path.stem}_{abs(hash(str(path)))}"


def load(path: Path, *, declares: str, error: type[ValueError] = LoadError) -> Any:
    """Import one file, or one package, without putting it on the import path.

    A directory is imported as a package: the spec is built from its
    `PACKAGE_MARKER` and told the directory is where its submodules live, which
    is the whole of what makes `from .client import resolve` resolve. A module
    that grew helpers is the reason to write a folder at all, so the folder has
    to import the way Python says a folder imports.

    `declares` is the export name the caller will look for, used only to say
    what to write when a loose file tries a relative import. `error` is the
    caller's own class, so the message a reader sees names their catalogue
    rather than this shared loader.

    `type[ValueError]` rather than `type[LoadError]`, which was the first
    spelling and cannot hold: `SubagentError` belongs to the domain, and a
    domain type subclassing one from `infrastructure/` would point the
    dependency the wrong way for the sake of a signature.
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
        raise error(msg)

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        # Importing writes `__pycache__` beside the source, which here means
        # inside the catalogue -- a directory holding what a person authored, and
        # the one an operator is most likely to keep under version control.
        # Bytecode there is noise in `git status` at best and something committed
        # at worst.
        #
        # Suppressed rather than deleted afterwards, so nothing is created to
        # clean up. Global for the length of one `exec_module` and restored
        # either way: a concurrent import elsewhere might skip its own cache
        # once, which costs a recompile and nothing else. The alternative,
        # `sys.pycache_prefix`, redirects every module in the process rather
        # than these few.
        written = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec.loader.exec_module(module)
        finally:
            sys.dont_write_bytecode = written
    except Exception as exc:
        del sys.modules[module_name]
        # The file name matters more than the traceback here: the reader is
        # someone who just added a file, not someone debugging kingfisher.
        advice = _relative_import_advice(path, exc, declares=declares)
        msg = advice or f"{path.name}: {type(exc).__name__}: {exc}"
        raise error(msg) from exc
    return module


def _relative_import_advice(path: Path, exc: Exception, *, declares: str) -> str | None:
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
        f"its own. Add {PACKAGE_MARKER} to {path.parent.name}/ and declare {declares} "
        f"there -- then its modules import from each other normally."
    )
