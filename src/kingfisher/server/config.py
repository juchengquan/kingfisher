"""What the server needs, kept out of what the library needs.

`Config` is the library's — workspace, models, timeouts, retention. A bind
address is none of its business, and putting one there is where the split
between the two would blur first: `Config` is passed to `Kingfisher`, and a
field on it reads as something a turn might consult.

So these live apart, read from their own environment prefix, and nothing in
`kingfisher.domain`, `kingfisher.application` or `kingfisher.infrastructure`
can see them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

PREFIX = "KINGFISHER_SERVER_"


@dataclass(frozen=True)
class ServerConfig:
    """How to serve, as opposed to what to serve."""

    #: Loopback by default, and that is a decision rather than a placeholder.
    #: This server does not know who is calling -- authentication, caller
    #: identity and per-caller quotas belong to whatever sits in front of it --
    #: so a default of `0.0.0.0` would publish an unauthenticated API to the
    #: network the moment someone ran it. Binding wider is a thing to opt into
    #: once something is in front.
    host: str = "127.0.0.1"
    port: int = 8000
    #: A ceiling on a request body. `task` is unbounded text and every other
    #: field is small, so this is not a tuning knob -- it is the difference
    #: between a bad request and a process holding a gigabyte of it.
    #:
    #: Read from `Content-Length`, so a chunked body without one is not caught
    #: here. Deliberate: reading a body to measure it is the cost this avoids.
    max_body_bytes: int = 1 << 20
    #: How often a quiet stream sends an SSE comment.
    #:
    #: Two jobs, and the second is the one that matters. Proxies drop idle
    #: connections -- that is the obvious one. But a disconnect is only noticed
    #: when the server next tries to send, so this is also what bounds how long
    #: a hung-up client keeps paying for model calls during a quiet tool call.
    #: Fifteen seconds is well inside the usual sixty-second proxy idle timeout
    #: and coarse enough to be invisible next to a turn.
    heartbeat_s: float = 15.0
    #: Where `input_refs` and `data_refs` are fetched from, or nowhere.
    #:
    #: Unset by default, and a request naming files by id is then a 500 saying
    #: no store is wired -- which is the honest answer, because it is the
    #: deployment that has not decided where files come from. Set it and the
    #: default app serves the shipped local store; wire something else by
    #: building the `Kingfisher` yourself and handing it to `create_app`.
    file_store_dir: Path | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ServerConfig:
        """Read `KINGFISHER_SERVER_*`, falling back to the defaults above.

        A prefix of its own rather than sharing `KINGFISHER_`, so that reading
        a deployment's environment tells you which half of the split each
        setting belongs to without consulting anything.
        """
        source = os.environ if env is None else env
        defaults = cls()
        return cls(
            host=source.get(f"{PREFIX}HOST", defaults.host),
            port=int(source.get(f"{PREFIX}PORT", defaults.port)),
            max_body_bytes=int(
                source.get(f"{PREFIX}MAX_BODY_BYTES", defaults.max_body_bytes)
            ),
            heartbeat_s=float(source.get(f"{PREFIX}HEARTBEAT_S", defaults.heartbeat_s)),
            file_store_dir=(
                Path(where) if (where := source.get(f"{PREFIX}FILE_STORE_DIR")) else None
            ),
        )
