"""Importing a workspace's own Python, without putting it on the import path."""

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
    """A workspace module could not be loaded, or should not be."""


def skipped(name: str) -> bool:
    """Directories a walk must not descend into, nor a file be read from."""
    return name.startswith(".") or name == "__pycache__"


def modules_in(directory: Path) -> list[Path]:
    """Every module a directory contributes, deepest layout first resolved.

    * a folder holding one is a **package** -- one unit, imported whole, its
      exports declared once in `__init__.py`. The walk stops there, because
      descending would scan the helper modules it exists to hold as though each
      were a module of its own.
    * anything else is **organisation** -- files are independent, nested as deep
      as you like, and each declares its own exports exactly as a flat one
      always did.
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
    """A name no other workspace file can collide with."""
    return f"{_NAMESPACE}.{path.stem}_{abs(hash(str(path)))}"


def load(path: Path, *, declares: str, error: type[ValueError] = LoadError) -> Any:
    """Import one file, or one package, without putting it on the import path."""
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
        # Importing writes `__pycache__` beside the source, which here means inside the
        # catalogue -- a directory holding what a person authored, and the one an
        # operator is most likely to keep under version control. Bytecode there is noise
        # in `git status` at best and something committed at worst.
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
    """Turn a leaked internal module name into the thing to actually do."""
    if not isinstance(exc, ModuleNotFoundError) or _NAMESPACE not in str(exc.name or ""):
        return None
    return (
        f"{path.name}: a relative import needs a package, and this file is loaded on "
        f"its own. Add {PACKAGE_MARKER} to {path.parent.name}/ and declare {declares} "
        f"there -- then its modules import from each other normally."
    )
