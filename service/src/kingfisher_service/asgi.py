"""An application for a server to point at: `kingfisher_service.asgi:app`."""

from __future__ import annotations

from kingfisher_service.app import create_app

app = create_app()
