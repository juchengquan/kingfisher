"""Reading a finished run's artifacts.

The smoke asks for `report.md` and `result.json` in its own task text, so these
know the names. Nothing in the package does.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path


def load_result(run_dir: Path) -> dict | None:
    path = Path(run_dir) / "result.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def promote_report(run_dir: Path, workspace: Path, name: str = "smoke") -> Path | None:
    """Copy a run's report somewhere stable, so it can be read run-over-run.

    `/derived` because it survives sweeps, not because it is a report -- the
    workspace has no notion of one. The pass/fail signal is `check_result`,
    not this copy.
    """
    source = Path(run_dir) / "report.md"
    if not source.exists():
        return None
    destination = Path(workspace) / "derived" / f"{name}.md"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return destination
