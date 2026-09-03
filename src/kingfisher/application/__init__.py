"""The application layer: what a turn does, in order.

Read the environment, prepare a session, build the graph for this request, run
it, record what happened. It orchestrates and decides nothing about the harness
— it speaks `Request`, `RunEvent` and `RunResult`, never `AIMessage`, and
reaches deepagents only through `infrastructure/`.

`run.py` and `runlog.py` each once carried their own copy of LangChain's
usage-metadata shape, kept in sync by nobody. That is the failure the rule
exists to prevent, and `tests/test_architecture.py` enforces it.

Names are re-exported here as well as from the package root, so a caller that
wants to say where something lives can. `from kingfisher.application import
Kingfisher` and `from kingfisher import Kingfisher` are the same object; the
root is the documented surface and this is the layer-local one.

Lazily, for the reason the root's own table is lazy and which matters more here:
`service` imports deepagents, which imports three provider SDKs at module level
and costs about 950ms. A plain `from .service import Kingfisher` on this line
would run before *any* submodule of this package, so reading a config through
`application.config` -- 39ms on its own -- would pay all of it. Measured both
ways before choosing.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

#: The same pairs the root's `_EXPORTS` carries for this layer, and
#: `test_the_layer_and_the_root_agree_about_this_layer` is what stops the two
#: drifting. A second table is the cost of this file existing; a test that the
#: two agree is what makes the cost bounded rather than a matter of memory.
_EXPORTS = {
    "Inventory": "kingfisher.application.inventory",
    "Kingfisher": "kingfisher.application.service",
    "Origin": "kingfisher.application.origins",
    "Origins": "kingfisher.application.origins",
    "config_from_env": "kingfisher.application.config",
    "inventory": "kingfisher.application.inventory",
    "paths_from_env": "kingfisher.application.config",
    "run": "kingfisher.application.run",
    "stream": "kingfisher.application.run",
}

__all__ = [
    "Inventory",
    "Kingfisher",
    "Origin",
    "Origins",
    "config_from_env",
    "inventory",
    "paths_from_env",
    "run",
    "stream",
]

if TYPE_CHECKING:
    # So type checkers and IDEs see the real symbols rather than `Any`, which is
    # the arrangement the root uses and for the same reason.
    from kingfisher.application.config import config_from_env as config_from_env
    from kingfisher.application.config import paths_from_env as paths_from_env
    from kingfisher.application.inventory import Inventory as Inventory
    from kingfisher.application.inventory import inventory as inventory
    from kingfisher.application.origins import Origin as Origin
    from kingfisher.application.origins import Origins as Origins
    from kingfisher.application.run import run as run
    from kingfisher.application.run import stream as stream
    from kingfisher.application.service import Kingfisher as Kingfisher


def __getattr__(name: str) -> Any:
    """PEP 562 lazy re-export."""
    try:
        module = _EXPORTS[name]
    except KeyError:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg) from None

    value = getattr(import_module(module), name)
    globals()[name] = value  # resolve once; subsequent lookups skip __getattr__
    return value


def __dir__() -> list[str]:
    return __all__

