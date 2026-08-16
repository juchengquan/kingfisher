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
        # Off, because `access` already logs every request -- and because
        # uvicorn's writes the *concrete* path. Measured against a live server:
        # `GET /sessions/5df2db83…` on the line above our own
        # `GET /sessions/{session_id}`, which is the credential this deployment
        # took care not to write, written anyway by the thing that serves it.
        access_log=False,
    )


def main() -> int:
    """The console entry point. Returns an exit code rather than calling `exit`."""
    # WARNING at the root, INFO for the two that have something to say. Setting
    # the root to INFO turns on every library at once -- httpx then logs a line
    # per outbound model call, which is noise in a server and the sort of
    # default that later logs something nobody meant to keep.
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("kingfisher.server").setLevel(logging.INFO)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    settings = ServerConfig.from_env()
    try:
        serve(settings)
    except ImportError:
        print(MISSING, file=sys.stderr)  # noqa: T201 -- this is a CLI
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
