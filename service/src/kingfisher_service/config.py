"""What the server needs, kept out of what the library needs."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kingfisher import ConfigError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

PREFIX = "KINGFISHER_SERVICE_"

def _reader(source: Mapping[str, str]) -> Callable[[str, Any], Any]:
    """One setting, under the only prefix that names it.

    `KINGFISHER_SERVER_` was read here alongside the current prefix for a
    deprecation and is not any more, so a suffix has one spelling and this looks
    it up. `kingfisher doctor` reports a deployment still carrying the old one --
    which is the whole of what is left of the fallback, because a variable that
    stops being read is otherwise silent: the server simply comes up on the
    default port with nothing to show for it.
    """

    def read(suffix: str, fallback: Any) -> Any:
        value = source.get(f"{PREFIX}{suffix}")
        return fallback if value is None else value

    return read


@dataclass(frozen=True)
class ServiceConfig:
    """How to serve, as opposed to what to serve."""

    #: Loopback by default, and that is a decision rather than a placeholder. This
    #: server authenticates nobody -- authentication and per-caller quotas belong to
    #: whatever sits in front of it -- so a default of `0.0.0.0` would publish an
    #: unauthenticated API to the network the moment someone ran it. Binding wider is a
    #: thing to opt into once something is in front.
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
    heartbeat_s: float = 15.0
    #: Where `input_refs` and `data_refs` are fetched from, or nowhere.
    #:
    #: Unset by default, and a request naming files by id is then a 500 saying no store
    #: is wired -- which is the honest answer, because it is the deployment that has not
    #: decided where files come from. Set it and the default app serves the shipped
    #: local store.
    file_store_dir: Path | None = None
    #: The same port, named rather than built: `module:name` for something callable with
    #: no arguments that returns a `FileStore`.
    file_store_factory: str | None = None
    #: Whether the audit log carries the task and the answer, or only what
    #: happened. Off, because what may be kept and for how long is a question
    #: about a deployment's obligations rather than about kingfisher -- so it is
    #: a switch somebody sets, not a judgement made here.
    #:
    #: Either way the audit logger has no handler until one is attached, so
    #: nothing is written by default at all.
    audit_content: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ServiceConfig:
        """Read `KINGFISHER_SERVICE_*`, falling back to the defaults above."""
        source = os.environ if env is None else env
        defaults = cls()
        read = _reader(source)
        return cls(
            host=read("HOST", defaults.host),
            port=int(read("PORT", defaults.port)),
            max_body_bytes=int(read("MAX_BODY_BYTES", defaults.max_body_bytes)),
            heartbeat_s=float(read("HEARTBEAT_S", defaults.heartbeat_s)),
            file_store_dir=(
                Path(where) if (where := read("FILE_STORE_DIR", None)) else None
            ),
            file_store_factory=(named.strip() or None)
            if (named := read("FILE_STORE_FACTORY", None))
            else None,
            audit_content=str(read("AUDIT_CONTENT", "")).lower() == "true",
        )

    def __post_init__(self) -> None:
        """Refuse a deployment that named its file store twice."""
        if self.file_store_dir is not None and self.file_store_factory is not None:
            msg = (
                f"the file store is configured twice: {PREFIX}FILE_STORE_DIR names "
                f"{str(self.file_store_dir)!r} and {PREFIX}FILE_STORE_FACTORY names "
                f"{self.file_store_factory!r}. Set one -- the factory for a store that "
                "is not a directory on this host, the directory for one that is"
            )
            raise ConfigError(msg)
