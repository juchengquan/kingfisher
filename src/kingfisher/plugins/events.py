"""Run events kingfisher ships beside the default, for a deployment to name."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path


class JsonlRunEvents:
    """`RunEvents` appended to a file, one JSON line per event: `kingfisher run --log`.

    Opened and closed for each event rather than held. A turn that crashes leaves
    every line it got to, and that is the turn a log is most wanted for.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: Mapping[str, object]) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
