"""`LoguruRunEvents`: every event into loguru, for an app that logs through it."""

from __future__ import annotations

from collections.abc import Mapping

from kingfisher.core.interfaces import RunEvents

try:
    from loguru import logger
except ModuleNotFoundError as missing:
    msg = "LoguruRunEvents needs loguru, which is an extra: pip install 'kingfisher[loguru]'"
    raise ModuleNotFoundError(msg, name=missing.name) from missing


class LoguruRunEvents(RunEvents):
    """Each event as a loguru record at its `level`, saying its `message`, with the whole
    event as `record["extra"]["kingfisher"]`."""

    def record(self, event: Mapping[str, object]) -> None:
        # At no depth: named after this module, a record stays under
        # `logger.disable("kingfisher")` through a sink of the app's that wraps this one.
        # Bound, not a keyword: loguru formats a message with its keywords, `{` and all.
        logger.bind(kingfisher=dict(event)).log(str(event["level"]), str(event["message"]))
