"""The object a deployment named in a setting, imported and built.

`KINGFISHER_SESSION_STORE_FACTORY` and `KINGFISHER_SERVICE_FILE_STORE_FACTORY` are
the same idea twice: `module:name` for something callable with no arguments that
returns an adapter kingfisher has never imported. This is that idea, once.

**What is checked here is the name, not the building.** A spec that will not parse, a
module that will not import, an attribute that is not there, a result of the wrong
shape -- those are wiring mistakes, and a `ConfigError` naming the setting is what an
operator can act on. A factory that raises *its own* exception is left alone: that is
the deployment's code failing at the deployment's job, its type may be one their own
error handling knows, and this function is already on the traceback saying which
setting reached it. Wrapping it would replace a `NoCredentialsError` with a sentence
about configuration that is not what went wrong.
"""

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
