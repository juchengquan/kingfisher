"""Reading a finished run's artifacts.

The smoke asks for `result.json` in its own task text, so this knows the name.
Nothing in the package does.
"""

from __future__ import annotations

import json
from pathlib import Path


def load_result(run_dir: Path) -> dict | None:
    path = Path(run_dir) / "result.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
