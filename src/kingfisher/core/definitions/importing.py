"""Importing the definitions folder's Python, without putting it on the import path."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

__all__ = [
    "PACKAGE_MARKER",
    "Export",
    "LoadError",
    "exported_from",
    "load",
    "modules_in",
    "skipped",
]

#: The package marker. A subfolder holding one is a unit rather than a pile:
#: it states its exports once, in there, and nothing inside it is scanned.
PACKAGE_MARKER = "__init__.py"

#: The namespace every definition module is imported under, only to make its name unique.
#: No parent package is registered: a package resolves relative imports against itself.
_NAMESPACE = "kingfisher_workspace"


def defined_in_a_workspace(module: str) -> bool:
    """Whether a class with this `__module__` was written in a definition file.

    Such a name is unique and meaningless, so a listing must not print it as where the
    code is; a class imported into a definition module keeps its own package.
    """
    return module == _NAMESPACE or module.startswith(f"{_NAMESPACE}.")


class LoadError(ValueError):
    """A definition module could not be loaded, or should not be."""


def skipped(name: str) -> bool:
    """Directories a walk must not descend into, nor a file be read from."""
    return name.startswith(".") or name == "__pycache__"


def modules_in(directory: Path) -> list[Path]:
    """Every module a directory contributes, walking into folders.

    A folder holding `__init__.py` is one package, imported whole; the walk stops there,
    or it would scan the package's helpers as modules of their own.
    """
    found: list[Path] = []
    for entry in sorted(directory.iterdir()):
        if skipped(entry.name):
            continue
        if entry.is_dir():
            if (entry / PACKAGE_MARKER).is_file():
                found.append(entry)
            else:
                found.extend(modules_in(entry))
        elif entry.suffix == ".py" and not entry.name.startswith("_"):
            found.append(entry)
    return found


def _module_name(path: Path) -> str:
    """A name no other definition file can collide with."""
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
        # No bytecode: it would land in the catalogue, beside what a person authored and
        # keeps under version control.
        written = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec.loader.exec_module(module)
        finally:
            sys.dont_write_bytecode = written
    except Exception as exc:
        del sys.modules[module_name]
        # Named by file: the reader just added one, and is not debugging kingfisher.
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


@dataclass(frozen=True)
class Export:
    """The name a kind's modules declare, and enough to say so to whoever forgot it."""

    #: `TOOLS`, `SUBAGENTS`, `MIDDLEWARES`.
    name: str
    #: The kind's own error, so a refusal reads as that kind's and `doctor` files it.
    error: type[ValueError]
    #: What the list holds, as a reader would say it: "tools", "middleware".
    holding: str
    #: One entry, spelled as an author would write it: `my_tool`, `MyMiddleware`.
    example: str


def exported_from(path: Path, *, where: str, declares: Export) -> Sequence[Any]:
    """The sequence one definition module declares, or a refusal an author can act on."""
    module = load(path, declares=declares.name, error=declares.error)
    exported = getattr(module, declares.name, None)
    if exported is None:
        declared_in = f"{where}{PACKAGE_MARKER}" if path.is_dir() else where
        msg = f"{declared_in}: must define {declares.name}, the {declares.holding} it contributes"
        raise declares.error(msg)
    # A list or tuple, not any iterable: a `BaseTool` and a compiled subagent's `dict` are
    # iterable, so `TOOLS = add` would quietly loop over field names.
    if not isinstance(exported, (list, tuple)):
        msg = (
            f"{where}: {declares.name} must be a list or tuple of {declares.holding}, "
            f"got {type(exported).__name__} -- write {declares.name} = [{declares.example}]"
        )
        raise declares.error(msg)
    return exported
