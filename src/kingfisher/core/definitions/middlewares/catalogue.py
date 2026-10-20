"""Reading a `middlewares/` directory into the classes it contributes."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

from kingfisher.core.definitions.importing import (
    Export,
    LoadError,
    exported_from,
    modules_in,
)

#: Declared rather than inferred, like `TOOLS`: searching for subclasses would
#: offer an imported one -- `from .base import CallCap` -- as a second entry.
EXPORT = "MIDDLEWARES"


class MiddlewareError(LoadError):
    """A `middlewares/` file that cannot be read, or does not declare itself."""


DECLARES = Export(EXPORT, error=MiddlewareError, holding="middleware", example="MyMiddleware")


def name_of(entry: type) -> str:
    """What a definition writes to select this class: an explicit `name`, or the class's.

    On the class, `AgentMiddleware.name` is a property object, hence the `str` check.
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

    Read once: the modules are executed to be read, so a second walk reruns their effects.
    """

    root: Path

    @cached_property
    def found(self) -> tuple[Registered, ...]:
        """Every middleware this directory defines, with its origin.

        A duplicate name is refused, not qualified: `middlewares:` takes no `where::what`.
        """
        directory = Path(self.root)
        if not directory.is_dir():
            return ()

        found: list[Registered] = []
        claimed: dict[str, str] = {}
        for path in modules_in(directory):
            where = str(path.relative_to(directory)) + ("/" if path.is_dir() else "")
            for entry in exported_from(path, where=where, declares=DECLARES):
                _refuse_unless_buildable(entry, where=where)
                name = name_of(entry)
                if claimed.get(name) == where:
                    msg = f"{where}: middleware {name!r} is defined twice in this file"
                    raise MiddlewareError(msg)
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


def _refuse_unless_buildable(entry: Any, *, where: str) -> None:
    """A class, and one deepagents will run.

    Stricter than the registry, which allows a factory: a file is imported once, so an
    object here would be shared by every graph, and a shared cap stops capping.
    """
    # Deferred: at module scope it would load the agent runtime for the light exports.
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
