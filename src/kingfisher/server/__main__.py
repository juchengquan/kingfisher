"""`kingfisher-server`, and `python -m kingfisher.server`.

Nothing here decides anything. It reads `ServerConfig` from the environment,
builds the app the factory builds, and hands both to uvicorn -- so running the
server and importing it give the same object, and there is no second way to
configure one.
"""

from __future__ import annotations

import logging
import sys

from kingfisher.server.app import create_app
from kingfisher.server.config import ServerConfig

MISSING = (
    "kingfisher-server needs the server extra: pip install 'kingfisher[server]'"
)


def serve(settings: ServerConfig) -> None:
    """Run the app until stopped.

    Imported inside the function so `kingfisher.server` stays importable without
    uvicorn -- an app served by something else (gunicorn, a hosted ASGI runner)
    needs the module, not this.
    """
    import uvicorn  # noqa: PLC0415

    # `uvicorn.run`, not `Kingfisher.run` -- the architecture rule names this
    # receiver explicitly rather than being loosened to allow both.
    uvicorn.run(
        create_app(),
        host=settings.host,
        port=settings.port,
    )


def main() -> int:
    """The console entry point. Returns an exit code rather than calling `exit`."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    settings = ServerConfig.from_env()
    try:
        serve(settings)
    except ImportError:
        print(MISSING, file=sys.stderr)  # noqa: T201 -- this is a CLI
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
