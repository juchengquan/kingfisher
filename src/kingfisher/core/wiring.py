"""The object a deployment named in a setting, imported and built."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from .config import ConfigError


def built_from_setting(spec: str, *, setting: str, interface: type) -> Any:
    """Import `spec`, call it, and check the result is an `interface`."""
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
    # Not `refuse_unless_subclass`: `interface` may be a `Mapping`, which nobody subclasses.
    if not isinstance(built, interface):
        msg = (
            f"{setting} names {spec!r}, which returned {type(built).__name__} -- not "
            f"a {interface.__name__}"
        )
        raise ConfigError(msg)
    return built
