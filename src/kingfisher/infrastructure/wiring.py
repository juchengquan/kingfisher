"""The object a deployment named in a setting, imported and built."""

from __future__ import annotations

from importlib import import_module
from typing import Any


def _wanted(port: type) -> str:
    """The method names a protocol asks for, for a message that says what is
    missing rather than only that something is."""
    return ", ".join(sorted(name for name in dir(port) if not name.startswith("_")))


def store_named(spec: str, *, setting: str, port: type) -> Any:
    """Import `spec`, call it, and check the result is a `port`."""
    # Imported here rather than at module scope: `config` is the package root's
    # own module and this one sits under `infrastructure/`, so a top-level
    # import would run on any `infrastructure` import for a name used on one
    # branch of one function.
    from kingfisher.config import ConfigError  # noqa: PLC0415

    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name or not attribute:
        msg = (
            f"{setting} is {spec!r}, which does not name anything. Write it as "
            "'module:name' -- the import path of a module, a colon, and something in "
            "it callable with no arguments"
        )
        raise ConfigError(msg)
    try:
        module = import_module(module_name)
    except ImportError as exc:
        msg = (
            f"{setting} names module {module_name!r}, which cannot be imported "
            f"({exc}). It has to be importable by this process, so an installed "
            "package or something already on the path"
        )
        raise ConfigError(msg) from exc
    try:
        factory = getattr(module, attribute)
    except AttributeError as exc:
        msg = (
            f"{setting} names {attribute!r} in {module_name!r}, which does not "
            "define it"
        )
        raise ConfigError(msg) from exc

    built = factory()
    if not isinstance(built, port):
        msg = (
            f"{setting} names {spec!r}, which returned {type(built).__name__} -- not "
            f"a {port.__name__}. It has to answer to {_wanted(port)}"
        )
        raise ConfigError(msg)
    return built
