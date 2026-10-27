"""Run events kingfisher ships beside the default, for a deployment to name."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from kingfisher.core.interfaces import RunEvents


class JsonlRunEvents(RunEvents):
    """`RunEvents` appended to a file, one JSON line per event: `kingfisher run --log`.

    Opened per event rather than held, so a turn that crashes leaves every line it got to.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: Mapping[str, object]) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
