"""Reading a `middleware/` directory into the classes it contributes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from types import MappingProxyType
from typing import Any

from kingfisher.infrastructure.importing import (
    PACKAGE_MARKER,
    LoadError,
    load,
    modules_in,
)

#: Declared rather than inferred, like `TOOLS`: searching for subclasses would
#: offer an imported one -- `from .base import CallCap` -- as a second entry.
EXPORT = "MIDDLEWARE"


class MiddlewareError(LoadError):
    """A `middleware/` file that cannot be read, or does not declare itself."""


def name_of(entry: type) -> str:
    """What a definition writes to select this class.

    `AgentMiddleware.name` is a *property*, so on the class it is the property
    object rather than a string -- checked, or the selector would stringify into
    `<property object at 0x...>`. An explicit `name` on the class wins.
    """
    declared = getattr(entry, "name", None)
    return declared if isinstance(declared, str) else entry.__name__


@dataclass(frozen=True)
class Registered:
    """One middleware class and the file it came from, inside the catalogue."""

    middleware: type
    source: str

    @property
    def name(self) -> str:
        return name_of(self.middleware)


@dataclass(frozen=True)
class LocalMiddlewareRepository:
    """The middleware defined in one directory, imported into this process.

    Read once, like `LocalToolRepository` and for its reason: these modules are
    *executed* to be read, so two walks would run every side effect twice.
    """

    root: Path

    @cached_property
    def found(self) -> tuple[Registered, ...]:
        """Every middleware this directory defines, with its origin.

        A duplicate name is refused rather than qualified: `middleware:` is a
        flat selector, so there is nowhere to put a `where::what` and nothing to
        fall back on.
        """
        directory = Path(self.root)
        if not directory.is_dir():
            return ()

        found: list[Registered] = []
        claimed: dict[str, str] = {}
        for path in modules_in(directory):
            where = str(path.relative_to(directory)) + ("/" if path.is_dir() else "")
            module = load(path, declares=EXPORT, error=MiddlewareError)
            exported = getattr(module, EXPORT, None)
            if exported is None:
                declared_in = f"{where}{PACKAGE_MARKER}" if path.is_dir() else where
                msg = f"{declared_in}: must define {EXPORT}, the middleware it contributes"
                raise MiddlewareError(msg)
            if not isinstance(exported, (list, tuple)):
                msg = (
                    f"{where}: {EXPORT} must be a list or tuple of middleware classes, "
                    f"not {type(exported).__name__}"
                )
                raise MiddlewareError(msg)
            for entry in exported:
                _refuse_unless_buildable(entry, where=where)
                name = name_of(entry)
                if (earlier := claimed.get(name)) is not None:
                    msg = (
                        f"{where}: two files define middleware {name!r} -- {earlier} "
                        f"and this one. A definition names one and there is nothing "
                        f"to tell them apart, so rename one of the classes or give "
                        f"one an explicit `name`"
                    )
                    raise MiddlewareError(msg)
                claimed[name] = where
                found.append(Registered(middleware=entry, source=where))
        return tuple(found)

    @cached_property
    def classes(self) -> dict[str, type]:
        """`name -> class`, which is the shape a registry already has."""
        return {entry.name: entry.middleware for entry in self.found}

    @property
    def names(self) -> tuple[str, ...]:
        """`AssetRepository`'s one member, so this reads like its siblings."""
        return tuple(entry.name for entry in self.found)


@dataclass(frozen=True)
class NoMiddleware:
    """A deployment that has not offered any, said rather than defaulted to.

    So that adding a fifth kind is not a breaking change for every caller that
    spelled the other four out -- the reason `from_config` reads its roots with
    `.get`. A repository over a path that does not exist would do the same while
    claiming to have looked somewhere.
    """

    @property
    def classes(self) -> Mapping[str, type]:
        return MappingProxyType({})

    @property
    def names(self) -> tuple[str, ...]:
        return ()


def _refuse_unless_buildable(entry: Any, *, where: str) -> None:
    """A class, and one deepagents will run.

    Stricter than the registry's refusal, which allows a zero-argument factory.
    A file is imported once, so an object here would be built once and shared by
    every graph -- the state leak that makes a cap stop capping.
    """
    # Deferred, the trade `models.py` makes by naming its chat classes as
    # strings: at module scope this made nine light exports heavy at once.
    from langchain.agents.middleware import AgentMiddleware  # noqa: PLC0415

    if not isinstance(entry, type):
        built = "a built" if isinstance(entry, AgentMiddleware) else "a"
        msg = (
            f"{where}: {EXPORT} holds {built} {type(entry).__name__}, and this format "
            f"takes classes. A middleware is built again for every graph, so a file "
            f"that hands over an object would share one across all of them"
        )
        raise MiddlewareError(msg)
    if not issubclass(entry, AgentMiddleware):
        msg = (
            f"{where}: {EXPORT} holds {entry.__name__}, which is not an "
            f"`AgentMiddleware`. That is the class deepagents wraps an agent with; "
            f"anything else is refused here rather than at the first turn"
        )
        raise MiddlewareError(msg)
