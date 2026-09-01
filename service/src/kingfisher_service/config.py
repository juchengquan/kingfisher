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
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

PREFIX = "KINGFISHER_SERVICE_"

#: What these were called while this package was `kingfisher.presentation`, and
#: still read. Renaming an environment variable is the one rename that fails in
#: silence: an import that moved stops the program and says so, while a variable
#: nobody reads falls back to its default and the server comes up on the wrong
#: port with nothing to show for it. So both are read, the new one wins, and
#: using the old one says so once.
WAS = "KINGFISHER_SERVER_"


def _reader(source: Mapping[str, str]) -> Callable[[str, Any], Any]:
    """One setting, under the current name or the one it used to have.

    The new name wins where both are set, because a deployment mid-migration has
    the new one for a reason. The old one is honoured and reported: honoured so
    nothing breaks on upgrade, reported so this does not become a second name
    nobody knows is load-bearing.

    A warning rather than a log line: this is read before any logging is
    configured, and the one caller is a process starting up.
    """

    def read(suffix: str, fallback: Any) -> Any:
        if (value := source.get(f"{PREFIX}{suffix}")) is not None:
            return value
        if (value := source.get(f"{WAS}{suffix}")) is not None:
            warnings.warn(
                f"{WAS}{suffix} is the old name for {PREFIX}{suffix} and is still "
                f"read; rename it, since the old one will stop being read.",
                DeprecationWarning,
                stacklevel=2,
            )
            return value
        return fallback

    return read


@dataclass(frozen=True)
class ServiceConfig:
    """How to serve, as opposed to what to serve."""

    #: Loopback by default, and that is a decision rather than a placeholder.
    #: This server authenticates nobody -- authentication and per-caller quotas
    #: belong to whatever sits in front of it -- so a default of `0.0.0.0` would
    #: publish an unauthenticated API to the network the moment someone ran it.
    #: Binding wider is a thing to opt into once something is in front.
    #:
    #: It does now *ask* who is calling, which is a smaller claim than it
    #: sounds and does not soften this one. A deployment with an access policy
    #: supplies a `groups_from` to `create_app`, and the shipped reader takes
    #: those groups off a header -- which is trustworthy exactly insofar as
    #: whatever sets it also strips it from inbound requests. Bound to
    #: `0.0.0.0` with nothing in front, that header is a request field any
    #: caller can write, so identity would be self-asserted and the policy
    #: would decide nothing. The reason for loopback is unchanged; there is
    #: simply more riding on it.
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
        """Read `KINGFISHER_SERVICE_*`, falling back to the defaults above.

        A prefix of its own rather than sharing `KINGFISHER_`, so that reading
        a deployment's environment tells you which half of the split each
        setting belongs to without consulting anything.

        `KINGFISHER_SERVER_*` still works and warns -- see `WAS`.
        """
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
            audit_content=str(read("AUDIT_CONTENT", "")).lower() == "true",
        )
